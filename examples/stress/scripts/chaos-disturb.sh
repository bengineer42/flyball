#!/usr/bin/env bash
# Mid-run disturbance for chaos.toml: there is no program step for "fail a
# reader" or "kick an actuator" (programmer/loops.py only has regulate,
# ramp, hold, arrive, manual, wait), so this drives the same HTTP routes the
# CLI and UI use, timed against chaos-run.yaml's schedule.
#
# Run the program first, then this script, against the same daemon:
#   flyball program run programs/chaos-run.yaml &
#   ./scripts/chaos-disturb.sh
#
# chaos.toml runs at clock speed 60, so its "hold: 5 minutes" step is about
# 5 real seconds long; this fires the disturbance inside that window.
set -euo pipefail

API="${1:-http://127.0.0.1:8000}"

echo "waiting for the ramp to saturate..."
sleep 1.5

echo "failing zone3's thermocouple (mid-hold)"
curl -sf -X POST "$API/api/readers/zone3/fail" -o /dev/null

sleep 1.0

echo "kicking heater5's plant input by -0.3 (a door opened, a leak)"
curl -sf -X POST "$API/api/actuators/heater5/disturb" -H 'content-type: application/json' \
  -d '{"offset": -0.3}' -o /dev/null

sleep 1.0

echo "restoring zone3's thermocouple"
curl -sf -X POST "$API/api/readers/zone3/restore" -o /dev/null

echo "done"
