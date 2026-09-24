//go:build e2e

package e2e

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"testing"
	"time"

	"flyballd/internal/principal"
)

// What TestMain sets up once: the two binaries, built from this tree, and
// the real flyball-runner.
var (
	repoRoot string // the repository: daemon/.. from here
	binDir   string // flyball, flyballd
	venvBin  string // engine/.venv/bin: flyball-runner, python
	skipWhy  string // non-empty: every test skips with this
	// password is the admin password every credential shape here uses, and
	// passwordLine its $scrypt$ line, made by the built `flyball password`.
	password     = "e2e-correct-horse-1"
	passwordLine string
)

func TestMain(m *testing.M) {
	flag.Parse()
	os.Exit(func() int {
		wd, err := os.Getwd()
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		repoRoot = filepath.Clean(filepath.Join(wd, "..", ".."))
		venvBin = filepath.Join(repoRoot, "engine", ".venv", "bin")
		if _, err := os.Stat(filepath.Join(venvBin, "flyball-runner")); err != nil {
			skipWhy = "no flyball-runner in engine/.venv (cd engine && UV_FROZEN=1 uv sync --all-extras)"
			return m.Run()
		}
		dir, err := os.MkdirTemp("", "fe2e-bin-")
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			return 1
		}
		defer os.RemoveAll(dir)
		for _, c := range []string{"flyball", "flyballd"} {
			cmd := exec.Command("go", "build", "-o", filepath.Join(dir, c), "./cmd/"+c)
			cmd.Dir = filepath.Join(repoRoot, "daemon")
			if out, err := cmd.CombinedOutput(); err != nil {
				fmt.Fprintf(os.Stderr, "building %s: %v\n%s", c, err, out)
				return 1
			}
		}
		binDir = dir
		out, err := exec.Command(filepath.Join(dir, "flyball"), "password", password).Output()
		if err != nil {
			fmt.Fprintln(os.Stderr, "flyball password:", err)
			return 1
		}
		passwordLine = strings.TrimSpace(string(out))
		return m.Run()
	}())
}

// env is one test's world: a short temp dir (unix socket paths stay well
// under 100 bytes) holding its rigs, stores, runtime dir and state, and an
// environment every process it starts inherits. Every process carrying
// its marker is killed when the test ends, and the dir removed.
type env struct {
	t      *testing.T
	dir    string
	rt     string // XDG_RUNTIME_DIR
	marker string
	vars   []string
}

func need(t *testing.T) {
	t.Helper()
	if skipWhy != "" {
		t.Skip(skipWhy)
	}
}

func newEnv(t *testing.T) *env {
	t.Helper()
	need(t)
	dir, err := os.MkdirTemp("", "fe2e-")
	if err != nil {
		t.Fatal(err)
	}
	e := &env{t: t, dir: dir, rt: filepath.Join(dir, "rt"), marker: filepath.Base(dir)}
	for _, d := range []string{"rt", "state", "config", "home"} {
		if err := os.Mkdir(filepath.Join(dir, d), 0o700); err != nil {
			t.Fatal(err)
		}
	}
	for _, kv := range os.Environ() {
		k, _, _ := strings.Cut(kv, "=")
		if strings.HasPrefix(k, "FLYBALL") || strings.HasPrefix(k, "XDG_") ||
			k == "HOME" || k == "PATH" || k == "RUNTIME_DIRECTORY" || k == "E2E_MARKER" {
			continue
		}
		e.vars = append(e.vars, kv)
	}
	e.vars = append(e.vars,
		"PATH="+venvBin+string(os.PathListSeparator)+binDir+string(os.PathListSeparator)+os.Getenv("PATH"),
		"XDG_RUNTIME_DIR="+e.rt,
		"XDG_STATE_HOME="+filepath.Join(dir, "state"),
		"XDG_CONFIG_HOME="+filepath.Join(dir, "config"),
		"HOME="+filepath.Join(dir, "home"),
		"UV_FROZEN=1",
		"E2E_MARKER="+e.marker,
	)
	t.Cleanup(func() {
		e.killAll()
		os.RemoveAll(dir)
	})
	return e
}

