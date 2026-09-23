#!/usr/bin/env bash
# Builds the dashboard UI and embeds it into the flyball binary
# (internal/webui, go:embed) -- a plain `go build ./cmd/flyball` embeds
# only the placeholder in internal/webui/dist/, since go:embed can't reach
# ui/apps/dashboard/dist from outside this module.
#
#   ./build-with-ui.sh [-o OUTPUT]
#
# Needs Node/npm (to build the UI) as well as Go -- run this somewhere that
# has both, not necessarily the machine that ends up running the binary
# (cross-compile with GOOS/GOARCH as usual; only `go build` itself cares
# about the target platform, npm run build's output isn't platform-specific).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

out=flyball
if [ "${1:-}" = "-o" ]; then out="$2"; fi

HASH_FILE=internal/webui/.ui-hash

# Everything that actually affects the built UI: source, lockfile, workspace
# configs, and the .mjs build scripts (third-party-licences.mjs writes into
# dist, so a change to it changes the output) -- not node_modules/dist, which
# are either huge or already the output. Same hash regardless of file order (sorted) or how many files
# (each file's own hash goes through a second sha256sum to collapse to one).
ui_hash() {
  find ../ui \( -name node_modules -o -name dist -o -name .vite \) -prune -o \
    -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.css' -o -name '*.html' -o -name 'package*.json' -o -name '*.yaml' -o -name '*.mjs' \) -print \
    | sort | xargs sha256sum | sha256sum | cut -d' ' -f1
}

new_hash=$(ui_hash)
if [ -f "$HASH_FILE" ] && [ "$(cat "$HASH_FILE")" = "$new_hash" ] && [ -s internal/webui/dist/index.html ]; then
  echo "==> UI unchanged since the last build-with-ui.sh run, skipping npm/vite"
else
  echo "==> building the UI"
  (cd ../ui && npm install && npm run build)
  echo "==> copying into internal/webui/dist"
  rm -rf internal/webui/dist
  mkdir -p internal/webui/dist
  cp -r ../ui/apps/dashboard/dist/. internal/webui/dist/
  echo "$new_hash" > "$HASH_FILE"
fi

echo "==> go build"
go build -o "$out" ./cmd/flyball

echo "==> built $out ($(du -h "$out" | cut -f1)) with the UI embedded"
echo "note: internal/webui/dist/ now holds the real build, not the placeholder --"
echo "don't commit it (git status will show .placeholder as deleted; that's expected,"
echo "git checkout -- internal/webui/dist/.placeholder restores it if you want a clean tree)."
