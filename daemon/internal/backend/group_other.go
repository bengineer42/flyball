//go:build !unix

package backend

import "os/exec"

func ownGroup(*exec.Cmd) {}
