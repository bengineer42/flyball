#!/usr/bin/env bash
# Regenerates rig.schema.json from the Python package's own RigConfig model
# (engine/src/flyball/runtime/config.py's rig_schema()), the single source
# of truth for every driver/tag registered there. Run this after adding or
# changing a driver, then commit the result -- schema_stale_test.go fails
# CI if the checked-in copy drifts from what the Python side would emit.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_dir="$here/../../../engine"
out="$here/rig.schema.json"

if [ ! -d "$engine_dir" ]; then
	echo "regen.sh: expected engine/ at $engine_dir" >&2
	exit 1
fi

(cd "$engine_dir" && uv run flyball rig schema) > "$out"
