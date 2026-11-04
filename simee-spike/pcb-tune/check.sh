#!/bin/sh
# Spike checks for kicad-cli pcb tune-test (simee-kicad#46): tune, then KiCad's DRC judges the result.
#   KC=<kicad-cli> ./check.sh
set -u
here=$(cd "$(dirname "$0")" && pwd)
w=$(mktemp -d); cd "$w"
export KICAD_CONFIG_HOME=$w/cfg KICAD_DOCUMENTS_HOME=$w/docs KICAD_CACHE_HOME=$w/cache
fail=0

mk() { name=$1; shift
    python3 "$here/make_board.py" "$name.kicad_pcb"; echo '{}' > "$name.kicad_pro"
    printf '(version 1)\n%s\n' "$*" > "$name.kicad_dru"; }
drc() {  # drc <board>: the DRC violation types, footprint library ones left out (the fixture has no library)
    "$KC" pcb drc --format json --severity-all -o "$1.drc.json" "$1.kicad_pcb" >/dev/null 2>&1
    python3 -c "import json,sys; d=json.load(open(sys.argv[1]))
v=sorted(x['type'] for x in d['violations'] if x['type']!='lib_footprint_issues')
print(' '.join(v + ['unconnected']*len(d['unconnected_items'])) or 'clean')" "$1.drc.json"; }
expect() { if [ "$2" = "$3" ]; then echo "  ok: $1: $3"; else echo "  FAIL: $1: got '$3', want '$2'"; fail=1; fi; }

LEN_A='(rule "SIG_A 45" (condition "A.NetName == '"'"'SIG_A'"'"'") (constraint length (min 44.9mm) (max 45.1mm)))'
SKEW='(rule "A-B skew" (condition "A.NetName == '"'"'SIG_A'"'"' || A.NetName == '"'"'SIG_B'"'"'") (constraint skew (max 0.2mm)))'

echo "1. one net to a given length, beside an obstacle 1.2 mm away"
mk one "$LEN_A"
expect "before" "length_out_of_range" "$(drc one)"
"$KC" pcb tune-test --net SIG_A --length 45 one.kicad_pcb || fail=1
expect "after" "clean" "$(drc one)"
grep -q '(type tuning_pattern)' one.kicad_pcb && echo "  ok: a tuning_pattern generator is saved" || { echo "  FAIL: no tuning pattern"; fail=1; }

LEN_OPT='(rule "SIG_A 45" (condition "A.NetName == '"'"'SIG_A'"'"'") (constraint length (min 44.9mm) (opt 45mm) (max 45.1mm)))'
echo "1b. the saved pattern reloads and retunes (pcbnew's edit path), up and down"
for len in 47 43; do
    printf '(version 1)\n(rule "SIG_A" (condition "A.NetName == '"'"'SIG_A'"'"'") (constraint length (min %s.9mm) (max %s.1mm)))\n' $((len-1)) $len > one.kicad_dru
    "$KC" pcb tune-test --net SIG_A --length $len one.kicad_pcb || fail=1
    expect "after $len mm" "clean" "$(drc one)"
    expect "patterns on the board" "1" "$(grep -c '(type tuning_pattern)' one.kicad_pcb)"
done

echo "2. one net to the board's own length rule (no --length): min/max only, then with opt"
mk rule "$LEN_A"
"$KC" pcb tune-test --net SIG_A rule.kicad_pcb; echo "  exit $? (KiCad's tuner aims at the rule's min when it names no opt)"
echo "   ... DRC after: $(drc rule)"
mk rule "$LEN_OPT"
"$KC" pcb tune-test --net SIG_A rule.kicad_pcb || fail=1
expect "after" "clean" "$(drc rule)"

echo "3. a two-net skew group: the shorter net matched to the longer"
mk skew "$SKEW"
expect "before" "skew_out_of_range" "$(drc skew)"
"$KC" pcb tune-test --net SIG_A --net SIG_B skew.kicad_pcb || fail=1
expect "after" "clean" "$(drc skew)"

echo "4. a length out of reach: 40 mm of track can't meander to 200 mm"
mk tight ""
"$KC" pcb tune-test --net SIG_A --length 200 tight.kicad_pcb; e=$?
[ $e -ne 0 ] && echo "  ok: exit $e" || { echo "  FAIL: exit 0 for an unreachable length"; fail=1; }
expect "after" "clean" "$(drc tight)"

PAIR_SKEW='(rule "USB skew" (condition "A.inDiffPair('"'"'USB'"'"')") (constraint skew (max 0.05mm)))'
PAIR_LEN='(rule "USB 46" (condition "A.inDiffPair('"'"'USB'"'"')") (constraint length (min 45.9mm) (max 46.1mm)))'

echo "5. a differential pair's skew: the shorter net meandered against the other"
mk pskew "$PAIR_SKEW"
expect "before" "skew_out_of_range" "$(drc pskew)"
"$KC" pcb tune-test --mode skew --net USB_P pskew.kicad_pcb || fail=1
expect "after" "clean" "$(drc pskew)"

echo "6. a differential pair tuned as one to a length"
mk plen "$PAIR_LEN"
expect "before" "length_out_of_range length_out_of_range" "$(drc plen)"
"$KC" pcb tune-test --mode diff-pair --net USB_P --length 46 plen.kicad_pcb; echo "  exit $? (the pair's 0.41 mm skew stays: USB_N ends up over)"
echo "   ... DRC after: $(drc plen)"
echo "6b. skew first, then the pair as one"
mk plen "$PAIR_LEN
$PAIR_SKEW"
"$KC" pcb tune-test --mode skew --net USB_P plen.kicad_pcb || fail=1
"$KC" pcb tune-test --mode diff-pair --net USB_P --length 46 plen.kicad_pcb || fail=1
expect "after" "clean" "$(drc plen)"

rm -rf "$here/last-run"; cp -R "$w" "$here/last-run"; rm -rf "$w"
exit $fail