// killAll SIGKILLs every process whose environment carries this env's
// marker: runners orphaned by a killed front included.
func (e *env) killAll() {
	want := []byte("E2E_MARKER=" + e.marker + "\x00")
	for range 3 {
		ents, _ := os.ReadDir("/proc")
		n := 0
		for _, ent := range ents {
			pid, err := strconv.Atoi(ent.Name())
			if err != nil || pid == os.Getpid() {
				continue
			}
			b, err := os.ReadFile(filepath.Join("/proc", ent.Name(), "environ"))
			if err == nil && bytes.Contains(append(b, 0), want) {
				syscall.Kill(pid, syscall.SIGKILL)
				n++
			}
		}
		if n == 0 {
			return
		}
		time.Sleep(200 * time.Millisecond)
	}
}

// path is p under the env's dir.
func (e *env) path(p ...string) string { return filepath.Join(append([]string{e.dir}, p...)...) }

// rig writes the oven example into sub/oven.yaml with extra (YAML text,
// e.g. a runner: section) appended, and returns its path. Its store is
// sub/oven.sqlite.
func (e *env) rig(sub, extra string) string {
	e.t.Helper()
	src, err := os.ReadFile(filepath.Join(repoRoot, "examples", "simulated", "oven.yaml"))
	if err != nil {
		e.t.Fatal(err)
	}
	if err := os.MkdirAll(e.path(sub), 0o755); err != nil {
		e.t.Fatal(err)
	}
	p := e.path(sub, "oven.yaml")
	if err := os.WriteFile(p, append(src, "\n"+extra+"\n"...), 0o644); err != nil {
		e.t.Fatal(err)
	}
	return p
}

// write writes a file under the env's dir.
func (e *env) write(rel, content string) string {
	e.t.Helper()
	p := e.path(rel)
	if err := os.MkdirAll(filepath.Dir(p), 0o755); err != nil {
		e.t.Fatal(err)
	}
	if err := os.WriteFile(p, []byte(content), 0o644); err != nil {
		e.t.Fatal(err)
	}
	return p
}

// proc is a started process. Its stdout and stderr go straight to a file
// (not a pipe), so a child that outlives it -- a runner whose front was
// killed -- neither blocks Wait nor loses its output.
type proc struct {
	t    *testing.T
	name string
	cmd  *exec.Cmd
	log  string
	done chan struct{}
}

// start runs prog (a name on the env's PATH) with args, in the env's dir,
// in a process group of its own. It is stopped (SIGTERM to the group, then
// SIGKILL) when the test ends; its output is logged if the test failed.
func (e *env) start(name, prog string, args ...string) *proc {
	e.t.Helper()
	return e.startIn(e.dir, nil, name, prog, args...)
}

func (e *env) startIn(dir string, extraEnv []string, name, prog string, args ...string) *proc {
	e.t.Helper()
	logPath := e.path(name + ".log")
	f, err := os.OpenFile(logPath, os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		e.t.Fatal(err)
	}
	defer f.Close()
	cmd := exec.Command(e.lookPath(prog), args...)
	cmd.Dir = dir
	cmd.Env = append(append([]string(nil), e.vars...), extraEnv...)
	cmd.Stdout, cmd.Stderr = f, f
	cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}
	if err := cmd.Start(); err != nil {
		e.t.Fatalf("starting %s: %v", name, err)
	}
	p := &proc{t: e.t, name: name, cmd: cmd, log: logPath, done: make(chan struct{})}
	go func() { cmd.Wait(); close(p.done) }()
	e.t.Cleanup(func() {
		p.stop(10 * time.Second)
		if e.t.Failed() {
			out := p.output()
			if len(out) > 12000 {
				out = "..." + out[len(out)-12000:]
			}
			e.t.Logf("---- %s output ----\n%s", name, out)
		}
	})
	return p
}

