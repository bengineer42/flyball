#!/usr/bin/env bash
# Mid-run disturbance for chaos.yaml: there is no program step for "fail a
# reader" or "kick an actuator" (programmer/loops.py only has regulate,
# ramp, hold, arrive, manual, wait), so this drives the same HTTP routes the
# CLI and UI use, timed against chaos-run.yaml's schedule.
#
# Run the program first, then this script, against the same runner:
#   flyball program run programs/chaos-run.yaml &
#   ./scripts/chaos-disturb.sh
#
# chaos.yaml runs at clock speed 60, so its "hold: 5 minutes" step is about
# 5 real seconds long; this fires the disturbance inside that window.
set -euo pipefail

API="${1:-http://127.0.0.1:8000}"

echo "waiting for the ramp to saturate..."
sleep 1.5

echo "failing zone3's thermocouple (mid-hold)"
curl -sf -X POST "$API/api/devices/furnace/commands/fail" -H 'content-type: application/json' \
  -d '{"signal": "zone3"}' -o /dev/null

sleep 1.0

echo "kicking heater5 by -270 W, 30 % of its 900 W (a door opened, a leak)"
curl -sf -X POST "$API/api/devices/heaters/commands/disturb" -H 'content-type: application/json' \
  -d '{"signal": "heater5", "offset": -270}' -o /dev/null

sleep 1.0

echo "restoring zone3's thermocouple"
curl -sf -X POST "$API/api/devices/furnace/commands/restore" -H 'content-type: application/json' \
  -d '{"signal": "zone3"}' -o /dev/null

echo "done"
