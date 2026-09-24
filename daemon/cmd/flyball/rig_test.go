package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// A board rig's pins are resolved by the runner, not by rig check: its
// refusal says so rather than leaving only a oneOf failure.
func TestRigCheckNamesTheBoardItDidNotApply(t *testing.T) {
	path := filepath.Join(t.TempDir(), "rig.yaml")
	rig := "board: rpi5\ndevices:\n  heater: {driver: pwm_channel, pin: PWM0}\n"
	if err := os.WriteFile(path, []byte(rig), 0o600); err != nil {
		t.Fatal(err)
	}
	err := runRigCheck([]string{path})
	if err == nil || !strings.Contains(err.Error(), `board "rpi5"`) {
		t.Errorf("got %v", err)
	}
}
