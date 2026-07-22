#!/usr/bin/env bash
# tests/tui-fidelity.sh — machine check for spec 2 (readable history; see
# README § "The spec") at the
# storage layer: runs the tagged TUI workload (tui-sim.sh) in an isolated tmux
# server on this repo's config, then asserts that
#   1. in-viewport repaints leak ZERO transient frames into history,
#   2. a resize round-trip (123x43 -> 98x33 -> 123x43) is lossless: no
#      duplicated lines, no orphaned wrap fragments, no transient leakage.
# Oversized-block transients (GEN=OVSZ) are reported informationally only:
# frames that scroll off the top are archived by ANY history-keeping terminal —
# that is app-inherent, not a persistence-layer defect (REPORT.md §7).
#
# Usage: bash tests/tui-fidelity.sh          (exit 0 = PASS, 1 = FAIL)
# Env:   FIDELITY_SOCKET  override the throwaway socket name.
set -eu

SELF_DIR=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
CONF=$SELF_DIR/../tmux/native.tmux.conf
SOCK=${FIDELITY_SOCKET:-dshx-fid-$$}
OUT=$(mktemp -d)
trap 'tmux -L "$SOCK" kill-server 2>/dev/null || true; rm -rf "$OUT"' EXIT

T() { tmux -L "$SOCK" -f "$CONF" "$@"; }
cap() { T capture-pane -p -e -J -t "=w:" -S - -E -1 > "$1"; }

fail=0
check() { # $1=label $2=actual $3=expected
    if [ "$2" -eq "$3" ]; then
        printf '  ok   %-46s %s\n' "$1" "$2"
    else
        printf '  FAIL %-46s %s (expected %s)\n' "$1" "$2" "$3"
        fail=1
    fi
}
metric_transients() { grep -c 'GEN=T[0-9]' "$1" || true; }
metric_dups() { grep -o 'GEN=FINAL cyc[0-9] line[0-9]*' "$1" | sort | uniq -d | wc -l; }
metric_fragments() { grep 'seg-' "$1" | grep -vc 'GEN=FINAL' || true; }

T new-session -d -s w -x 123 -y 43
T send-keys -t w "bash $SELF_DIR/tui-sim.sh" Enter
for _ in $(seq 1 60); do
    T capture-pane -p -t w | grep -q SIM-DONE && break
    sleep 0.5
done
T capture-pane -p -t w | grep -q SIM-DONE || { echo "FAIL: workload never finished"; exit 1; }

echo "stable size (123x43):"
cap "$OUT/stable.txt"
check "transient frames archived"      "$(metric_transients "$OUT/stable.txt")" 0
check "duplicated transcript lines"    "$(metric_dups       "$OUT/stable.txt")" 0
check "orphaned wrap fragments"        "$(metric_fragments  "$OUT/stable.txt")" 0
echo "  info oversized-block transients archived        $(grep -c 'GEN=OVSZ' "$OUT/stable.txt" || true) (app-inherent, unscored)"

T resize-window -t "=w:" -x 98 -y 33
sleep 0.5
T resize-window -t "=w:" -x 123 -y 43
sleep 0.5
echo "after resize round-trip (123x43 -> 98x33 -> 123x43):"
cap "$OUT/roundtrip.txt"
check "transient frames archived"      "$(metric_transients "$OUT/roundtrip.txt")" 0
check "duplicated transcript lines"    "$(metric_dups       "$OUT/roundtrip.txt")" 0
check "orphaned wrap fragments"        "$(metric_fragments  "$OUT/roundtrip.txt")" 0
check "FINAL lines preserved across round-trip" \
    "$(grep -c 'GEN=FINAL' "$OUT/roundtrip.txt")" "$(grep -c 'GEN=FINAL' "$OUT/stable.txt")"

if [ "$fail" -eq 0 ]; then echo "OVERALL: PASS"; else echo "OVERALL: FAIL"; fi
exit "$fail"
