//go:build !unix

package backend

import (
	"os"
	"os/exec"
)

func ownGroup(*exec.Cmd) {}

func killGroup(p *os.Process) error { return p.Kill() }

// No runner.lock to find a live runner by (frontdir's lockHeld is always
// false here), so nothing is ever adopted.
func processStart(int) string { return "" }

func processAlive(int, string) bool { return false }
