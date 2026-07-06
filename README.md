# Detachable Shell — persistent terminal sessions with 100% native UX

Persistent, named shell sessions on a remote server that look and feel
**exactly like a plain terminal**: the terminal emulator owns all scrolling,
mouse selection, and copy/paste (no copy-mode, no captured keys, no status
bars), and on reattach the session's **history is replayed into the
terminal's native scrollback** — so you can wheel up past the attach point,
drag-select old output, and Ctrl+Shift+C it, hours or days later.

Two independent, equivalently verified implementations (pick either; both
can run side by side):

| | `dsh-tmux` (tmux-based) | `dsh-shpool` (shpool-based) |
|---|---|---|
| Engine | stock tmux 3.4 on a private socket, tamed by config + wrapper | [shpool](https://github.com/shell-pool/shpool) 0.11 (persistence-only, never owns the screen) |
| Replay depth | up to 100,000 lines | up to 10,000 lines (config) |
| Implementation | `tmux/` | `shpool/` |

Full requirements, test evidence, and the head-to-head comparison: `REPORT.md`.

## Setup

1. Dependencies: `tmux` ≥ 3.2 (for `dsh-tmux`); `shpool` binary (for `dsh-shpool`),
   e.g. `cargo install shpool --locked` (no root needed).
2. Put the two entry points on your PATH (self-relative — they find their own
   configs, so only these pointers matter):

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

   (Run from this directory. `~/bin` is on PATH from the next login on Ubuntu.)

## Usage — identical grammar for both commands

```
dsh-tmux                    dsh-shpool                 list sessions
dsh-tmux work               dsh-shpool work            attach or create "work"
Ctrl-q                 Ctrl-q                     detach (from inside)
dsh-tmux detach [work]      dsh-shpool detach [work]   detach a client remotely
dsh-tmux kill work          dsh-shpool kill work       kill session(s)
dsh-tmux attach kill        dsh-shpool attach kill     attach a session named like a subcommand
```

Daily rhythm: one session per terminal tab (`dsh-tmux work`). Detach by pressing
Ctrl-q **or just closing the tab / dropping SSH** — sessions shrug off
hang-ups. Reattach later with the same command; scroll up: your history is
there, natively. End a session with `exit` inside it.

Knowing where you are: shpool prefixes your prompt with `shpool:<name>`;
tmux sets the terminal title to `[tmux:<name>] …` (VS Code displays titles
only with `"terminal.integrated.tabs.title": "${sequence}"` in settings).

## How it works (short version)

Both tools obey a byte contract: they never send mouse-tracking or
alternate-screen escape sequences to your terminal, so the terminal retains
native selection/scroll/copy *by construction*, and they replay stored
session history as plain text on attach so it lands in real scrollback.

- **`dsh-tmux`**: tmux runs with `mouse off`, zero key bindings (except Ctrl-q →
  detach), no status bar, and with the alternate-screen capability stripped,
  so it paints on the normal screen. The wrapper replays scrolled-off pane
  history via `capture-pane` before attaching (visible screen excluded — no
  duplication; one blank seam line marks the boundary).
- **`dsh-shpool`**: shpool is a transparent byte pipe with a per-user daemon
  (auto-started by the wrapper, socket at `~/.local/run/shpool/`). Config
  (`shpool/config.toml`): `session_restore_mode = lines(10000)`,
  single-key Ctrl-q detach (no chord latching), silent attach.

## Caveats (honest list)

- Sessions die on server reboot, and (shpool) if the daemon is killed —
  optional hardening: systemd *user* unit with `Restart=on-failure`
  (template in `shpool/README.md`).
- One attached client per session; attaching kicks a stale client.
- Ctrl-q never reaches programs inside sessions (it's the detach key).
- tmux: history that scrolled off before a session was created on this
  config can't be replayed; TUI apps replay as snapshots of scrolled lines.
- shpool: reattaching at a *shorter* terminal clips the last
  (old−new)-rows of the final screen; scrolled-off history is unaffected.
- Client-side rendering is the terminal's job: a broken emulator (e.g. the
  VS Code 1.117 mouse-forwarding bug) breaks bare SSH the same way — these
  tools send plain bytes only.

## Testing

`tests/harness.py` machine-verifies the spec (byte contract, history replay
across brutal client kills, persistence, 5-session isolation, interactivity):

```sh
python3 tests/harness.py tests/configs/tmux-branch.json     # or shpool-alt.json
```

Config JSONs contain absolute paths — update them if this directory moves.
What no machine can prove (real mouse feel, end-to-end clipboard): 30-second
human checklist in `REPORT.md` §2.
