package main

import (
	"strings"
	"testing"
	"time"
)

// In the D-028 refused state the requested address answers 503, so a
// bare `flyball stop` (which goes there) cannot stop the rig: the banner
// names a stop that does, with this run's own front-dir.
func TestRunRefusedBannerNamesAWorkingStop(t *testing.T) {
	r := startRun(t, "name: t\nrunner:\n  front:\n    listen: 127.0.0.1:0\n    auth: password\n    password: hunter2\n")
	echoAt(t, r, r.addr(), nil)
	var dir string
	for end := time.Now().Add(5 * time.Second); dir == "" && time.Now().Before(end); time.Sleep(20 * time.Millisecond) {
		if lines := readLines(r.args); len(lines) > 0 {
			f := strings.Fields(lines[0])
			for i, a := range f {
				if a == "--front-dir" && i+1 < len(f) {
					dir = f[i+1]
				}
			}
		}
	}
	out := r.output()
	if dir == "" || !strings.Contains(out, "`flyball stop --front-dir "+dir+"`") {
		t.Fatalf("the banner does not name `flyball stop --front-dir %s`:\n%s", dir, out)
	}
	if strings.Contains(out, "Ctrl-C or `flyball stop` does") {
		t.Fatalf("the banner still offers a bare `flyball stop`:\n%s", out)
	}
}