func (e *env) lookPath(prog string) string {
	for _, d := range []string{binDir, venvBin} {
		if p := filepath.Join(d, prog); fileExists(p) {
			return p
		}
	}
	return prog
}

// run runs prog to completion (60 s at most) in the env and returns its
// stdout, stderr and exit code.
func (e *env) run(extraEnv []string, prog string, args ...string) (stdout, stderr string, code int) {
	e.t.Helper()
	cmd := exec.Command(e.lookPath(prog), args...)
	cmd.Dir = e.dir
	cmd.Env = append(append([]string(nil), e.vars...), extraEnv...)
	var o, s bytes.Buffer
	cmd.Stdout, cmd.Stderr = &o, &s
	if err := cmd.Start(); err != nil {
		e.t.Fatal(err)
	}
	done := make(chan error, 1)
	go func() { done <- cmd.Wait() }()
	select {
	case <-done:
	case <-time.After(60 * time.Second):
		cmd.Process.Kill()
		<-done
		e.t.Fatalf("%s %v: still running after 60 s", prog, args)
	}
	return o.String(), s.String(), cmd.ProcessState.ExitCode()
}

func (p *proc) output() string {
	b, _ := os.ReadFile(p.log)
	return string(b)
}

func (p *proc) pid() int { return p.cmd.Process.Pid }

// exited reports whether the process has ended.
func (p *proc) exited() bool {
	select {
	case <-p.done:
		return true
	default:
		return false
	}
}

// wait waits up to d for the process to end and returns its exit code.
func (p *proc) wait(d time.Duration) int {
	p.t.Helper()
	select {
	case <-p.done:
		return p.cmd.ProcessState.ExitCode()
	case <-time.After(d):
		p.t.Fatalf("%s still running after %s", p.name, d)
		return -1
	}
}

// stop sends SIGTERM to the group, then SIGKILL after d.
func (p *proc) stop(d time.Duration) {
	if p.exited() {
		syscall.Kill(-p.pid(), syscall.SIGKILL) // anything left in its group
		return
	}
	syscall.Kill(-p.pid(), syscall.SIGTERM)
	select {
	case <-p.done:
	case <-time.After(d):
	}
	syscall.Kill(-p.pid(), syscall.SIGKILL)
	<-p.done
}

// waitOutput waits up to d for re to match the process's output and
// returns the submatches.
func (p *proc) waitOutput(re string, d time.Duration) []string {
	p.t.Helper()
	rx := regexp.MustCompile(re)
	for end := time.Now().Add(d); time.Now().Before(end); time.Sleep(100 * time.Millisecond) {
		if m := rx.FindStringSubmatch(p.output()); m != nil {
			return m
		}
		if p.exited() {
			if m := rx.FindStringSubmatch(p.output()); m != nil {
				return m
			}
			p.t.Fatalf("%s exited (%d) before printing %q; output:\n%s", p.name, p.cmd.ProcessState.ExitCode(), re, p.output())
		}
	}
	p.t.Fatalf("%s: no %q within %s; output:\n%s", p.name, re, d, p.output())
	return nil
}

// flyballRun starts `flyball run rig args...` and returns the process and
// where the front listens: an http(s)://host:port base URL, or unix:/path.
func (e *env) flyballRun(name, rig string, args ...string) (*proc, string) {
	e.t.Helper()
	p := e.start(name, "flyball", append([]string{"run", rig}, args...)...)
	m := p.waitOutput(`flyball: serving rig \S+ on (\S+) \(`, 60*time.Second)
	return p, strings.TrimSuffix(m[1], "/")
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil
}

// ---- HTTP ----

type resp struct {
	Status int
	Header http.Header
	Body   []byte
}

