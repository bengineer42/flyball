package front

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Audit is the front's authentication log (auth.md § Audit): one JSON
// record per line, through log/slog's JSON handler, which escapes every
// control character a name could smuggle in (CWE-117, adv-blind 6). The
// file is 0600 in a 0700 directory, set by this code whatever the umask.
// Each record carries the wall time, a sequence number and the boot id.
//
// Authentication events fail closed: a login or a token change whose
// record cannot be written does not happen (Event's error). It is safe for
// concurrent use.
type Audit struct {
	mu   sync.Mutex
	f    *os.File
	h    slog.Handler
	seq  uint64
	boot string
	err  error // not open: every Event returns it

	// An audit that could not be opened is tried again (OpenAuditRetrying)
	// at path, at most every AuditRetry by now; "" never.
	path  string
	now   func() time.Time
	tried time.Time
}

// AuditRetry is how often, at most, an audit that could not be opened is
// tried again (on the next event, or the next look at Failing).
const AuditRetry = 5 * time.Second

// FailedAudit is an audit log that could not be opened and is not tried
// again: the front still serves (D-028), but every Event returns err, so
// the events that fail closed (a sign-in, a token created or revoked)
// answer 503 as when a single write fails.
func FailedAudit(err error) *Audit { return &Audit{err: err} }

// OpenAudit opens (creating) the audit file at path, e.g.
// `<data_dir>/front/audit.jsonl` or
// `$XDG_STATE_HOME/flyball/front-<id>/audit.jsonl`.
func OpenAudit(path string) (*Audit, error) {
	a := &Audit{}
	if err := a.open(path); err != nil {
		return nil, err
	}
	return a, nil
}

// OpenAuditRetrying is OpenAudit for a front, which serves on whatever
// happens (D-028): an audit that cannot be opened is returned with the
// error, fails every Event as FailedAudit does, and is opened again, at
// most every AuditRetry, until it opens -- so fixing the file needs no
// restart. now is its clock (nil: time.Now).
func OpenAuditRetrying(path string, now func() time.Time) (*Audit, error) {
	if now == nil {
		now = time.Now
	}
	a := &Audit{path: path, now: now}
	err := a.open(path)
	if err != nil {
		a.err, a.tried = err, now()
	}
	return a, err
}

// open opens path into a (not yet shared, or under a.mu).
func (a *Audit) open(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return fmt.Errorf("audit: %w", err)
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return fmt.Errorf("audit: %w", err)
	}
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		return fmt.Errorf("audit: %w", err)
	}
	if err := endLine(f, path); err != nil {
		f.Close()
		return fmt.Errorf("audit: %w", err)
	}
	boot := make([]byte, 8)
	rand.Read(boot)
	a.f, a.boot = f, hex.EncodeToString(boot)
	a.h = slog.NewJSONHandler(f, &slog.HandlerOptions{
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if len(groups) == 0 && (a.Key == slog.LevelKey || a.Key == slog.MessageKey) {
				return slog.Attr{}
			}
			return a
		},
	})
	return nil
}

// endLine ends a torn last line (a crash or a full disk mid-append, here
// or in `flyball token create`) with a newline, so the next record starts
// a line of its own: a JSON-lines reader then loses the torn line only,
// not the record after it too.
func endLine(f *os.File, path string) error {
	fi, err := f.Stat()
	if err != nil || fi.Size() == 0 {
		return err
	}
	r, err := os.Open(path)
	if err != nil {
		return err
	}
	defer r.Close()
	last := make([]byte, 1)
	if _, err := r.ReadAt(last, fi.Size()-1); err != nil {
		return err
	}
	if last[0] != '\n' {
		_, err = f.Write([]byte{'\n'})
	}
	return err
}

// failingLocked is why a cannot record now, nil once it can: an audit
// that could not be opened is tried again when AuditRetry has passed.
func (a *Audit) failingLocked() error {
	if a.err == nil || a.path == "" || a.now().Sub(a.tried) < AuditRetry {
		return a.err
	}
	a.tried = a.now()
	if err := a.open(a.path); err != nil {
		a.err = err
		return err
	}
	a.err = nil
	return nil
}

// Failing is why the audit cannot record (it could not be opened), nil if
// it can. It tries the open again as Event does.
func (a *Audit) Failing() error {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.failingLocked()
}

// Event writes one record: event, then attrs. A nil Audit writes nothing.
func (a *Audit) Event(event string, attrs ...slog.Attr) error {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if err := a.failingLocked(); err != nil {
		return err
	}
	a.seq++
	rec := slog.NewRecord(time.Now(), slog.LevelInfo, "", 0)
	rec.AddAttrs(slog.String("event", event), slog.Uint64("seq", a.seq), slog.String("boot", a.boot))
	rec.AddAttrs(attrs...)
	if err := a.h.Handle(context.Background(), rec); err != nil {
		return fmt.Errorf("audit: %w", err)
	}
	return nil
}

// Close closes the file, and stops trying to open one that could not be.
func (a *Audit) Close() error {
	if a == nil {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.path = ""
	if a.f == nil {
		return nil
	}
	return a.f.Close()
}
