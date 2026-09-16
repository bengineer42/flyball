#!/usr/bin/env bash
# Kill the process groups started by rig-up.sh (daemon + npm/vite wrapper and its children).
S=${FLYBALL_CHECK_DIR:-/tmp/flyball-check}; mkdir -p "$S/logs" "$S/stores" "$S/shots"
name=$1
if [ -f "$S/logs/$name.pids" ]; then
  for pid in $(cat "$S/logs/$name.pids"); do kill -- -"$pid" 2>/dev/null || kill "$pid" 2>/dev/null; done
  sleep 1
  for pid in $(cat "$S/logs/$name.pids"); do kill -9 -- -"$pid" 2>/dev/null; done
  rm -f "$S/logs/$name.pids"; echo "stopped $name"
else echo "no pids for $name"; fi