func (r resp) json(t *testing.T, v any) {
	t.Helper()
	if err := json.Unmarshal(r.Body, v); err != nil {
		t.Fatalf("decoding %q: %v", r.Body, err)
	}
}

func (r resp) String() string { return fmt.Sprintf("%d %s", r.Status, bytes.TrimSpace(r.Body)) }

// hc is the default client: no cookie jar, no redirects followed.
var hc = &http.Client{Timeout: 20 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error {
	return http.ErrUseLastResponse
}}

// h is a header list: name, value, name, value...
type h []string

func do(t *testing.T, c *http.Client, method, url, body string, hdr h) resp {
	t.Helper()
	r, err := try(c, method, url, body, hdr)
	if err != nil {
		t.Fatalf("%s %s: %v", method, url, err)
	}
	return r
}

func try(c *http.Client, method, url, body string, hdr h) (resp, error) {
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, url, rd)
	if err != nil {
		return resp{}, err
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	for i := 0; i+1 < len(hdr); i += 2 {
		if strings.EqualFold(hdr[i], "Host") {
			req.Host = hdr[i+1]
			continue
		}
		req.Header.Add(hdr[i], hdr[i+1])
	}
	res, err := c.Do(req)
	if err != nil {
		return resp{}, err
	}
	defer res.Body.Close()
	b, err := io.ReadAll(res.Body)
	return resp{Status: res.StatusCode, Header: res.Header, Body: b}, err
}

// waitStatus repeats the request until it answers want, up to d.
func waitStatus(t *testing.T, c *http.Client, method, url string, hdr h, want int, d time.Duration) resp {
	t.Helper()
	var last resp
	var lastErr error
	for end := time.Now().Add(d); time.Now().Before(end); time.Sleep(250 * time.Millisecond) {
		last, lastErr = try(c, method, url, "", hdr)
		if lastErr == nil && last.Status == want {
			return last
		}
	}
	t.Fatalf("%s %s: no %d within %s (last: %v %v)", method, url, want, d, last, lastErr)
	return last
}

func bearer(tok string) h { return h{"Authorization", "Bearer " + tok} }

// origin is the header a same-origin browser request carries.
func origin(base string) h { return h{"Origin", base} }

func cat(hs ...h) h {
	var out h
	for _, x := range hs {
		out = append(out, x...)
	}
	return out
}

// rawRequest sends a request line verbatim over TCP (the path is not
// cleaned by any client library) and reads the answer.
func rawRequest(t *testing.T, addr, method, path string) resp {
	t.Helper()
	c, err := net.DialTimeout("tcp", addr, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer c.Close()
	c.SetDeadline(time.Now().Add(10 * time.Second))
	fmt.Fprintf(c, "%s %s HTTP/1.1\r\nHost: %s\r\nConnection: close\r\n\r\n", method, path, addr)
	res, err := http.ReadResponse(bufio.NewReader(c), nil)
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	b, _ := io.ReadAll(res.Body)
	return resp{Status: res.StatusCode, Header: res.Header, Body: b}
}

// unixClient dials sock whatever the URL says.
func unixClient(sock string) *http.Client {
	return &http.Client{Timeout: 20 * time.Second, Transport: &http.Transport{
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", sock)
		},
	}, CheckRedirect: hc.CheckRedirect}
}

// hostPort is base without its scheme.
func hostPort(base string) string {
	_, hp, _ := strings.Cut(base, "://")
	return hp
}

// ---- the runner's side ----

// runnerInfo is GET /api/runner's endpoint, and the front-dir it is in.
func frontDirOf(t *testing.T, endpoint string) string {
	t.Helper()
	sock, ok := strings.CutPrefix(endpoint, "unix:")
	if !ok {
		t.Fatalf("the runner's endpoint is %q, want unix:", endpoint)
	}
	return filepath.Dir(sock)
}

