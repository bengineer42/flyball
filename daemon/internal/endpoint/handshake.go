package endpoint

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"syscall"
	"time"
)

// ErrNotListening: nothing answers at the endpoint yet -- the socket file
// does not exist (ENOENT) or nobody accepts on it (ECONNREFUSED). A
// runner that is still starting looks exactly like this.
var ErrNotListening = errors.New("endpoint: nothing listening yet")

// Signer adds a signed principal to a readiness probe, minted with the
// runner's current key for aud. The front supplies it (principal.Mint;
// the probe's claims are the front's own business, e.g. sub
// "local:front", kind "service", no scopes).
type Signer func(r *http.Request, key [32]byte, aud string) error

// FrontInfo is a fronted runner's answer to a signed GET
// <root>/api/auth/front.
type FrontInfo struct {
	Protocol int    `json:"protocol"`
	Aud      string `json:"aud"`
	Pid      int    `json:"pid"`
	Flyball  string `json:"flyball"`
}

// Protocol is the front <-> runner protocol this front speaks.
const Protocol = 1

// ProbeTimeout bounds each of the handshake's two requests.
const ProbeTimeout = 2 * time.Second

// Handshake decides whether the runner at e is ready to be proxied to.
// Two probes of GET <rootPath>/api/auth/front:
//
//  1. unsigned, which must get 401: the runner enforces the principal;
//  2. signed with key for aud, which must get 200 {"protocol": 1, "aud": aud}.
//
// A nil error means ready. An error wrapping ErrNotListening means
// "still starting"; any other error means the runner answered, but not as
// a fronted runner of this front (an old runner, the wrong key or aud).
func Handshake(ctx context.Context, e Endpoint, rootPath, aud string, key [32]byte, sign Signer) (FrontInfo, error) {
	tr := e.Transport()
	tr.DisableKeepAlives = true
	defer tr.CloseIdleConnections()
	c := &http.Client{Transport: tr, Timeout: ProbeTimeout}
	url := e.URL(rootPath) + "/api/auth/front"

	code, _, err := get(ctx, c, url, nil)
	if err != nil {
		return FrontInfo{}, err
	}
	if code != http.StatusUnauthorized {
		return FrontInfo{}, fmt.Errorf("runner at %s: an unsigned GET %s/api/auth/front answered %d, not 401: it does not enforce the principal (a runner too old for this front?)", e, rootPath, code)
	}
	if sign == nil {
		return FrontInfo{}, fmt.Errorf("runner at %s: no signer, so the signed probe cannot be made", e)
	}
	code, body, err := get(ctx, c, url, func(r *http.Request) error { return sign(r, key, aud) })
	if err != nil {
		return FrontInfo{}, err
	}
	if code != http.StatusOK {
		return FrontInfo{}, fmt.Errorf("runner at %s: a signed GET %s/api/auth/front answered %d, not 200", e, rootPath, code)
	}
	var info FrontInfo
	if err := json.Unmarshal(body, &info); err != nil {
		return FrontInfo{}, fmt.Errorf("runner at %s: GET %s/api/auth/front: %v", e, rootPath, err)
	}
	if info.Protocol != Protocol {
		return info, fmt.Errorf("runner at %s speaks front protocol %d; this front speaks %d", e, info.Protocol, Protocol)
	}
	if info.Aud != aud {
		return info, fmt.Errorf("runner at %s answers as aud %q, not %q", e, info.Aud, aud)
	}
	return info, nil
}

func get(ctx context.Context, c *http.Client, url string, sign func(*http.Request) error) (int, []byte, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0, nil, err
	}
	if sign != nil {
		if err := sign(req); err != nil {
			return 0, nil, fmt.Errorf("signing the probe: %w", err)
		}
	}
	resp, err := c.Do(req)
	if err != nil {
		if NotListening(err) {
			return 0, nil, fmt.Errorf("%w (%v)", ErrNotListening, err)
		}
		return 0, nil, err
	}
	defer resp.Body.Close()
	body, err := io.ReadAll(io.LimitReader(resp.Body, 64<<10))
	return resp.StatusCode, body, err
}

// NotListening says whether a dial error means nothing is listening yet:
// ECONNREFUSED, or ENOENT for a socket file not yet created.
func NotListening(err error) bool {
	return errors.Is(err, syscall.ECONNREFUSED) || errors.Is(err, syscall.ENOENT) || errors.Is(err, os.ErrNotExist)
}
