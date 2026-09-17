#!/usr/bin/env bash
# Regenerates rig.schema.json from the Python package's own RigConfig model
# (engine/src/flyball/runtime/config.py's rig_schema()), the single source
# of truth for every driver/tag registered there. Run this after adding or
# changing a driver, then commit the result -- schema_stale_test.go fails
# CI if the checked-in copy drifts from what the Python side would emit.
#
# Calls rig_schema() directly rather than through the old `flyball rig
# schema` Python CLI command, which cli.py's removal deleted -- this
# reproduces exactly what that command printed (json.dumps(..., indent=2),
# no --json flag), confirmed against the removed cli.py's own source.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_dir="$here/../../../engine"
out="$here/rig.schema.json"

if [ ! -d "$engine_dir" ]; then
	echo "regen.sh: expected engine/ at $engine_dir" >&2
	exit 1
fi

(cd "$engine_dir" && uv run python -c '
import json
from flyball.runtime.config import rig_schema
print(json.dumps(rig_schema(), indent=2))
') > "$out"