// lockPid reads the pid from a front-dir's runner.lock.
func lockPid(t *testing.T, dir string) int {
	t.Helper()
	b, err := os.ReadFile(filepath.Join(dir, "runner.lock"))
	if err != nil {
		t.Fatal(err)
	}
	f := strings.Fields(string(b))
	if len(f) < 2 || f[0] != "pid" {
		t.Fatalf("runner.lock = %q", b)
	}
	pid, _ := strconv.Atoi(f[1])
	return pid
}

func alive(pid int) bool { return syscall.Kill(pid, 0) == nil }

func readFile(t *testing.T, p string) string {
	t.Helper()
	b, err := os.ReadFile(p)
	if err != nil {
		t.Fatal(err)
	}
	return strings.TrimSpace(string(b))
}

// mint signs a principal with the key and aud in a front-dir, the way the
// front does; aud overrides the dir's when not empty.
func mint(t *testing.T, dir, aud string, scp []string, now time.Time) string {
	t.Helper()
	k, err := principal.ReadKeyFile(filepath.Join(dir, "key"))
	if err != nil {
		t.Fatal(err)
	}
	if aud == "" {
		aud = readFile(t, filepath.Join(dir, "aud"))
	}
	tok, err := principal.Mint(k, principal.Claims{Sub: "local:console", Nm: "e2e", Sid: "e2e-sid", Scp: scp,
		Kind: "human", Aud: aud, Sch: "http", Iat: now.Unix(), Exp: now.Add(principal.Lifetime).Unix()})
	if err != nil {
		t.Fatal(err)
	}
	return tok
}

// auditRow is one row of the runner's action audit.
type auditRow map[string]any

func (r auditRow) s(k string) string { v, _ := r[k].(string); return v }

// audit reads the store's audit table with the runner's own Python.
func audit(t *testing.T, store string) []auditRow {
	t.Helper()
	code := `import sqlite3, json, sys
c = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
c.row_factory = sqlite3.Row
print(json.dumps([dict(r) for r in c.execute("select * from audit order by id")]))`
	out, err := exec.Command(filepath.Join(venvBin, "python"), "-c", code, store).Output()
	if err != nil {
		var ee *exec.ExitError
		if errors.As(err, &ee) {
			t.Logf("reading the audit of %s: %s", store, ee.Stderr)
		}
		return nil
	}
	var rows []auditRow
	if err := json.Unmarshal(out, &rows); err != nil {
		t.Fatal(err)
	}
	return rows
}

// waitAudit waits up to 10 s for a row matching every key=value in want.
func waitAudit(t *testing.T, store string, want map[string]string) auditRow {
	t.Helper()
	var rows []auditRow
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(200 * time.Millisecond) {
		rows = audit(t, store)
	next:
		for _, r := range rows {
			for k, v := range want {
				got := fmt.Sprint(r[k])
				if k == "details" {
					if !strings.Contains(got, v) {
						continue next
					}
				} else if got != v {
					continue next
				}
			}
			return r
		}
	}
	if len(rows) > 6 {
		rows = rows[len(rows)-6:]
	}
	b, _ := json.MarshalIndent(rows, "", " ")
	t.Fatalf("no audit row with %v in %s; last rows:\n%s", want, store, b)
	return nil
}

// program is a program that holds for ten hours after taking the controller.
const program = `{"name":"e2e","steps":[{"regulate":{"controllers":"heater.drive","setpoint":50}},{"wait":{"minutes":600}}]}`

// ---- websockets ----

// ws is a client websocket, reads only (and the close handshake).
type ws struct {
	c  net.Conn
	br *bufio.Reader
}

