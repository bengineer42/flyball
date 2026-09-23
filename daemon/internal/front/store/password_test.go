package store

import (
	"errors"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

// Lines made outside this package: the first by `flyball password` (the Go
// CLI, cmd/flyball/local.go), the second by the same code as the runner's
// auth.py hash_password (hashlib.scrypt, n=16384 r=8 p=1, dklen 64).
const (
	goLine  = "$scrypt$n=16384,r=8,p=1$X7nwhKWa9FbD_ss_a3-beQ$m_yVgSuXkoSt-9wc3WihNGKs_4s7jLyGiBlTcgiqiW9YH4-8LeE5KZLhQM1c6N7Y0uNPT0sJmeu8ZKEi3Y5pcw"
	goPlain = "correct horse battery"
	pyLine  = "$scrypt$n=16384,r=8,p=1$Z3Wk4WypLn_u1THV-PEr4A$ElDY78oSOV7HRAc1aBDJnm30nq3FT69UDxT-HVRJSIIUWkGDbT-S2vbpxDtkjOsXFLTxFVw83mfSG1bzLkUMOg"
	pyPlain = "staple été"
)

func TestScryptCompat(t *testing.T) {
	h := NewHasher(2)
	for _, c := range []struct {
		name, line, plain string
	}{{"flyball password", goLine, goPlain}, {"auth.py hash_password", pyLine, pyPlain}} {
		ok, err := h.Verify(c.plain, c.line)
		if err != nil || !ok {
			t.Errorf("%s: Verify(right) = %v, %v; want true, nil", c.name, ok, err)
		}
		ok, err = h.Verify(c.plain+"x", c.line)
		if err != nil || ok {
			t.Errorf("%s: Verify(wrong) = %v, %v; want false, nil", c.name, ok, err)
		}
	}
}

func TestParseScryptRefuses(t *testing.T) {
	for _, line := range []string{
		"",
		"hunter2", // plaintext: the caller falls back, never compares it
		"$scrypt$",
		"$scrypt$n=16384,r=8,p=1$salt",
		"$scrypt$n=16384,r=8$c2FsdA$aGFzaA",
		"$scrypt$n=16383,r=8,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaA",      // not a power of two
		"$scrypt$n=1073741824,r=8,p=1$c2FsdHNhbHRzYWx0c2FsdA$aGFzaGhhc2hoYXNoaGFzaA", // 1 TiB of memory
		"$scrypt$n=16384,r=8,p=1$!!$aGFzaGhhc2hoYXNoaGFzaA",
		"$scrypt$n=16384,r=8,p=1$c2FsdHNhbHRzYWx0c2FsdA$", // empty hash
	} {
		if err := ParseScrypt(line); err == nil {
			t.Errorf("ParseScrypt(%q) = nil; want an error", line)
		}
		if ok, err := NewHasher(1).Verify("hunter2", line); ok || err == nil {
			t.Errorf("Verify(_, %q) = %v, %v; want false and an error", line, ok, err)
		}
	}
	if err := ParseScrypt(goLine); err != nil {
		t.Errorf("ParseScrypt(goLine) = %v", err)
	}
}

// blockingHasher's hash waits on release, so a test can hold slots open.
func blockingHasher(n int) (h *Hasher, entered chan struct{}, release chan struct{}) {
	h = NewHasher(n)
	entered, release = make(chan struct{}, 16), make(chan struct{})
	h.hash = func(plain, salt []byte, n, r, p, size int) ([]byte, error) {
		entered <- struct{}{}
		<-release
		return make([]byte, size), nil
	}
	return h, entered, release
}

func TestHashSemaphore(t *testing.T) {
	h, entered, release := blockingHasher(2)
	var wg sync.WaitGroup
	for range 2 {
		wg.Add(1)
		go func() { defer wg.Done(); _, _ = h.Verify(goPlain, goLine) }()
	}
	<-entered
	<-entered
	start := time.Now()
	ok, err := h.Verify(goPlain, goLine)
	if !errors.Is(err, ErrBusy) || ok {
		t.Fatalf("third concurrent Verify = %v, %v; want false, ErrBusy", ok, err)
	}
	if waited := time.Since(start); waited > 50*time.Millisecond {
		t.Errorf("third Verify took %v; want an immediate refusal, not a queue", waited)
	}
	close(release)
	wg.Wait()
	if _, err := h.Verify(goPlain, goLine); err != nil {
		t.Errorf("Verify after the slots freed = %v; want no error", err)
	}
}

func TestHashSemaphoreBoundsConcurrency(t *testing.T) {
	h := NewHasher(2)
	var now, peak atomic.Int32
	h.hash = func(plain, salt []byte, n, r, p, size int) ([]byte, error) {
		c := now.Add(1)
		for {
			old := peak.Load()
			if c <= old || peak.CompareAndSwap(old, c) {
				break
			}
		}
		time.Sleep(time.Millisecond)
		now.Add(-1)
		return make([]byte, size), nil
	}
	var wg sync.WaitGroup
	var busy atomic.Int32
	for range 64 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := h.Verify(goPlain, goLine); errors.Is(err, ErrBusy) {
				busy.Add(1)
			}
		}()
	}
	wg.Wait()
	if p := peak.Load(); p > 2 {
		t.Errorf("peak concurrent hashes = %d; want at most 2", p)
	}
	if busy.Load() == 0 {
		t.Errorf("no Verify of 64 was refused; the semaphore never filled")
	}
}

func TestVerifyConstantTimeCompare(t *testing.T) {
	// The comparison itself is subtle.ConstantTimeCompare (hmac.Equal); what
	// a test can check is that it runs on the whole digest: a digest that
	// differs only in its last byte is still refused.
	h := NewHasher(1)
	h.hash = func(plain, salt []byte, n, r, p, size int) ([]byte, error) {
		out := make([]byte, size)
		out[size-1] = 1
		return out, nil
	}
	line := "$scrypt$n=16,r=1,p=1$c2FsdHNhbHRzYWx0c2FsdA$" + b64(make([]byte, 64))
	if ok, err := h.Verify("x", line); ok || err != nil {
		t.Errorf("Verify with a last-byte difference = %v, %v; want false, nil", ok, err)
	}
}
