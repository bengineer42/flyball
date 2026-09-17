#!/usr/bin/env bash
# Regenerates program.schema.json from the Python package's own dialect
# module (engine/src/flyball/server/dialect.py's program_schema()), built
# from the static Commands registry (engine/src/flyball/programmer/command.py)
# -- populated by Command subclasses as their modules import, not by plugin
# discovery, so this is a genuinely static schema. Run this after adding or
# changing a built-in program command, then commit the result --
# program_schema_stale_test.go fails CI if the checked-in copy drifts.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_dir="$here/../../../engine"
out="$here/program.schema.json"

if [ ! -d "$engine_dir" ]; then
	echo "regen-program.sh: expected engine/ at $engine_dir" >&2
	exit 1
fi

(cd "$engine_dir" && uv run flyball program schema) > "$out"