// dialWS upgrades path on a fresh connection from dial; host is the Host
// header. It returns the handshake's response even when it was refused.
func dialWS(t *testing.T, dial func() (net.Conn, error), host, path string, hdr h) (*ws, *http.Response) {
	t.Helper()
	c, err := dial()
	if err != nil {
		t.Fatal(err)
	}
	key := make([]byte, 16)
	rand.Read(key)
	var b strings.Builder
	fmt.Fprintf(&b, "GET %s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n", path, host)
	fmt.Fprintf(&b, "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n", base64.StdEncoding.EncodeToString(key))
	for i := 0; i+1 < len(hdr); i += 2 {
		fmt.Fprintf(&b, "%s: %s\r\n", hdr[i], hdr[i+1])
	}
	b.WriteString("\r\n")
	c.SetDeadline(time.Now().Add(10 * time.Second))
	if _, err := c.Write([]byte(b.String())); err != nil {
		t.Fatal(err)
	}
	br := bufio.NewReader(c)
	res, err := http.ReadResponse(br, &http.Request{Method: "GET"})
	if err != nil {
		c.Close()
		t.Fatal(err)
	}
	c.SetDeadline(time.Time{})
	w := &ws{c: c, br: br}
	t.Cleanup(func() { c.Close() })
	return w, res
}

// next reads the next frame within d: a data frame's payload, or the close
// code (and closed true) of a close frame.
func (w *ws) next(d time.Duration) (payload []byte, code int, closed bool, err error) {
	w.c.SetReadDeadline(time.Now().Add(d))
	for {
		head := make([]byte, 2)
		if _, err := io.ReadFull(w.br, head); err != nil {
			return nil, 0, false, err
		}
		op := head[0] & 0x0f
		n := uint64(head[1] & 0x7f)
		switch n {
		case 126:
			ext := make([]byte, 2)
			if _, err := io.ReadFull(w.br, ext); err != nil {
				return nil, 0, false, err
			}
			n = uint64(binary.BigEndian.Uint16(ext))
		case 127:
			ext := make([]byte, 8)
			if _, err := io.ReadFull(w.br, ext); err != nil {
				return nil, 0, false, err
			}
			n = binary.BigEndian.Uint64(ext)
		}
		if n > 16<<20 {
			return nil, 0, false, fmt.Errorf("frame of %d bytes", n)
		}
		p := make([]byte, n)
		if _, err := io.ReadFull(w.br, p); err != nil {
			return nil, 0, false, err
		}
		switch op {
		case 0x8:
			if len(p) >= 2 {
				code = int(binary.BigEndian.Uint16(p))
			} else {
				code = 1005
			}
			return p, code, true, nil
		case 0x9, 0xa:
			continue
		default:
			return p, 0, false, nil
		}
	}
}

// waitClose reads until a close frame, up to d, and returns its code and
// how long it took; -1 if the connection ended without one.
func (w *ws) waitClose(d time.Duration) (int, time.Duration) {
	start := time.Now()
	for time.Since(start) < d {
		_, code, closed, err := w.next(d - time.Since(start))
		if err != nil {
			return -1, time.Since(start)
		}
		if closed {
			return code, time.Since(start)
		}
	}
	return -1, time.Since(start)
}

// open asserts the socket is open: a data frame arrives within d.
func (w *ws) open(t *testing.T, d time.Duration) {
	t.Helper()
	p, code, closed, err := w.next(d)
	if err != nil || closed {
		t.Fatalf("the websocket is not open: closed=%v code=%d err=%v payload=%q", closed, code, err, p)
	}
}

func tcpDial(addr string) func() (net.Conn, error) {
	return func() (net.Conn, error) { return net.DialTimeout("tcp", addr, 5*time.Second) }
}

// ---- TLS ----

