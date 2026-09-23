package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// extendsTestHash is a real $scrypt$ line (the one local_test.go checks).
const extendsTestHash = "$scrypt$n=16384,r=8,p=1$36M9tNDQnT4LXnO_XFWyaA$H_N0KkLlVTY4PF16x5xY16V42DHL6cAnJKuvBxBjFRjuHTForWqlYIRGmlH0_9ehZ_2RmUJWhS_k8bCiO0V6eg"

// authAt waits (up to 10 s) for GET http://addr/api/auth and decodes it.
func authAt(t *testing.T, r *fakeRun, addr string) map[string]any {
	t.Helper()
	var last string
	for end := time.Now().Add(10 * time.Second); time.Now().Before(end); time.Sleep(50 * time.Millisecond) {
		resp, err := testClient.Get("http://" + addr + "/api/auth")
		if err != nil {
			last = err.Error()
			continue
		}
		var v map[string]any
		err = json.NewDecoder(resp.Body).Decode(&v)
		resp.Body.Close()
		if resp.StatusCode == 200 && err == nil {
			return v
		}
		last = resp.Status
	}
	t.Fatalf("no /api/auth at %s (last: %s); output:\n%s", addr, last, r.output())
	return nil
}

// A runner file that `extends` the rig and carries runner.front (the
// split-config pattern, 2-config/runner.md § A runner file) gets the door
// it asks for, whether its extends paths are relative or absolute -- the
// runner resolves both (Path.parent / name), and so must the front.
func TestRunExtendsFileKeepsItsFront(t *testing.T) {
	baseDir := t.TempDir()
	base := filepath.Join(baseDir, "oven.yaml")
	if err := os.WriteFile(base, []byte("name: oven\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	front := "runner:\n  front:\n    listen: 127.0.0.1:0\n    auth: password\n    password: \"" + extendsTestHash + "\"\n    anonymous: read\n"
	for name, extends := range map[string]string{
		"absolute": base,
		"relative": "", // filled in below: relative to the invocation file
	} {
		t.Run(name, func(t *testing.T) {
			dir := fakeEnv(t)
			if extends == "" {
				rel, err := filepath.Rel(dir, base)
				if err != nil {
					t.Fatal(err)
				}
				extends = rel
			}
			r := startRunIn(t, dir, "extends: ["+extends+"]\n"+front)
			auth := authAt(t, r, r.addr())
			if auth["shape"] != "password" {
				t.Fatalf("/api/auth shape = %v, want password; output:\n%s", auth["shape"], r.output())
			}
			if strings.Contains(strings.Join(anyStrings(auth["verbs"]), ","), "operate") {
				t.Fatalf("anonymous verbs = %v: an extends file's password front must not be open", auth["verbs"])
			}
		})
	}
}

// A runner file whose extends cannot be resolved (a missing parent) is
// never a silent open front: the front falls back with the D-028 banner,
// and /api/auth says why.
func TestRunBrokenExtendsIsNotSilentlyOpen(t *testing.T) {
	dir := fakeEnv(t)
	r := startRunIn(t, dir, "extends: [missing.yaml]\nrunner:\n  front:\n    listen: 127.0.0.1:0\n    auth: password\n    password: \""+extendsTestHash+"\"\n", "--listen", "127.0.0.1:0")
	auth := authAt(t, r, r.addr())
	if !strings.Contains(r.output(), "D-028") || !strings.Contains(r.output(), "missing.yaml") {
		t.Fatalf("no fallback banner naming the parent; output:\n%s", r.output())
	}
	exposure, _ := auth["exposure"].(map[string]any)
	warning, _ := exposure["warning"].(string)
	if !strings.Contains(warning, "missing.yaml") {
		t.Fatalf("/api/auth exposure = %v, want a warning naming the parent", auth["exposure"])
	}
	matches, _ := filepath.Glob(filepath.Join(dir, "state", "flyball", "front-*", "audit.jsonl"))
	if len(matches) != 1 {
		t.Fatalf("audit files: %v", matches)
	}
	if b, _ := os.ReadFile(matches[0]); !strings.Contains(string(b), "fallback") {
		t.Fatalf("no fallback record in the audit: %s", b)
	}
}

func anyStrings(v any) []string {
	list, _ := v.([]any)
	out := make([]string, 0, len(list))
	for _, x := range list {
		s, _ := x.(string)
		out = append(out, s)
	}
	return out
}

// startRunIn is startRun with dir already made by fakeEnv.
func startRunIn(t *testing.T, dir, rigYAML string, flags ...string) *fakeRun {
	t.Helper()
	rig := filepath.Join(dir, "rig.yaml")
	if err := os.WriteFile(rig, []byte(rigYAML), 0o644); err != nil {
		t.Fatal(err)
	}
	r := &fakeRun{t: t, dir: dir, marker: filepath.Join(dir, "stopped"), args: filepath.Join(dir, "args"),
		sigs: make(chan os.Signal, 2), done: make(chan error, 1), addrs: make(chan string, 4)}
	runOut = r
	r.listen()
	go func() { r.done <- run(append([]string{rig}, flags...), r.sigs) }()
	t.Cleanup(func() { r.stop() })
	return r
}
