#!/usr/bin/env bash
# Regenerates rig.schema.json from the Python package's own RigConfig model
# (engine/src/flyball/runtime/config.py's rig_schema()), which holds every
# driver and link tag installed where it runs. Run this after adding or
# changing a driver, then commit the result -- staleness_test.go runs this
# same script and fails if the checked-in copy drifts.
#
# rig_schema() only knows the tags of packages installed beside it. The
# engine's dev venv has the sim, modbus, visa and furnace packages; every
# other first-party package that registers tags (`flyball.configs` entry
# point) is added here with --with-editable, so the schema -- and so
# `flyball rig check` -- accepts chip, linux and instrument-adapter rigs.
# A new extension with tags goes in this list.
#
# Usage: regen.sh [OUT]   (default: rig.schema.json beside this script)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
engine_dir="$here/../../../engine"
out="${1:-$here/rig.schema.json}"

if [ ! -d "$engine_dir" ]; then
	echo "regen.sh: expected engine/ at $engine_dir" >&2
	exit 1
fi

(cd "$engine_dir" && uv run --frozen --quiet \
	--with-editable ../extensions/chips \
	--with-editable ../extensions/linux \
	--with-editable ../extensions/qcodes \
	--with-editable ../extensions/pymeasure \
	python -c '
import json
from flyball.runtime.config import rig_schema
print(json.dumps(rig_schema(), indent=2))
') > "$out"
