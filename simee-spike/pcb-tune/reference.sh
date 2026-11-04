#!/bin/sh
# The spike on a real board (simee-kicad#46): gusmanb/logicanalyzer's original board (GPL-3.0, fetched,
# never committed), its 24 hand-matched /CHAN_* nets tuned to 52 mm as one group, then DRC with zones
# refilled and a 52 +- 0.1 mm length rule.
#   KC=<kicad-cli> ./reference.sh
set -eu
w=$(mktemp -d); cd "$w"
export KICAD_CONFIG_HOME=$w/cfg KICAD_DOCUMENTS_HOME=$w/docs KICAD_CACHE_HOME=$w/cache
curl -sfL -o la.kicad_pcb \
  https://raw.githubusercontent.com/gusmanb/logicanalyzer/fc05977ad4cca3fd813de80bf25d0a207402dffb/Electronics/LogicAnalyzer/LogicAnalyzer/LogicAnalyzer.kicad_pcb
nets=""; for i in $(seq 0 23); do nets="$nets --net /CHAN_$i"; done
# shellcheck disable=SC2086
"$KC" pcb tune-test $nets --length 52 -o t52.kicad_pcb la.kicad_pcb || true
echo '{}' > t52.kicad_pro
printf '(version 1)\n(rule "chan 52" (condition "A.NetName == '"'"'/CHAN_*'"'"'") (constraint length (min 51.9mm) (max 52.1mm)))\n' > t52.kicad_dru
for b in la t52; do
    [ -f $b.kicad_pro ] || echo '{}' > $b.kicad_pro
    "$KC" pcb drc --refill-zones --format json --severity-all -o $b.json $b.kicad_pcb >/dev/null 2>&1 || true
    python3 -c "import json,sys,collections; d=json.load(open(sys.argv[1])); v=d['violations']
print(sys.argv[1], dict(collections.Counter(x['type'] for x in v)), 'unconnected', len(d['unconnected_items']))
[print('  ', x['description']) for x in v if x['type'] == 'length_out_of_range']" $b.json
done
echo "boards in $w"
