package front

import (
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

// testClock is a settable clock.
type testClock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *testClock) Now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	return c.t
}

func (c *testClock) Advance(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

// D-047 item 7: an audit that cannot be opened refuses sign-ins (503) and
// says why in /api/auth's exposure warning; it is opened again, at most
// every AuditRetry, so once the file can be opened a sign-in works and is
// recorded with no restart.
func TestAuditRetriesOpen(t *testing.T) {
	path := filepath.Join(t.TempDir(), "front", "audit.jsonl")
	if err := os.MkdirAll(path, 0o700); err != nil { // a directory where the file goes
		t.Fatal(err)
	}
	clock := &testClock{t: time.Unix(1_800_000_000, 0)}
	a, err := OpenAuditRetrying(path, clock.Now)
	if err == nil {
		t.Fatal("a directory opened as the audit file")
	}
	h := newHarness(t, Config{Auth: "password", Password: testScrypt}, func(o *Options) { o.Audit = a })
	login := func() int {
		t.Helper()
		return h.do("POST", "/api/auth/login", `{"password":"`+testPassword+`"}`, h.origin()).StatusCode
	}
	warning := func() string {
		t.Helper()
		info := readJSON[AuthInfo](t, h.do("GET", "/api/auth", "", nil))
		if info.Exposure == nil || info.Exposure.Warning == nil {
			return ""
		}
		return *info.Exposure.Warning
	}

	if code := login(); code != 503 {
		t.Fatalf("sign-in with no audit log: %d, want 503", code)
	}
	// Anyone who reaches the front reads the warning: it says the audit
	// cannot be opened and where the reason is, never the error, which
	// names a path (wave 3 F7). The log has it (frontwire.OpenAudit).
	if w := warning(); !strings.Contains(w, "no audit log") || !strings.Contains(w, "the front's log") ||
		strings.Contains(w, "is a directory") || strings.Contains(w, path) {
		t.Fatalf("exposure warning %q: want the audit named as unopenable, with no path or error", w)
	}

	if err := os.Remove(path); err != nil { // the operator fixes it
		t.Fatal(err)
	}
	clock.Advance(AuditRetry - time.Second)
	if code := login(); code != 503 {
		t.Fatalf("sign-in before the retry interval: %d, want 503 (tried at most every %v)", code, AuditRetry)
	}
	clock.Advance(time.Second)
	if code := login(); code != 200 {
		t.Fatalf("sign-in after the audit can be opened: %d, want 200", code)
	}
	raw, err := os.ReadFile(path)
	if err != nil || !strings.Contains(string(raw), `"event":"login.ok"`) {
		t.Fatalf("the sign-in was not recorded: %v %s", err, raw)
	}
	if w := warning(); strings.Contains(w, "audit") {
		t.Fatalf("exposure warning %q still names the audit once it opened", w)
	}
}

// The audit's reason is added to a fallback's warning, not put in its
// place.
func TestAuditWarningKeepsFallback(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	if err := os.MkdirAll(path, 0o700); err != nil {
		t.Fatal(err)
	}
	a, _ := OpenAuditRetrying(path, nil)
	h := newHarness(t, Config{Listen: "127.0.0.1:0", Auth: "password", Password: "change-me"}, func(o *Options) { o.Audit = a })
	if h.plan.Fallback == "" {
		t.Fatalf("plan %+v: no fallback", h.plan)
	}
	info := readJSON[AuthInfo](t, h.do("GET", "/api/auth", "", nil))
	if info.Exposure == nil || info.Exposure.Warning == nil {
		t.Fatal("no exposure warning")
	}
	if w := *info.Exposure.Warning; !strings.Contains(w, h.plan.Fallback) || !strings.Contains(w, "no audit log") {
		t.Fatalf("warning %q: want the fallback and the audit both", w)
	}
}

// Wave 3 R4: a crash can leave a torn last line in the audit. The next
// record starts on a line of its own, so a JSON-lines reader loses only
// the torn line, never the record after it.
func TestAuditAfterTornLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "audit.jsonl")
	if err := os.WriteFile(path, []byte("garbage line\n{\"half\":"), 0o600); err != nil {
		t.Fatal(err)
	}
	for i := range 2 { // a second open finds the file whole: nothing added
		a, err := OpenAudit(path)
		if err != nil {
			t.Fatal(err)
		}
		if err := a.Event("login.ok", slog.Int("n", i)); err != nil {
			t.Fatal(err)
		}
		a.Close()
	}
	raw, _ := os.ReadFile(path)
	lines := strings.Split(strings.TrimSuffix(string(raw), "\n"), "\n")
	if len(lines) != 4 || lines[1] != `{"half":` {
		t.Fatalf("audit lines %q: want the garbage, the torn line, then two records", lines)
	}
	for _, l := range lines[2:] {
		var rec map[string]any
		if err := json.Unmarshal([]byte(l), &rec); err != nil || rec["event"] != "login.ok" {
			t.Fatalf("record %q after a torn line: %v", l, err)
		}
	}
}
