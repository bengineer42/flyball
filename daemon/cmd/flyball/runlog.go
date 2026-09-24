package main

import (
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
)

// runLogCap is how big run.log grows before it is rotated to run.log.1
// (overwriting any previous one): a simple cap, not a dated history.
const runLogCap = 4 << 20 // 4 MiB

// runLog is `flyball run`'s own output and the runner's, also kept at
// <state dir>/run.log (D-038): the terminal goes away on a hangup, the
// log does not. The directory is 0700, the file 0600, both set here
// whatever the umask; it rotates once to run.log.1 past runLogCap.
type runLog struct {
	mu   sync.Mutex
	f    *os.File
	path string
	size int64
}

// openRunLog creates (or appends to) dir/run.log, making dir 0700 first.
func openRunLog(dir string) (*runLog, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, fmt.Errorf("run.log: %w", err)
	}
	path := filepath.Join(dir, "run.log")
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return nil, fmt.Errorf("run.log: %w", err)
	}
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		return nil, fmt.Errorf("run.log: %w", err)
	}
	size := int64(0)
	if fi, err := f.Stat(); err == nil {
		size = fi.Size()
	}
	return &runLog{f: f, path: path, size: size}, nil
}

// tee returns a writer that copies every write to term (the terminal:
// os.Stdout, os.Stderr, or a test's buffer) and to the log file. A write
// error on term -- a dead tty (EIO) after the terminal has gone -- is
// dropped, not returned: a caller piping the runner's stdout/stderr
// through this (exec.Cmd's internal io.Copy) must keep draining rather
// than stopping or wedging on it (D-038).
func (l *runLog) tee(term io.Writer) io.Writer {
	return &teeWriter{log: l, term: term}
}

type teeWriter struct {
	log  *runLog
	term io.Writer
}

func (w *teeWriter) Write(p []byte) (int, error) {
	if w.term != nil {
		_, _ = w.term.Write(p) // best-effort: a dead tty is not this write's problem
	}
	w.log.write(p)
	return len(p), nil
}

// write appends p to the file, rotating first if it would push size past
// runLogCap. A write or rotation failure is dropped (best-effort, like
// term's): a full disk must not stop the rig either.
func (l *runLog) write(p []byte) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f == nil {
		return
	}
	if l.size+int64(len(p)) > runLogCap {
		l.rotate()
	}
	if l.f == nil {
		return
	}
	n, _ := l.f.Write(p)
	l.size += int64(n)
}

// rotate replaces run.log with a fresh, empty file, keeping the old
// content at run.log.1 (overwriting any earlier one). l.mu held.
func (l *runLog) rotate() {
	l.f.Close()
	os.Rename(l.path, l.path+".1")
	f, err := os.OpenFile(l.path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		l.f = nil
		return
	}
	_ = f.Chmod(0o600)
	l.f, l.size = f, 0
}

// Close closes the file. Safe to call once.
func (l *runLog) Close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.f == nil {
		return nil
	}
	err := l.f.Close()
	l.f = nil
	return err
}
