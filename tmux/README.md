# tmux — persistent sessions with fully native terminal UX

Implements the project spec in `WHY.md` (requirements R1–R5) with stock tmux 3.4 on a
**private socket** plus a replay-on-attach wrapper. tmux is reduced to a pure
persistence layer: no prefix key, no bindings, no mouse mode, no status bar,
no alternate screen. Selection, copy/paste, and wheel-scrolling are 100%
owned by your terminal emulator; on reattach the wrapper first prints the
session's scrolled-off history into the terminal so it lands in **native
scrollback** (R4), then attaches.

## Files

- `native.tmux.conf` — server config. Only ever loaded on the private
  socket `jerryspec`. Never load it into the default tmux server.
- `dsh-tmux` — executable attach-or-create wrapper.

## Usage

```sh
ATT=/home/jerry/Tools/detachable-shell/tmux/dsh-tmux

$ATT work        # create-or-attach session "work" on private socket jerryspec
$ATT             # list sessions on the socket
```

Recommended convenience (does not modify rc files automatically — do it
yourself if you want it):

```sh
mkdir -p ~/bin
ln -s /home/jerry/Tools/detachable-shell/tmux/dsh-tmux ~/bin/dsh-tmux
# ensure ~/bin is on PATH (it is by default on Ubuntu when it exists)
```

Day-to-day:

- **Open a session:** `dsh-tmux paper1` in a terminal tab. Five tabs with five
  names = five concurrent sessions (R5). Session names: anything tmux
  accepts (avoid `.` and `:`).
- **Detach:** just close the tab / kill the SSH connection. There is no
  detach key (there are no keys at all — every keystroke reaches your
  program, R2). The session keeps running (R1).
- **Reattach:** `dsh-tmux paper1` from any terminal. If a stale client is still
  attached, `dsh-tmux` kicks it first (exactly one client per session). Before
  attaching, `dsh-tmux` resizes the session to your current terminal, prints the
  entire scrolled-off history (with colors) followed by a dim
  `─── attached: NAME; history above ───` marker line, then attaches.
  Wheel-scroll up = your terminal's own scrollback containing the full
  pre-attach history (R4). Drag-select + Ctrl+Shift+C = your terminal's own
  clipboard (R3).
- **End a session for good:** exit the shell inside it (`exit` / Ctrl-D).
  The server exits by itself when the last session ends (`exit-empty on`).

Environment overrides: `ATT_SOCKET` (socket name, default `jerryspec`),
`ATT_CONF` (config path, default `native.tmux.conf` next to `dsh-tmux`).

## What to expect at attach (cosmetics)

- One blank seam line between replayed history and the repainted live
  screen. This is the residue of a screenful of padding `dsh-tmux` emits so that
  tmux's attach-time clear (`\e[H\e[2J`, which erases rather than scrolls)
  cannot destroy the tail of the replay.
- A TUI's history (e.g. Claude Code) replays as periodic snapshots of lines
  that scrolled off the top, not a keystroke-by-keystroke recording. tmux
  stores history as final cell state, so the replay can only contain text +
  color escapes — never mouse/alt-screen sequences.
- A wrapped line that straddled the history/screen boundary is cut at the
  boundary. Cosmetic only; no line is lost or duplicated.
- Alt-screen apps (less, vim) inside the session still work; their screens
  correctly never enter history.

## Design notes (why each piece exists)

- `prefix None` + `unbind-key -a` + `mouse off`: tmux captures zero
  keystrokes and zero mouse events; copy-mode is unreachable (R2).
- `terminal-overrides ',*:smcup@:rmcup@'`: tmux never switches the outer
  terminal to the alternate screen, so all output scrolls into native
  scrollback (R3/R4). Verified byte-level on tmux 3.4: attach emits no
  `\e[?1049h`/`\e[?47h`, no mouse-enable, no `\e[3J`.
- `history-limit 100000`: applies to panes created after server start on
  this socket — the replay source. A 100k-line capture measures ~0.17 s.
- `dsh-tmux` resizes the window to the *current* terminal **before** capturing
  (a size mismatch at attach shuffles lines across the history/screen
  boundary: taller client = duplicated lines, shorter = lost lines), then
  restores `window-size latest` (resize-window flips it to `manual`).
- `capture-pane -p -e -J -S - -E -1` replays **only** scrolled-off history;
  the visible screen is repainted by tmux at attach, so nothing duplicates.
  Pane-targeted commands use `-t "=NAME:"` — a bare `=NAME` silently
  resolves to nothing as a pane target on 3.4.
- Empty history is not replayed (a capture of empty history would emit one
  stray blank line), so a brand-new session attaches with zero artifacts.

## Safety

Everything lives on the private socket `jerryspec`
(`tmux -L jerryspec ...`). The default tmux server — where your 5 existing
live sessions run — is never touched by `dsh-tmux` or by this config. Do not run
`tmux source-file native.tmux.conf` inside the default server: `unbind-key
-a` and `prefix None` would take your bindings away there.

## Migrating your 5 existing sessions (when you choose to — nothing is automatic)

Your current sessions live on the **default** socket with your old config.
They cannot fully adopt this setup in place: `terminal-overrides` is read at
client attach so it could partially apply, but `history-limit` is fixed per
existing pane and the prefix/mouse/binding changes would have to be made on
the shared default server — don't. Instead, per session, at a natural break
in its Claude Code task:

1. (Optional) Salvage the old session's history to a file — read-only, safe:

   ```sh
   tmux capture-pane -p -e -J -t OLDNAME -S - > ~/oldname-history.txt
   ```

2. Cleanly stop the program in the old session and exit its shell (or
   `tmux kill-session -t OLDNAME` on the default socket once you're sure).

3. Start the replacement: `dsh-tmux OLDNAME`, then relaunch the program inside.
   (You can `cat ~/oldname-history.txt` first if you want the salvaged
   history in the new terminal's scrollback.)

Repeat for each of the 5 sessions at your own pace; old and new coexist
because they are on different sockets.

## Verification

Acceptance harness (5 tests: no-alt-screen/no-mouse-enable byte contract,
200-line pre-attach replay, same-shell persistence across client kill,
input transparency, multi-session):

```sh
python3 /home/jerry/Tools/detachable-shell/tests/harness.py \
  /home/jerry/Tools/detachable-shell/tests/configs/tmux-branch.json
```

Latest run: see `tests/results-tmux-branch.json`.
