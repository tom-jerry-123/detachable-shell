# Detachable Shell — persistent terminal sessions with 100% native UX

Persistent, named shell sessions on a remote server that look and feel
**exactly like a plain terminal**: the emulator owns all scrolling, mouse
selection, and copy/paste — no copy-mode, no captured keys, no status bars —
and on reattach the session's history is replayed into the terminal's
**native scrollback**, so you can wheel up past the attach point,
drag-select old output, and Ctrl+Shift+C it days later.

Two implementations (they can run side by side). **`dsh-tmux` is the daily
driver.** `dsh-shpool` is a persistence-only fallback: it replays history at
its authored width with no reflow, so full-width TUI lines shatter on any
size-mismatched reattach — fails spec 2(a) (`REPORT.md` §7); fine at fixed
sizes and for non-TUI workloads.

| | `dsh-tmux` (tmux-based) | `dsh-shpool` (shpool-based) |
|---|---|---|
| Engine | stock tmux 3.4 on a private socket, tamed by config + wrapper | [shpool](https://github.com/shell-pool/shpool) 0.11 (persistence-only, never owns the screen) |
| Replay depth | up to 100,000 lines | up to 10,000 lines (config) |
| Implementation | `tmux/` | `shpool/` |

## The spec

Spec of record since 2026-07-20 (supersedes `WHY.md` R1–R5, kept as design
history). Each requirement is observable user experience with a behavioral
test; none prescribes implementation. Evidence: `REPORT.md`.

### 1 — Sessions persist, hard

A named session (shell + running program) survives SSH drops, closed
windows, and abrupt terminal kills; reattach by name from any terminal. ~5
concurrent sessions, one attached client each (attaching kicks a stale
client). The layer must not add new ways for sessions to die; its death
modes are stated and signed off, not discovered.
**Test:** kill the window mid-run; reattach from the other emulator — the
program never noticed.

### 2 — Wheel-up retrieves readable history

The mouse wheel scrolls back through session output — including pre-attach
and while-detached output — and it reads **as it looked when live** (the
bar: what tmux copy-mode shows). No commands, no modes. Specifically:
**(a)** fidelity holds across the daily VS Code ↔ GNOME size changes;
**(b)** reading while output streams is never yanked to the bottom;
**(c)** advertised depth is actually reachable in both emulators (VS Code
defaults to 1,000 lines — see Setup).
**Test:** reattach a real Claude Code session from the other emulator at a
different size; a human judges readability, not a marker count.

### 3 — Standard select/copy/paste

Click-drag selects anywhere, including scrolled-up history; Ctrl+Shift+C/V
work; multi-line pastes don't self-execute; no mode is ever entered or
exited. A long wrapped line (canonical case: a file path) copies as **one
logical line** — no injected newlines, trailing padding, or tool chrome.
**Test:** the six-gesture ritual — drag-select → Ctrl+Shift+C → paste →
click prompt box → type → wheel-scroll — in both emulators, on live and
scrolled-back content, including one wrapped path.

### 4 — Live screen is clean and transparent

Every keystroke reaches the program immediately — no prefix keys, no added
latency, Esc not delayed; sole exception: Ctrl-q detaches and never reaches
inner programs. Heavy TUI repaints (Claude Code edit/update blocks) render
without flicker, tearing, or garbling — indistinguishable from bare ssh.
**Test:** stream a large edit block side-by-side with the same run over bare
ssh; then verify exotic keys (lone Ctrl-Space, Alt-chords, F-keys) reach an
inner byte echoer.

## Setup

1. Dependencies: `tmux` ≥ 3.2 (for `dsh-tmux`); `shpool` binary (for
   `dsh-shpool`), e.g. `cargo install shpool --locked` (no root needed).
2. Put the two entry points on your PATH (self-relative — they find their
   own configs):

   ```sh
   mkdir -p ~/bin
   cat > ~/bin/dsh-tmux <<EOF
   #!/usr/bin/env bash
   export ATT_SOCKET="\${ATT_SOCKET:-persist}"
   exec $(pwd)/tmux/dsh-tmux "\$@"
   EOF
   chmod +x ~/bin/dsh-tmux
   ln -sf "$(pwd)/shpool/dsh-shpool" ~/bin/dsh-shpool
   ```

   (Run from this directory. `~/bin` is on PATH from the next login.)

3. VS Code only — spec 2(c): raise the terminal scrollback cap, or replay
   deeper than the default 1,000 lines is silently truncated client-side:
   `"terminal.integrated.scrollback": 100000` (user settings, or machine
   settings on the remote host).

## Usage — identical grammar for both commands

```
dsh-tmux                    dsh-shpool                 list sessions
dsh-tmux work               dsh-shpool work            attach or create "work"
Ctrl-q                 Ctrl-q                     detach (from inside)
dsh-tmux detach [work]      dsh-shpool detach [work]   detach a client remotely
dsh-tmux kill work          dsh-shpool kill work       kill session(s)
dsh-tmux attach kill        dsh-shpool attach kill     attach a session named like a subcommand
```

Daily rhythm: one session per terminal tab. Detach with Ctrl-q **or just
close the tab / drop SSH**; reattach with the same command; `exit` inside to
end. You always know you're in a session: shpool prefixes the prompt with
`shpool:<name>`; tmux sets the terminal title to `[tmux:<name>] …` (VS Code
shows titles with `"terminal.integrated.tabs.title": "${sequence}"`).

## How it works (short version)

Both tools obey a byte contract — they never send mouse-tracking or
alternate-screen sequences, so the terminal keeps native
selection/scroll/copy *by construction* — and replay stored history as plain
text on attach so it lands in real scrollback. `dsh-tmux`: mouse off, zero
bindings (Ctrl-q only), no status bar, alt-screen stripped, synchronized-
update guards for flicker-free repaints; the wrapper resizes, waits for
output to settle, then replays scrolled-off history via `capture-pane`
before attaching (visible screen excluded; one blank seam line). `dsh-shpool`:
a transparent byte pipe with a per-user daemon auto-started by the wrapper;
restores the last 10,000 lines, single-key Ctrl-q, silent attach.

## Caveats (honest list)

- Sessions die on server reboot, and (shpool) if the daemon is killed —
  optional systemd user-unit hardening in `shpool/README.md`.
- Ctrl-q never reaches programs inside sessions (it's the detach key).
- tmux: history from before a session existed on this config can't be
  replayed. Frames a TUI scrolls off the top (e.g. an edit block taller
  than the window) are archived forever — true of any history-keeping
  terminal — and sessions created before 2026-07-21 carry accumulated
  garble that no replay fix can remove (`REPORT.md` §7).
- shpool: a *shorter* reattach clips the last (old−new) rows of the final
  screen; a *different width* shatters full-width lines — the 2(a)
  demotion above.
- Client-side rendering is the terminal's job: a broken emulator breaks
  bare SSH the same way — these tools send plain bytes only.

## Testing

`tests/harness.py` machine-verifies the byte contract, history replay across
brutal client kills, persistence, 5-session isolation, and interactivity;
`tests/tui-fidelity.sh` machine-verifies spec-2 storage fidelity under a
tagged TUI workload with resize churn. The human half (mouse feel,
end-to-end clipboard): checklist in `tests/README.md`.

```sh
python3 tests/harness.py tests/configs/tmux-branch.json   # or shpool-alt.json
bash tests/tui-fidelity.sh
```

Config JSONs contain absolute paths — update them if this directory moves.
