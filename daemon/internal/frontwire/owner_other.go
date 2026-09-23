//go:build !unix

package frontwire

import "io/fs"

// ownedByMe: no uid to compare here, so a passwd home is never taken
// (HOME -- USERPROFILE on Windows -- or XDG_STATE_HOME must say).
func ownedByMe(fs.FileInfo) bool { return false }
