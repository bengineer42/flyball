//go:build !unix

package front

// checkSocketDir checks nothing where there are no unix permissions.
func checkSocketDir(string) error { return nil }