// writeCert writes a self-signed ECDSA certificate for 127.0.0.1 and
// localhost, with serial, to cert and key (atomically: a temp file and a
// rename each), and returns it.
func writeCert(t *testing.T, cert, key string, serial int64) *x509.Certificate {
	t.Helper()
	k, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	tmpl := &x509.Certificate{
		SerialNumber: big.NewInt(serial), Subject: pkix.Name{CommonName: "flyball e2e " + strconv.FormatInt(serial, 10)},
		NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().Add(24 * time.Hour),
		KeyUsage:    x509.KeyUsageDigitalSignature | x509.KeyUsageCertSign,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true, IsCA: true,
		IPAddresses: []net.IP{net.ParseIP("127.0.0.1")}, DNSNames: []string{"localhost"},
	}
	der, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &k.PublicKey, k)
	if err != nil {
		t.Fatal(err)
	}
	kb, err := x509.MarshalECPrivateKey(k)
	if err != nil {
		t.Fatal(err)
	}
	atomicWrite(t, key, pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: kb}), 0o600)
	atomicWrite(t, cert, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: der}), 0o644)
	c, _ := x509.ParseCertificate(der)
	return c
}

func atomicWrite(t *testing.T, p string, b []byte, mode os.FileMode) {
	t.Helper()
	tmp := p + ".tmp"
	if err := os.WriteFile(tmp, b, mode); err != nil {
		t.Fatal(err)
	}
	if err := os.Rename(tmp, p); err != nil {
		t.Fatal(err)
	}
}

// servedSerial makes a new TLS connection to addr, trusting pool, and
// returns the serial the server presented.
func servedSerial(t *testing.T, addr string, pool *x509.CertPool) (int64, error) {
	t.Helper()
	c, err := tls.DialWithDialer(&net.Dialer{Timeout: 5 * time.Second}, "tcp", addr,
		&tls.Config{RootCAs: pool, ServerName: "127.0.0.1"})
	if err != nil {
		return 0, err
	}
	defer c.Close()
	return c.ConnectionState().PeerCertificates[0].SerialNumber.Int64(), nil
}

// ---- MCP over streamable HTTP ----

// mcp is one MCP session through the front.
type mcp struct {
	t    *testing.T
	url  string
	hdr  h
	sid  string
	next int
}

// call sends one JSON-RPC request and returns the HTTP status and, on 200,
// the response's result (or its error, as err).
func (m *mcp) call(method string, params any) (int, json.RawMessage, error) {
	m.t.Helper()
	m.next++
	body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "id": m.next, "method": method, "params": params})
	hdr := cat(m.hdr, h{"Accept", "application/json, text/event-stream"})
	if m.sid != "" {
		hdr = append(hdr, "Mcp-Session-Id", m.sid, "Mcp-Protocol-Version", "2025-06-18")
	}
	r := do(m.t, hc, "POST", m.url, string(body), hdr)
	if r.Status != 200 {
		return r.Status, r.Body, nil
	}
	if s := r.Header.Get("Mcp-Session-Id"); s != "" {
		m.sid = s
	}
	msg := r.Body
	if strings.HasPrefix(r.Header.Get("Content-Type"), "text/event-stream") {
		msg = nil
		for _, line := range strings.Split(string(r.Body), "\n") {
			if d, ok := strings.CutPrefix(strings.TrimRight(line, "\r"), "data:"); ok {
				msg = []byte(strings.TrimSpace(d))
			}
		}
	}
	var env struct {
		Result json.RawMessage `json:"result"`
		Error  json.RawMessage `json:"error"`
	}
	if err := json.Unmarshal(msg, &env); err != nil {
		return r.Status, r.Body, fmt.Errorf("not JSON-RPC: %q", r.Body)
	}
	if env.Error != nil {
		return r.Status, env.Error, fmt.Errorf("JSON-RPC error: %s", env.Error)
	}
	return r.Status, env.Result, nil
}

// initialize opens the session; the status is the HTTP status.
func (m *mcp) initialize() int {
	m.t.Helper()
	st, _, err := m.call("initialize", map[string]any{"protocolVersion": "2025-06-18", "capabilities": map[string]any{},
		"clientInfo": map[string]any{"name": "flyball-e2e", "version": "1"}})
	if st == 200 && err != nil {
		m.t.Fatal(err)
	}
	if st == 200 {
		body, _ := json.Marshal(map[string]any{"jsonrpc": "2.0", "method": "notifications/initialized"})
		do(m.t, hc, "POST", m.url, string(body), cat(m.hdr, h{"Accept", "application/json, text/event-stream",
			"Mcp-Session-Id", m.sid, "Mcp-Protocol-Version", "2025-06-18"}))
	}
	return st
}

