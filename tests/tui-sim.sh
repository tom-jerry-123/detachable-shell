#!/usr/bin/env bash
# tui-sim.sh — mimic Claude Code-style output so scrollback pollution is measurable.
# Emits: transcript lines (tagged GEN=FINAL, some long enough to wrap), then a
# "streaming edit block" redrawn in place with cursor-up + erase-line, whose
# transient frames are tagged GEN=<nn>. Frames are wrapped in synchronized-update
# guards (\e[?2026h/l) like modern TUIs. A clean terminal's scrollback should
# contain ONLY GEN=FINAL lines; any GEN=<nn> line in history is pollution.
# Cycle 2 uses an oversized block (taller than the pane) — the reported garble
# trigger — whose transients MUST scroll off; we tag those OVSZ for separation.
set -u
rows=$(stty size 2>/dev/null | cut -d' ' -f1); rows=${rows:-43}

block() { # $1=tag $2=height  — redraw a growing block in place
  local tag=$1 grow=$2 drawn=0 gen l cap
  for gen in $(seq 1 "$grow"); do
    printf '\e[?2026h'
    cap=$(( rows - 2 )); [ "$drawn" -gt "$cap" ] && drawn=$cap
    [ "$drawn" -gt 0 ] && printf '\e[%dA' "$drawn"
    for l in $(seq 1 "$gen"); do
      printf '\e[2K\e[32m+ GEN=%s%02d block-line %02d/%02d transient diff content\e[0m\n' "$tag" "$gen" "$l" "$gen"
    done
    drawn=$gen
    printf '\e[?2026l'
    sleep 0.02
  done
  # finalize: overwrite visible part with FINAL
  cap=$(( rows - 2 )); [ "$drawn" -gt "$cap" ] && drawn=$cap
  printf '\e[%dA' "$drawn"
  for l in $(seq 1 "$drawn"); do
    printf '\e[2K\e[32m+ GEN=FINAL blk%s line%02d finalized diff line\e[0m\n' "$tag" "$l"
  done
}

for cycle in 1 2 3; do
  for i in $(seq 1 40); do
    if [ $(( i % 7 )) -eq 0 ]; then
      # long single logical line (~200 chars) that must wrap: a fake path
      printf '\e[36m# GEN=FINAL cyc%d line%03d /data/experiments/run-%03d/%s/checkpoint.pt\e[0m\n' \
        "$cycle" "$i" "$i" "$(printf 'seg-%02d/%.0s' "$i" 1 2 3 4 5 6 7 8 9 10 11 12)"
    else
      printf '. GEN=FINAL cyc%d line%03d ordinary transcript text here\n' "$cycle" "$i"
    fi
  done
  if [ "$cycle" -eq 2 ]; then block OVSZ $(( rows + 17 )); else block T 14; fi
done
printf 'SIM-DONE rows=%s\n' "$rows"
