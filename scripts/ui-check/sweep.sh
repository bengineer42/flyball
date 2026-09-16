#!/usr/bin/env bash
# Full page sweep for one rig. Usage: sweep.sh <rig file> <api-port> <ui-port> [--record]
# Device-model routes/API (updated wave-3: pages are dashboards/overview/inputs/graph/
# controllers/devices/programs/events/sessions/simulation; controllers are named by their
# target's address, POST /api/controllers/{address}/regulate takes {"at": value}).
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

echo "--- controllers (for detail pages + fallback activity) ---"
controllers=$(curl -s "http://127.0.0.1:$api/api/controllers")
echo "$controllers" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  print([c.get('name') for c in d])
except Exception as e:
  print('ERR', e)
" 2>/dev/null
ctrlinfo=$(echo "$controllers" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  if d:
    c=d[0]
    print(c.get('name',''), c.get('source',''))
except Exception:
  pass
" 2>/dev/null)
ctrlname=$(echo "$ctrlinfo" | awk '{print $1}')
srcaddr=$(echo "$ctrlinfo" | awk '{print $2}')
echo "detail targets so far: controller='$ctrlname' source='$srcaddr'"

echo "--- devices (for the device detail page + input fallback) ---"
devices=$(curl -s "http://127.0.0.1:$api/api/devices")
devname=$(echo "$devices" | python3 -c "
import json,sys
try:
  d=json.load(sys.stdin)
  print(d[0]['name'] if d else '')
except Exception:
  pass
" 2>/dev/null)
if [ -z "$srcaddr" ]; then
  srcaddr=$(echo "$devices" | python3 -c "
import json,sys
def walk(nodes):
  for s in nodes:
    if 'signals' in s:
      yield from walk(s['signals'])
    elif 'P' in (s.get('access') or ''):
      yield s.get('address', '')
found = ''
try:
  d=json.load(sys.stdin)
  for dev in d:
    for addr in walk(dev.get('signals', [])):
      found = addr
      break
    if found:
      break
except Exception:
  pass
print(found)
" 2>/dev/null)
fi
echo "detail targets: controller='$ctrlname' source='$srcaddr' device='$devname'"

if [ -z "$ran" ] && [ -n "$ctrlname" ]; then
  echo "--- no program applies to this rig; regulating each controller directly for activity ---"
  schema=$(curl -s "http://127.0.0.1:$api/api/controllers/schema")
  echo "$controllers" | python3 -c "
import json,sys
try:
  ctrls=json.load(sys.stdin)
except Exception:
  ctrls=[]
schema=json.loads('''$schema''') if '''$schema''' else {}
ranges={s['address']: s.get('range') for s in schema.get('sources', [])}
for c in ctrls:
  lo, hi = (ranges.get(c.get('source')) or [0,1])[:2]
  target = lo + 0.6*(hi-lo)
  print(c.get('name',''), target)
" 2>/dev/null | while read -r cn tgt; do
    [ -z "$cn" ] && continue
    out=$(curl -s -X POST "http://127.0.0.1:$api/api/controllers/$cn/regulate" -H 'Content-Type: application/json' -d "{\"at\": $tgt}")
    echo "regulate $cn -> $tgt : $out"
  done
fi

echo "--- waiting 10s for program/regulation to produce activity ---"
sleep 10

shots="$S/shots"
mkdir -p "$shots"
run() { node "$TOOLS/shot.mjs" "$@"; }

pages=(dashboards overview inputs graph controllers devices programs events sessions simulation)

for page in "${pages[@]}"; do
  run "$base/#/$page" "$shots/sweep-$name-$page.png" --wait 3000
  run "$base/#/$page" "$shots/sweep-$name-$page-dark.png" --dark --wait 3000
  run "$base/#/$page" "$shots/sweep-$name-$page-400.png" --width 400 --height 800 --wait 3000
done

if [ -n "$srcaddr" ]; then
  run "$base/#/inputs/$srcaddr" "$shots/sweep-$name-input-detail.png" --wait 3000
else
  echo "no signal found for $name — skipping input detail"
fi
if [ -n "$devname" ]; then
  run "$base/#/devices/$devname" "$shots/sweep-$name-device-detail.png" --wait 3000
else
  echo "no device found for $name — skipping device detail"
fi
if [ -n "$ctrlname" ]; then
  run "$base/#/controllers/$ctrlname" "$shots/sweep-$name-controller-detail.png" --wait 3000
else
  echo "no controller found for $name — skipping controller detail"
fi

echo "=== done $name ==="
