package front

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"log/slog"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// Audit is the front's authentication log (auth.md § Audit): one JSON
// record per line, through log/slog's JSON handler, which escapes every
// control character a name could smuggle in (CWE-117, adv-blind 6). The
// file is 0600 in a 0700 directory, set by this code whatever the umask.
// Each record carries the wall time, a sequence number and the boot id.
//
// Authentication events fail closed: a login or a token change whose
// record cannot be written does not happen (Event's error). It is safe for
// concurrent use.
type Audit struct {
	mu   sync.Mutex
	f    *os.File
	h    slog.Handler
	seq  uint64
	boot string
	err  error // FailedAudit: every Event returns it
}

// FailedAudit is an audit log that could not be opened: the front still
// serves (D-028), but every Event returns err, so the events that fail
// closed (a sign-in, a token created or revoked) answer 503 as when a
// single write fails.
func FailedAudit(err error) *Audit { return &Audit{err: err} }

// OpenAudit opens (creating) the audit file at path, e.g.
// `<data_dir>/front/audit.jsonl` or
// `$XDG_STATE_HOME/flyball/front-<id>/audit.jsonl`.
func OpenAudit(path string) (*Audit, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, fmt.Errorf("audit: %w", err)
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_APPEND|os.O_CREATE, 0o600)
	if err != nil {
		return nil, fmt.Errorf("audit: %w", err)
	}
	if err := f.Chmod(0o600); err != nil {
		f.Close()
		return nil, fmt.Errorf("audit: %w", err)
	}
	boot := make([]byte, 8)
	rand.Read(boot)
	return &Audit{f: f, h: slog.NewJSONHandler(f, &slog.HandlerOptions{
		ReplaceAttr: func(groups []string, a slog.Attr) slog.Attr {
			if len(groups) == 0 && (a.Key == slog.LevelKey || a.Key == slog.MessageKey) {
				return slog.Attr{}
			}
			return a
		},
	}), boot: hex.EncodeToString(boot)}, nil
}

// Event writes one record: event, then attrs. A nil Audit writes nothing.
func (a *Audit) Event(event string, attrs ...slog.Attr) error {
	if a == nil {
		return nil
	}
	if a.err != nil {
		return a.err
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	a.seq++
	rec := slog.NewRecord(time.Now(), slog.LevelInfo, "", 0)
	rec.AddAttrs(slog.String("event", event), slog.Uint64("seq", a.seq), slog.String("boot", a.boot))
	rec.AddAttrs(attrs...)
	if err := a.h.Handle(context.Background(), rec); err != nil {
		return fmt.Errorf("audit: %w", err)
	}
	return nil
}

// Close closes the file.
func (a *Audit) Close() error {
	if a == nil || a.f == nil {
		return nil
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	return a.f.Close()
}
