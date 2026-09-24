// Package tlsfile serves TLS from a certificate/key file pair (D-033): the
// front terminates TLS from a certificate a caller already has, reloading a
// renewal without a restart. There is no ACME client and no self-signed
// generation here -- a site with nginx, Caddy or Tailscale in front leaves
// this package unused and sets url: instead.
package tlsfile

import (
	"crypto/tls"
	"fmt"
	"log"
	"os"
	"os/signal"
	"sync"
	"sync/atomic"
	"syscall"
	"time"
)

// MinVersion is the floor the front negotiates at (D-033).
const MinVersion = tls.VersionTLS12

// DefaultPollInterval is how often a Reloader re-stats its files, absent a
// SIGHUP. The spec allows "at most every 10s"; this is that bound.
const DefaultPollInterval = 10 * time.Second

// Reloader serves a certificate pair loaded from disk and kept current: it
// re-stats both files at most every poll interval and on SIGHUP, reloading
// only when either file's mtime or size has moved since the last good load.
// A bad or half-written renewal is refused -- the last good pair keeps
// serving, and the failure is logged -- so a reload never takes the front's
// TLS listener down.
//
// A zero Reloader is not usable; construct one with NewReloader.
type Reloader struct {
	certFile, keyFile string
	logger            *log.Logger
	pollInterval      time.Duration

	mu                sync.RWMutex
	cert              *tls.Certificate
	certMod, keyMod   time.Time
	certSize, keySize int64

	sighup chan os.Signal
	stop   chan struct{}
	done   chan struct{}
	closed atomic.Bool
}

// Option configures a Reloader at construction.
type Option func(*Reloader)

// WithLogger sends the reloader's diagnostics (every kept-last-good reload
// failure) to l instead of the standard logger.
func WithLogger(l *log.Logger) Option {
	return func(r *Reloader) { r.logger = l }
}

// withPollInterval overrides the default 10s poll interval. Test-only: real
// callers reload on SIGHUP or accept the spec's 10s bound.
func withPollInterval(d time.Duration) Option {
	return func(r *Reloader) { r.pollInterval = d }
}

// NewReloader loads certFile/keyFile once and returns an error if that
// initial load fails -- a caller such as the front's config resolution
// turns that into the D-028 fallback to local-on-loopback. Once started,
// the Reloader re-checks the files in the background; call Close to stop.
func NewReloader(certFile, keyFile string, opts ...Option) (*Reloader, error) {
	return newReloader(certFile, keyFile, DefaultPollInterval, opts...)
}

func newReloader(certFile, keyFile string, pollInterval time.Duration, opts ...Option) (*Reloader, error) {
	r := &Reloader{
		certFile:     certFile,
		keyFile:      keyFile,
		logger:       log.Default(),
		pollInterval: pollInterval,
		stop:         make(chan struct{}),
		done:         make(chan struct{}),
	}
	for _, opt := range opts {
		opt(r)
	}

	if err := r.attempt(true); err != nil {
		return nil, fmt.Errorf("tlsfile: loading %s / %s: %w", certFile, keyFile, err)
	}

	r.sighup = make(chan os.Signal, 1)
	signal.Notify(r.sighup, syscall.SIGHUP)
	go r.run()

	return r, nil
}

// GetCertificate implements the signature tls.Config.GetCertificate wants,
// always answering with the current pair.
func (r *Reloader) GetCertificate(*tls.ClientHelloInfo) (*tls.Certificate, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.cert == nil {
		return nil, fmt.Errorf("tlsfile: no certificate loaded")
	}
	return r.cert, nil
}

// Config returns a *tls.Config wired to this reloader, at the MinVersion
// floor. Callers are free to copy and extend it.
func (r *Reloader) Config() *tls.Config {
	return &tls.Config{
		MinVersion:     MinVersion,
		GetCertificate: r.GetCertificate,
	}
}

// Reload forces an immediate re-check, regardless of the poll interval. It
// returns the error from a failed reload; the last good pair is unaffected
// and keeps serving. The background SIGHUP handler calls this.
func (r *Reloader) Reload() error {
	return r.attempt(true)
}

// Close stops the background poll and signal handling. It does not affect
// the certificate already loaded; GetCertificate keeps answering with it.
func (r *Reloader) Close() {
	if r.closed.CompareAndSwap(false, true) {
		signal.Stop(r.sighup)
		close(r.stop)
		<-r.done
	}
}

func (r *Reloader) run() {
	defer close(r.done)
	ticker := time.NewTicker(r.pollInterval)
	defer ticker.Stop()
	for {
		select {
		case <-r.stop:
			return
		case <-ticker.C:
			r.attempt(false) //nolint:errcheck // logged inside attempt
		case <-r.sighup:
			r.attempt(true) //nolint:errcheck // logged inside attempt
		}
	}
}

// attempt re-stats both files and, if force or either has moved since the
// last good load, reloads the pair. A stat or parse failure is logged and
// returned, and never disturbs an already-loaded certificate.
func (r *Reloader) attempt(force bool) error {
	certStat, err := os.Stat(r.certFile)
	if err != nil {
		r.logf("stat %s failed, keeping last good certificate: %v", r.certFile, err)
		return err
	}
	keyStat, err := os.Stat(r.keyFile)
	if err != nil {
		r.logf("stat %s failed, keeping last good certificate: %v", r.keyFile, err)
		return err
	}

	if !force && r.unchanged(certStat, keyStat) {
		return nil
	}

	cert, err := tls.LoadX509KeyPair(r.certFile, r.keyFile)
	if err != nil {
		r.logf("reload of %s / %s failed, keeping last good certificate: %v", r.certFile, r.keyFile, err)
		return err
	}

	r.mu.Lock()
	r.cert = &cert
	r.certMod, r.certSize = certStat.ModTime(), certStat.Size()
	r.keyMod, r.keySize = keyStat.ModTime(), keyStat.Size()
	r.mu.Unlock()
	return nil
}

func (r *Reloader) unchanged(certStat, keyStat os.FileInfo) bool {
	r.mu.RLock()
	defer r.mu.RUnlock()
	if r.cert == nil {
		return false
	}
	return certStat.ModTime().Equal(r.certMod) && certStat.Size() == r.certSize &&
		keyStat.ModTime().Equal(r.keyMod) && keyStat.Size() == r.keySize
}

func (r *Reloader) logf(format string, args ...any) {
	r.logger.Printf("tlsfile: "+format, args...)
}
