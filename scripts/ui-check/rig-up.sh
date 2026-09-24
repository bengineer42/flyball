#!/usr/bin/env bash
# Start a runner for a rig plus a Vite dev server pointed at it.
#   rig-up.sh <rig file> <api-port> <ui-port> [extra runner args…]
# Logs: $S/logs/<name>-runner.log and -vite.log. PIDs in $S/logs/<name>.pids. Store in $S/stores/<name>.sqlite.
# Stop with: rig-down.sh <name>
set -u
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
S=${FLYBALL_CHECK_DIR:-/tmp/flyball-check}; mkdir -p "$S/logs" "$S/stores" "$S/shots"
rig=$(realpath "$1"); api=$2; ui=$3; shift 3
name=$(basename "$rig" | sed 's/\.[a-z]*$//')
cd "$ROOT/engine"
setsid nohup uv run --extra server --extra cli flyball-runner "$rig" --port "$api" --store "$S/stores/$name.sqlite" "$@" > "$S/logs/$name-runner.log" 2>&1 &
echo $! > "$S/logs/$name.pids"
cd "$ROOT/ui"
FLYBALL_URL="http://127.0.0.1:$api" setsid nohup npx vite --config apps/dashboard/vite.config.ts apps/dashboard --port "$ui" --strictPort > "$S/logs/$name-vite.log" 2>&1 &
echo $! >> "$S/logs/$name.pids"
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$api/api/auth" >/dev/null && break; sleep 0.5; done
curl -sf "http://127.0.0.1:$api/api/auth" >/dev/null && echo "runner $name up on :$api" || { echo "runner $name FAILED — see $S/logs/$name-runner.log"; tail -20 "$S/logs/$name-runner.log"; }
for i in $(seq 1 40); do curl -sf "http://127.0.0.1:$ui/" >/dev/null && break; sleep 0.5; done
curl -sf "http://127.0.0.1:$ui/" >/dev/null && echo "ui $name up on http://127.0.0.1:$ui" || echo "vite $name FAILED — see $S/logs/$name-vite.log"
