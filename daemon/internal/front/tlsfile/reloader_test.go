package tlsfile

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"log"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func serialOf(t *testing.T, cert *tls.Certificate) int64 {
	t.Helper()
	if cert == nil || len(cert.Certificate) == 0 {
		t.Fatal("certificate has no leaf bytes")
	}
	leaf, err := x509.ParseCertificate(cert.Certificate[0])
	if err != nil {
		t.Fatalf("parsing leaf: %v", err)
	}
	return leaf.SerialNumber.Int64()
}

func mustGetCertificate(t *testing.T, r *Reloader) *tls.Certificate {
	t.Helper()
	cert, err := r.GetCertificate(&tls.ClientHelloInfo{})
	if err != nil {
		t.Fatalf("GetCertificate: %v", err)
	}
	return cert
}

func waitForSerial(t *testing.T, r *Reloader, want int64, timeout time.Duration) {
	t.Helper()
	deadline := time.Now().Add(timeout)
	for {
		if serialOf(t, mustGetCertificate(t, r)) == want {
			return
		}
		if time.Now().After(deadline) {
			t.Fatalf("serial never became %d within %s", want, timeout)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func TestNewReloaderLoadsInitialCertificate(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	r, err := NewReloader(certPath, keyPath)
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()

	if got := serialOf(t, mustGetCertificate(t, r)); got != 1 {
		t.Fatalf("serial = %d, want 1", got)
	}
	if r.Config().MinVersion != tls.VersionTLS12 {
		t.Fatalf("MinVersion = %#x, want TLS 1.2", r.Config().MinVersion)
	}
}

func TestReloadOnRenewal_PickedUpInBackground(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	r, err := newReloader(certPath, keyPath, 30*time.Millisecond)
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()
	waitForSerial(t, r, 1, time.Second)

	writeCert(t, certPath, keyPath, 2)

	// Within 10s per spec; the short poll interval here bounds the test.
	waitForSerial(t, r, 2, 2*time.Second)
}

func TestReloadOnRenewal_PickedUpOnExplicitReload(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	// A long poll interval: only the explicit Reload() should pick this up
	// within the test's lifetime.
	r, err := newReloader(certPath, keyPath, time.Hour)
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()

	writeCert(t, certPath, keyPath, 2)

	if err := r.Reload(); err != nil {
		t.Fatalf("Reload: %v", err)
	}
	if got := serialOf(t, mustGetCertificate(t, r)); got != 2 {
		t.Fatalf("serial = %d, want 2 after explicit Reload", got)
	}
}

func TestBadRenewalKeepsLastGood(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	var logBuf bytes.Buffer
	r, err := NewReloader(certPath, keyPath, WithLogger(log.New(&logBuf, "", 0)))
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()

	// A half-written renewal: truncate the cert file mid-write, as a crash
	// or a reader racing an atomic rename might see.
	if err := writeAtomic(certPath, []byte("-----BEGIN CERTIFICATE-----\nMIIB"), 0o644); err != nil {
		t.Fatalf("writing half-written cert: %v", err)
	}

	if err := r.Reload(); err == nil {
		t.Fatal("Reload of a half-written cert: want error, got nil")
	}

	if got := serialOf(t, mustGetCertificate(t, r)); got != 1 {
		t.Fatalf("serial = %d, want 1 (last good) after bad renewal", got)
	}
	if !strings.Contains(logBuf.String(), "keeping last good certificate") {
		t.Fatalf("log output = %q, want it to mention keeping the last good certificate", logBuf.String())
	}

	// A fully bad key (valid cert, garbage key) is refused the same way.
	if err := writeAtomic(keyPath, []byte("not a key"), 0o600); err != nil {
		t.Fatalf("writing bad key: %v", err)
	}
	if err := r.Reload(); err == nil {
		t.Fatal("Reload with a bad key: want error, got nil")
	}
	if got := serialOf(t, mustGetCertificate(t, r)); got != 1 {
		t.Fatalf("serial = %d, want 1 (last good) after bad key renewal", got)
	}
}

func TestStartupBadFiles(t *testing.T) {
	dir := t.TempDir()

	t.Run("missing files", func(t *testing.T) {
		_, err := NewReloader(filepath.Join(dir, "missing-cert.pem"), filepath.Join(dir, "missing-key.pem"))
		if err == nil {
			t.Fatal("want error for missing files, got nil")
		}
	})

	t.Run("garbage files", func(t *testing.T) {
		certPath, keyPath := filepath.Join(dir, "garbage-cert.pem"), filepath.Join(dir, "garbage-key.pem")
		if err := writeAtomic(certPath, []byte("nope"), 0o644); err != nil {
			t.Fatalf("writing garbage cert: %v", err)
		}
		if err := writeAtomic(keyPath, []byte("nope"), 0o600); err != nil {
			t.Fatalf("writing garbage key: %v", err)
		}
		_, err := NewReloader(certPath, keyPath)
		if err == nil {
			t.Fatal("want error for garbage files, got nil")
		}
	})
}

// TestRealHandshakePicksUpRenewal drives a real httptest TLS server through
// the reloader and confirms a fresh tls.Dial sees the certificate that was
// current at handshake time, across a rotation.
func TestRealHandshakePicksUpRenewal(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	r, err := newReloader(certPath, keyPath, 30*time.Millisecond)
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()

	ts := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	ts.TLS = r.Config()
	ts.StartTLS()
	defer ts.Close()

	dial := func() int64 {
		// httptest.Server.StartTLS fills in its own placeholder Certificates
		// entry when none is set (it only ever checks Certificates, not
		// GetCertificate). crypto/tls only consults GetCertificate ahead of
		// Certificates when the ClientHello carries SNI, so the dial must
		// send one for this real handshake to exercise the reloader.
		conn, err := tls.Dial("tcp", ts.Listener.Addr().String(), &tls.Config{
			InsecureSkipVerify: true, //nolint:gosec // test only
			ServerName:         "localhost",
		})
		if err != nil {
			t.Fatalf("tls.Dial: %v", err)
		}
		defer conn.Close()
		state := conn.ConnectionState()
		if len(state.PeerCertificates) == 0 {
			t.Fatal("handshake completed with no peer certificates")
		}
		return state.PeerCertificates[0].SerialNumber.Int64()
	}

	if got := dial(); got != 1 {
		t.Fatalf("serial served = %d, want 1", got)
	}

	writeCert(t, certPath, keyPath, 2)
	waitForSerial(t, r, 2, 2*time.Second)

	if got := dial(); got != 2 {
		t.Fatalf("serial served after renewal = %d, want 2", got)
	}
}

func TestMinimumVersionIsTLS12(t *testing.T) {
	dir := t.TempDir()
	certPath, keyPath := filepath.Join(dir, "cert.pem"), filepath.Join(dir, "key.pem")
	writeCert(t, certPath, keyPath, 1)

	r, err := NewReloader(certPath, keyPath)
	if err != nil {
		t.Fatalf("NewReloader: %v", err)
	}
	defer r.Close()

	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("Listen: %v", err)
	}
	tlsLn := tls.NewListener(ln, r.Config())
	defer tlsLn.Close()

	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		w.WriteHeader(http.StatusOK)
	})}
	go srv.Serve(tlsLn) //nolint:errcheck
	defer srv.Close()

	_, err = tls.Dial("tcp", ln.Addr().String(), &tls.Config{
		InsecureSkipVerify: true, //nolint:gosec // test only
		MinVersion:         tls.VersionTLS10,
		MaxVersion:         tls.VersionTLS11,
	})
	if err == nil {
		t.Fatal("dial with a client capped at TLS 1.1: want error, got nil")
	}

	conn, err := tls.Dial("tcp", ln.Addr().String(), &tls.Config{InsecureSkipVerify: true}) //nolint:gosec // test only
	if err != nil {
		t.Fatalf("dial with a default client: %v", err)
	}
	defer conn.Close()
	if conn.ConnectionState().Version < tls.VersionTLS12 {
		t.Fatalf("negotiated version %#x, want at least TLS 1.2", conn.ConnectionState().Version)
	}
}
