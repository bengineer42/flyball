//go:build !unix

package store

// lockFile is a no-op where there is no flock (Windows, not yet a target):
// a front and a `flyball token` writing the file at the same moment could
// lose one update there. Each write is still atomic.
func lockFile(string) (func(), error) { return func() {}, nil }