// tools lists the session's tool names.
func (m *mcp) tools() []string {
	m.t.Helper()
	st, res, err := m.call("tools/list", map[string]any{})
	if st != 200 || err != nil {
		m.t.Fatalf("tools/list: %d %s %v", st, res, err)
	}
	var out struct {
		Tools []struct {
			Name string `json:"name"`
		} `json:"tools"`
	}
	json.Unmarshal(res, &out)
	var names []string
	for _, x := range out.Tools {
		names = append(names, x.Name)
	}
	return names
}

// tool calls a tool and returns its text and whether it is an error.
func (m *mcp) tool(name string, args map[string]any) (string, bool) {
	m.t.Helper()
	st, res, err := m.call("tools/call", map[string]any{"name": name, "arguments": args})
	if st != 200 || err != nil {
		m.t.Fatalf("tools/call %s: %d %s %v", name, st, res, err)
	}
	var out struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
		IsError bool `json:"isError"`
	}
	json.Unmarshal(res, &out)
	var b strings.Builder
	for _, c := range out.Content {
		b.WriteString(c.Text)
	}
	return b.String(), out.IsError
}

func contains(xs []string, x string) bool {
	for _, y := range xs {
		if y == x {
			return true
		}
	}
	return false
}

func x509Pool(certs ...*x509.Certificate) *x509.CertPool {
	p := x509.NewCertPool()
	for _, c := range certs {
		p.AddCert(c)
	}
	return p
}

// tlsClient trusts pool and opens a new connection for every request.
func tlsClient(pool *x509.CertPool) *http.Client {
	return &http.Client{Timeout: 20 * time.Second, CheckRedirect: hc.CheckRedirect,
		Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: pool}, DisableKeepAlives: true}}
}

// autheliaProxy is a stand-in for a reverse proxy doing Authelia's forward
// auth: it serves on loopback TCP and forwards to the front's unix socket,
// dropping every inbound Remote-* header and asserting the identity the
// test names in X-Test-User (X-Test-Groups, X-Test-Email) as Remote-User,
// Remote-Groups and Remote-Email. It returns its base URL.
func autheliaProxy(t *testing.T, sock string) string {
	t.Helper()
	rp := &httputil.ReverseProxy{
		Rewrite: func(pr *httputil.ProxyRequest) {
			pr.Out.URL.Scheme, pr.Out.URL.Host = "http", "front"
			pr.Out.Host = pr.In.Host
			for k := range pr.Out.Header {
				if strings.HasPrefix(strings.ToLower(k), "remote-") || strings.HasPrefix(strings.ToLower(k), "remote_") {
					pr.Out.Header.Del(k)
				}
			}
			assert := map[string]string{"X-Test-User": "Remote-User", "X-Test-Groups": "Remote-Groups", "X-Test-Email": "Remote-Email"}
			for from, to := range assert {
				if v := pr.In.Header.Get(from); v != "" {
					pr.Out.Header.Set(to, v)
				}
				pr.Out.Header.Del(from)
			}
		},
		Transport: &http.Transport{DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			var d net.Dialer
			return d.DialContext(ctx, "unix", sock)
		}},
	}
	srv := httptest.NewServer(rp)
	t.Cleanup(srv.Close)
	return srv.URL
}

// execPython runs one SQL statement against store (read-write) with the
// runner's own Python and returns its output.
func execPython(store, sql string) (string, error) {
	code := `import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute(sys.argv[2])
c.commit()`
	out, err := exec.Command(filepath.Join(venvBin, "python"), "-c", code, store, sql).CombinedOutput()
	return string(out), err
}
