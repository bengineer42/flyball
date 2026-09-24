//go:build unix

package front

import (
	"os"
	"syscall"
)

// lockAudit holds an exclusive flock on the audit file f until the returned
// release. A front and `flyball token create` append to one audit.jsonl;
// the lock makes each record, and the torn-line check before the first, a
// unit the other writer cannot interleave with.
func lockAudit(f *os.File) func() {
	for {
		err := syscall.Flock(int(f.Fd()), syscall.LOCK_EX)
		if err != syscall.EINTR {
			break
		}
	}
	return func() { syscall.Flock(int(f.Fd()), syscall.LOCK_UN) }
}
