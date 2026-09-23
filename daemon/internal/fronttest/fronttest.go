//go:build unix

// Package fronttest is a fronted flyball-runner stand-in for the tests of
// the two fronts (`flyball run`, flyballd): a real HTTP server on the
// endpoint a front-dir names, which answers the readiness handshake and
// refuses any request whose principal does not verify under its key and
// aud, as the Python runner does (A5). It is test infrastructure only.
package fronttest

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"flyballd/internal/endpoint"
	"flyballd/internal/endpoint/frontdir"
	"flyballd/internal/principal"
)

// Echo is what every guarded path answers: who the principal says the
// caller is, and which incarnation answered.
type Echo struct {
	Path   string           `json:"path"`
	Claims principal.Claims `json:"claims"`
	Pid    int              `json:"pid"`
	Key    string           `json:"key"` // the first 8 hex of the key, to tell incarnations apart
}

// Runner is one fronted runner.
type Runner struct {
	EP   endpoint.Endpoint
	Key  principal.Key
	Aud  string
	Root string

	ln   net.Listener
	srv  *http.Server
	lock *os.File
}

// Serve binds ep and serves as a fronted runner under root with key and aud.
func Serve(ep endpoint.Endpoint, key principal.Key, aud, root string) (*Runner, error) {
	if ep.Network == "unix" {
		os.Remove(ep.Address)
	}
	ln, err := net.Listen(ep.Network, ep.Address)
	if err != nil {
		return nil, err
	}
	r := &Runner{EP: ep, Key: key, Aud: aud, Root: root, ln: ln}
	r.srv = &http.Server{Handler: r, ReadHeaderTimeout: 5 * time.Second}
	go r.srv.Serve(ln)
	return r, nil
}

// FromFrontDir reads dir's key, aud and endpoint as `flyball-runner
// --front-dir dir` does, holds runner.lock (writing `pid <n> rig <rig>`)
// and serves.
func FromFrontDir(dir, root, rig string) (*Runner, error) {
	if err := frontdir.Check(dir); err != nil {
		return nil, err
	}
	key, err := frontdir.ReadKey(dir)
	if err != nil {
		return nil, err
	}
	aud, err := os.ReadFile(filepath.Join(dir, frontdir.Aud))
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(filepath.Join(dir, frontdir.EndpointFile))
	if err != nil {
		return nil, err
	}
	ep, err := endpoint.Parse(strings.TrimSpace(string(raw)))
	if err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dir, frontdir.Lock), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		return nil, fmt.Errorf("runner.lock: %w", err)
	}
	lock.Truncate(0)
	fmt.Fprintf(lock, "pid %d rig %s\n", os.Getpid(), rig)
	r, err := Serve(ep, principal.Key(key), strings.TrimSpace(string(aud)), root)
	if err != nil {
		lock.Close()
		return nil, err
	}
	r.lock = lock
	return r, nil
}

// Close stops serving and releases runner.lock.
func (r *Runner) Close() {
	r.srv.Close()
	if r.EP.Network == "unix" {
		os.Remove(r.EP.Address)
	}
	if r.lock != nil {
		r.lock.Close()
	}
}

func (r *Runner) ServeHTTP(w http.ResponseWriter, req *http.Request) {
	path := strings.TrimPrefix(req.URL.Path, r.Root)
	toks := req.Header.Values(principal.Header)
	var claims principal.Claims
	err := errors.New("no principal")
	if len(toks) == 1 {
		claims, err = principal.Verify(toks[0], r.Key, r.Aud, time.Now())
	}
	if err != nil {
		code := "format"
		var pe *principal.Error
		if errors.As(err, &pe) {
			code = pe.Code
		}
		w.Header().Set("X-Flyball-Principal-Error", code)
		http.Error(w, `{"detail":"no valid principal"}`, http.StatusUnauthorized)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if path == "/api/auth/front" {
		json.NewEncoder(w).Encode(map[string]any{"protocol": endpoint.Protocol, "aud": r.Aud, "pid": os.Getpid(), "flyball": "fake"})
		return
	}
	json.NewEncoder(w).Encode(Echo{Path: req.URL.Path, Claims: claims, Pid: os.Getpid(), Key: fmt.Sprintf("%x", r.Key[:4])})
}
