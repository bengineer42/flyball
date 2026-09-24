#!/usr/bin/env bash
# Regenerates program.schema.json from the Python package's own dialect
# module (engine/src/flyball/server/dialect.py's program_schema()), built
# from the step catalog (engine/src/flyball/sequencing/step.py)
# -- populated by Step subclasses as their modules import, not by plugin
# discovery, so this is a genuinely static schema. Run this after adding or
# changing a built-in program step, then commit the result --
# program_schema_stale_test.go fails CI if the checked-in copy drifts.
#
# Calls program_schema() directly rather than through the old `flyball
# program schema` Python CLI command, which cli.py's removal deleted --
# this reproduces exactly what that command printed (Dialect(), the
# default dialect, json.dumps(..., indent=2)), confirmed against the
# removed cli.py's own source.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_dir="$here/../../../engine"
out="$here/program.schema.json"

if [ ! -d "$engine_dir" ]; then
	echo "regen-program.sh: expected engine/ at $engine_dir" >&2
	exit 1
fi

(cd "$engine_dir" && uv run python -c '
import json
from flyball.interfaces.server.dialect import Dialect, program_schema
from flyball.model.catalog import ensure_discovered
commands = dict(ensure_discovered().steps.items())
print(json.dumps(program_schema(Dialect(steps=commands)), indent=2))
') > "$out"
