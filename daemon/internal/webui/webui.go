// Package webui embeds the dashboard's built static files into the flyball
// binary. go:embed can only reach files inside this module (daemon/), so
// ../../build-with-ui.sh builds ui/apps/dashboard and copies its dist/
// here before `go build` runs -- a plain `go build ./cmd/flyball` embeds
// only the placeholder checked into dist/, since dist/ would otherwise be
// empty and go:embed errors on a pattern matching zero files.
package webui

import "embed"

//go:embed all:dist
var Dist embed.FS
