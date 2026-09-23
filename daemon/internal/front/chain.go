package front

import "net/http"

// Outcome is a provider's answer about one request (F10: tri-state).
type Outcome int

const (
	// NotMine: the request carries nothing this provider reads; the chain
	// asks the next one.
	NotMine Outcome = iota
	// Accept: the request is this identity.
	Accept
	// Reject: the request presented this provider's credential and it is
	// not valid. Terminal: 401, never anonymous.
	Reject
)

// Identity is who a provider says the caller is.
type Identity struct {
	Issuer, Subject, Name string // sub = <provider>:<issuer>#<subject>, or <provider>:<subject>
	Groups                []string
	Kind                  string // human | service | agent; "" is human
	Sid                   string
	Scopes                []string // tokens only: the ceiling
}

// Client is one provider in the chain: bearer token → session cookie →
// proxy assertion → anonymous. The first two are the front's own; the
// proxy shape's comes from a ProxyFactory (C4's presets).
type Client interface {
	Name() string
	// Authenticate: Reject, or a non-nil error, is terminal -- 401 for
	// Reject, 503 for an error (a store or JWKS failure), never anonymous.
	Authenticate(r *http.Request) (Identity, Outcome, error)
}
