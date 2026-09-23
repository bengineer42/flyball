//go:build !unix

package front

import "os"

// lockAudit is a no-op where there is no flock (Windows, not yet a target):
// each record is still one append.
func lockAudit(*os.File) func() { return func() {} }
