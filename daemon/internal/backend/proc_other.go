//go:build !unix

package backend

import "os/exec"

func ownGroup(*exec.Cmd) {}

// No runner.lock to find a live runner by (frontdir's lockHeld is always
// false here), so nothing is ever adopted.
func processStart(int) string { return "" }

func processAlive(int, string) bool { return false }
