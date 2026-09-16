#!/usr/bin/env bash
# Full page sweep for one rig. Usage: sweep.sh <rig.toml> <api-port> <ui-port> [--record]
set -u
S=${FLYBALL_CHECK_DIR:-/tmp/flyball-check}; mkdir -p "$S/logs" "$S/stores" "$S/shots"
TOOLS="$(cd "$(dirname "$0")" && pwd)"
rig=$(realpath "$1"); api=$2; ui=$3; shift 3
extra=("$@")
name=$(basename "$rig" | sed 's/\.[a-z]*$//')
base="http://127.0.0.1:$ui"

echo "=== starting $name (api $api, ui $ui) ==="
bash "$TOOLS/rig-up.sh" "$rig" "$api" "$ui" "${extra[@]}"
sleep 1

echo "--- programs library ---"
progs=$(curl -s "http://127.0.0.1:$api/api/programs/library")
prognames=$(echo "$progs" | python3 -c "import json,sys
try:
  d=json.load(sys.stdin)
  print('\n'.join(p['name'] for p in d) if isinstance(d,list) else '')
except Exception:
  pass" 2>/dev/null)
echo "programs: $(echo "$prognames" | tr '\n' ' ')"
ran=""
if [ -n "$prognames" ]; then
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    resp=$(curl -s -X POST "http://127.0.0.1:$api/api/programs/library/$p/run")
    if echo "$resp" | grep -q '"detail"'; then
      echo "program $p failed: $resp"
    else
      echo "program $p started ok"
      ran="$p"
      break
    fi
  done <<< "$prognames"
fi
[ -z "$ran" ] && echo "no program ran successfully"

echo "--- loops (for detail pages + fallback activity) ---"
loops=$(curl -s "http://127.0.0.1:$api/api/loops")
echo "$loops" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  print([l.get('name') for l in d])
except Exception as e:
  print('ERR', e)
" 2>/dev/null
loopinfo=$(echo "$loops" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  if d:
    l=d[0]
    ch=l.get('channel') or {}
    print(l.get('name',''), ch.get('source',''), ch.get('measurand',''))
except Exception:
  pass
" 2>/dev/null)
loopname=$(echo "$loopinfo" | awk '{print $1}')
srcname=$(echo "$loopinfo" | awk '{print $2}')
measurand=$(echo "$loopinfo" | awk '{print $3}')
echo "detail targets: loop='$loopname' source='$srcname' measurand='$measurand'"

if [ -z "$ran" ]; then
  echo "--- no program applies to this rig's loops; regulating each loop directly for activity ---"
  echo "$loops" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  for l in d:
    ch = l.get('channel') or {}
    lo,hi = (ch.get('range') or [0,1])[:2]
    target = lo + 0.6*(hi-lo)
    print(l.get('name',''), target)
except Exception:
  pass
" 2>/dev/null | while read -r ln tgt; do
    [ -z "$ln" ] && continue
    out=$(curl -s -X POST "http://127.0.0.1:$api/api/loops/$ln/regulate" -H 'Content-Type: application/json' -d "{\"at\": $tgt}")
    echo "regulate $ln -> $tgt : $out"
  done
fi

echo "--- waiting 10s for program to run ---"
sleep 10

shots="$S/shots"
mkdir -p "$shots"
run() { node "$TOOLS/shot.mjs" "$@"; }

for page in "" dashboards sources actuators loops programs events sessions simulation; do
  if [ -z "$page" ]; then out="$shots/sweep-$name-home.png"; url="$base/#/"; else out="$shots/sweep-$name-$page.png"; url="$base/#/$page"; fi
  run "$url" "$out" --wait 3000
done

run "$base/#/" "$shots/sweep-$name-home-dark.png" --dark --wait 3000
run "$base/#/dashboards" "$shots/sweep-$name-dashboards-dark.png" --dark --wait 3000
run "$base/#/" "$shots/sweep-$name-home-400.png" --width 400 --height 800 --wait 3000
run "$base/#/dashboards" "$shots/sweep-$name-dashboards-400.png" --width 400 --height 800 --wait 3000

if [ -n "$srcname" ] && [ -n "$measurand" ]; then
  run "$base/#/sources/$srcname/$measurand" "$shots/sweep-$name-source-detail.png" --wait 3000
else
  echo "no source/measurand found for $name — skipping source detail"
fi
if [ -n "$loopname" ]; then
  run "$base/#/loops/$loopname" "$shots/sweep-$name-loop-detail.png" --wait 3000
else
  echo "no loop found for $name — skipping loop detail"
fi

echo "=== done $name ==="
