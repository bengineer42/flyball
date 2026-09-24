//go:build !unix

package frontdir

import (
	"io/fs"
	"os"
)

// No uid to compare, and no flock: a Windows front-dir relies on its ACL
// and the runner's own <store>.lock.
func owner(fs.FileInfo) (int, bool) { return 0, false }

func lockHeld(*os.Root) (bool, error) { return false, nil }

func lockForWrite(*os.Root) (func(), bool, error) { return func() {}, false, nil }
