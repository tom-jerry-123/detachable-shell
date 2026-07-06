# shpool as the persistence layer (spec candidate)

[shpool](https://crates.io/crates/shpool) 0.11.0 (`~/.cargo/bin/shpool`,
installed from crates.io) configured as a **pure persistence layer** per
the project spec in `WHY.md`. With the config in this directory it passes all
five harness tests (`tests/harness.py`), both when the daemon is
pre-started (`tests/configs/shpool-alt.json`) and via the auto-daemonizing
wrapper (`tests/configs/shpool-wrapper.json`).

## Usage

```sh
# attach to (or create) a named session — one per terminal tab:
dsh-shpool work1
```

That is the whole interface. The wrapper:

1. auto-starts the shpool daemon if none is listening
   (`shpool --daemonize`; proper double-fork + setsid, pid file + flock,
   stale-socket cleanup — note `--daemonize` is a *global* flag; at the
   `attach` level `-d` means `--dir`),
2. uses the socket `~/.local/run/shpool/shpool.socket` (survives logout,
   unlike `/run/user/$UID`; override with `SHPOOL_RUN_DIR` for isolated
   testing, `SHPOOL_BIN` to point at another binary),
3. attaches with `-f` so a stale attached client elsewhere is bumped
   (spec R5: exactly one client per session).

To end a session: `exit` the shell. To detach but keep it running: just
close the terminal window/tab — surviving abrupt hangups is the whole
point and is what the harness tests (SIGHUP, no graceful detach). From
another terminal: `shpool --socket ~/.local/run/shpool/shpool.socket
detach <name>` / `list` / `kill <name>`.

## Config rationale (`config.toml`)

| setting | why |
|---|---|
| `session_restore_mode = { lines = 10000 }` | R4: on reattach shpool replays up to the last 10000 history lines as **plain rendered text** into the client pty, which lands in the terminal's native scrollback. Verified: 200/200 marker lines, ascending, no `ESC[3J` scrollback erase afterwards. |
| `prompt_prefix = ""` | R2 invisibility: default shpool prepends `shpool:$SHPOOL_SESSION_NAME` to the shell prompt; empty string disables the injection. |
| `keybinding = []` | R2 "typing always reaches the program": disables **all** shpool keybindings, including the default detach chord `Ctrl-Space Ctrl-q`. See deviation notes below. |
| `motd = "never"` | Explicitly no message of the day (this is also the default). |

### Attach noise

Verified byte-exact: on a **fresh** attach shpool prints *nothing* — the
first bytes a client sees are the shell's own prompt. On **reattach** the
replay is prefixed only by `ESC[?25h ESC[m ESC[H ESC[J` (show cursor, SGR
reset, home, clear visible screen — *not* `3J`, so existing terminal
scrollback is untouched) followed by the re-rendered history. No banner,
no motd, no session-name decoration.

### Replay semantics (worth knowing)

- The replay comes from an internal vt100 spool (1024 cells wide by
  default, `vt100_output_spool_width`), **not** a raw byte log. TUI redraw
  garbage is collapsed to its final visual state: colored (SGR) text is
  preserved, cursor-movement/erase-line games are resolved, and content a
  program itself overwrote or `ESC[2K`-erased is (correctly) absent.
  Verified: zero malformed/unterminated escape sequences in the replayed
  stream, zero mouse/alt-screen enables, terminal fully interactive after.
- Unicode is safe: CJK + emoji history and 600-char lines replay
  byte-intact (valid UTF-8, payloads contiguous) even reattaching at
  60x20 after 120x32.
- **Height-shrink caveat:** reattaching with *fewer rows* than the
  previous client clips the tail of the final screen — exactly
  `old_rows − new_rows` trailing lines were lost in testing (32→24 rows:
  last 8 lines, the shell repaints the prompt afterwards). Same-size,
  wider/narrower-same-height, and taller reattaches are lossless. Attach
  with tabs of similar height, or expect to lose the last few
  bottom-of-screen lines (scrolled-off history is unaffected).

## Daemon lifecycle (honest caveats)

**Sessions live inside the daemon.** Verified by SIGKILLing the daemon
while a session ran: the session shell and its background child died
within ~2 s (their pty master vanished → SIGHUP), the attached client
exited, and after a daemon restart `shpool list` was empty; reattaching by
the same name created a brand-new shell. Consequences:

- A daemon crash/kill or a **server reboot ends every session**. There is
  no state on disk that resurrects them. Plan for this like you would for
  tmux server death.
- Don't put the socket where the daemon binary can be reaped by session
  cleanup; the auto-daemonized process is setsid'd and survives terminal
  and SSH-session close (harness-verified).

Recommended pattern on this box (no root):

- **Default: the wrapper.** `dsh-shpool` lazily (re)starts the daemon on
  first attach after boot; no unit files needed. This is the configuration
  the harness validated end-to-end.
- **Optional hardening:** `systemctl --user` works here, so a user unit
  gives auto-restart-on-crash:

  ```ini
  # ~/.config/systemd/user/shpool.service
  [Unit]
  Description=shpool session daemon
  [Service]
  ExecStart=%h/.cargo/bin/shpool --config-file %h/Tools/detachable-shell/shpool/config.toml --socket %h/.local/run/shpool/shpool.socket daemon
  Restart=on-failure
  [Install]
  WantedBy=default.target
  ```

  `systemctl --user enable --now shpool` plus
  `loginctl enable-linger $USER` (if permitted) to start it at boot
  without a login. The wrapper coexists with this: if the unit's daemon
  is up, `--daemonize` is a no-op. (Restart only helps future sessions —
  sessions running at crash time are still lost.)

## Remaining R2 deviations

With `keybinding = []` (this config): **none found.** Verified with a
raw-mode byte echoer inside the session: lone `Ctrl-Space` (0x00), the
full `0x00 0x11` chord, and `Ctrl-S` all reach the inner program
immediately and nothing detaches. The trade-off is that there is *no*
keyboard detach — detach by closing the terminal or from another terminal.

With shpool's **default** keybindings (if you remove `keybinding = []`):
the daemon watches for `Ctrl-Space Ctrl-q`. A lone `Ctrl-Space` is
**held back indefinitely** (not delivered after 3 s; flushed only
together with the next keystroke) — this breaks programs that bind
`Ctrl-Space` (emacs set-mark, some Claude Code keymaps) and is a real,
measured R2 violation. The chord itself is snipped from the input stream
(the inner program never sees it) and detaches cleanly.

Also note: bracketed-paste (`ESC[?2004h`) sequences seen in transcripts
come from bash's readline, not shpool — identical in a bare terminal, and
explicitly allowed by the byte contract.

## Verification record

- `tests/configs/results-shpool-alt.json` — pre-started daemon, final
  config: T1–T5 PASS (fresh run 2026-07-05).
- `tests/configs/results-shpool-wrapper.json` — `dsh-shpool` wrapper with
  auto-daemonize, final config: T1–T5 PASS (fresh run 2026-07-05).
- Adversarial probes (isolated `XDG_RUNTIME_DIR`, 2026-07-05): TUI/color
  history replay at 80x24 after 120x32; CJK/emoji + 600-char lines at
  60x20; detach-chord byte timing; daemon SIGKILL; attach-noise byte
  capture — findings folded into the sections above.
