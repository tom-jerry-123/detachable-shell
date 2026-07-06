#!/bin/sh
# HARNESS-VALIDATION FIXTURE ONLY — not the production solution.
# A minimal "candidate A"-style tool: tmux on a PRIVATE socket, mouse off,
# alt-screen (smcup) suppressed via terminal-overrides, and history replayed
# into the outer terminal (native scrollback) before attaching.
#
# Uses ONLY the private socket "spec-harness-fixture"; never touches the
# default tmux socket or the user's live sessions.
set -e
SOCK=spec-harness-fixture
S="$1"
[ -n "$S" ] || { echo "usage: $0 SESSION-NAME" >&2; exit 2; }

T() { tmux -L "$SOCK" -f /dev/null "$@"; }

if ! T has-session -t "=$S" 2>/dev/null; then
    T new-session -d -s "$S" -x 120 -y 32
fi
# Never emit smcup/rmcup (alt screen) to any client terminal; no mouse mode;
# no status bar (keeps the byte stream clean, though status would not violate
# the byte contract anyway).
T set-option -s terminal-overrides '*:smcup@:rmcup@' 2>/dev/null || true
T set-option -g mouse off 2>/dev/null || true
T set-option -g status off 2>/dev/null || true
T set-option -g history-limit 50000 2>/dev/null || true

# Replay stored history (incl. pre-attach output) as plain lines so it lands
# in the OUTER terminal's native scrollback, then attach.
T capture-pane -p -J -t "=$S:" -S - 2>/dev/null || true
exec tmux -L "$SOCK" -f /dev/null attach-session -t "=$S"
