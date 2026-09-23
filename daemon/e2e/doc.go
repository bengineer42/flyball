// Package e2e is the real-process end-to-end test of the front: the built
// flyball and flyballd binaries, the real flyball-runner from engine/.venv,
// real unix sockets, TLS and a stand-in authenticating proxy. Its tests sit
// behind the e2e build tag:
//
//	cd daemon && UV_FROZEN=1 go test -tags e2e -race ./e2e/...
//
// README.md maps each Phase 1 merge requirement to the test that proves it.
package e2e
