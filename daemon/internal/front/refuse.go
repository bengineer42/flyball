package front

import "net/http"

// holdRefused answers 503 on Plan.Refused, the address a credential shape
// asked for before its D-028 fallback: a reverse proxy still pointed there
// gets "authentication is misconfigured", not the open local shape, which
// is on Plan.Listen instead. The body is generic (D-028, amended): whoever
// reaches this address may be anyone the proxy serves, and a reason can
// name paths or quote the file (a YAML type error quotes the value), so it
// says only where the reason is -- the banner, the log, the audit and
// /api/auth keep it. An address that cannot be held is logged; the rig is
// served either way.
func (f *Front) holdRefused() {
	if f.plan.Refused == "" {
		return
	}
	at := Plan{Listen: f.plan.Refused, TLS: f.plan.RefusedTLS}
	ln, err := Listen(at)
	if err != nil {
		f.log.Error("front: cannot hold the requested address to answer 503 on it", "listen", f.plan.Refused, "err", err)
		return
	}
	// `flyball stop` prints this body, and a stop cannot come through here,
	// so the body leads with the ways that can. Stopping flyballd is not
	// one: it leaves its runners, and their rigs, running (D-037).
	const msg = "flyball: to stop this rig, press Ctrl-C in its `flyball run` terminal, or on its host run" +
		" `flyball stop --front-dir DIR` (DIR: the front-dir `flyball run` printed; under flyballd's systemd unit," +
		" /run/flyball/NAME, as flyballd's user) or `flyball stop --pid N`. Stopping flyballd leaves the rig" +
		" running (D-037); `flyball runners stop NAME`, with a manage token and FLYBALLD_URL set to the local" +
		" address flyballd's log names, ends its runner. This front's authentication is misconfigured, so nothing" +
		" is served here until it is fixed; the reason is in the `flyball run` terminal and its run.log, or in" +
		" `journalctl -u flyballd`. The rig keeps running (D-028)."
	f.refused = NewServer(at, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Cache-Control", "no-store")
		plainError(w, http.StatusServiceUnavailable, msg)
	}))
	go f.refused.Serve(ln)
}
