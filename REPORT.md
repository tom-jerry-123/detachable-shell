# Persistent Terminal Sessions — Final Report

Spec: the project spec in `WHY.md` (requirements R1–R5). Two working candidates were built and tested with the
automated acceptance harness (`tests/harness.py`, 5 tests T1–T5; see `tests/README.md`).

**Adopt `tmux/dsh-tmux` (tmux 3.4 on a private socket + replay-on-attach).** Details below.
**Update 2026-07-05:** shpool was subsequently verified to the same adversarial standard and is an
equally valid choice — see §6 (shpool addendum) for the head-to-head.

## 1. Requirements matrix

Candidates:
- **tmux-branch** — `tmux/dsh-tmux` + `native.tmux.conf`, private socket `jerryspec`. Built,
  harness-passed (exit 0, `tests/results-tmux-branch.json`), independently re-run and
  adversarially verified (verdict CONFIRMED: TUI-redraw + injected mouse/alt-screen escapes,
  unicode + 600-char wrapped lines, geometry changes — no gaming, no leaks).
- **zmx** (alternatives) — third-party binary `alternatives/bin/zmx` v0.6.0 (ghostty-vt based)
  + `zmx-attach` wrapper (rejected candidate; its binary/wrapper/config were not imported into
  this repo — only the result log `tests/results-zmx-alt.json`). Harness run 2026-07-05: all 5
  tests PASS (exit 0). One run only; **no**
  adversarial verification, no TUI/unicode stress, prebuilt binary of a young project.

| Req | Meaning | tmux-branch | zmx |
|---|---|---|---|
| R1 persistence across abrupt client kill | same shell `$$`, background pid alive & advancing | **PROVEN** (T3: pid alive, counter advancing, `$$` 1488524→1488524) | **PROVEN** (T3: same-shell, counter advancing) |
| R2 native, no modes | zero mouse/alt-screen enables + interactive after stray-byte bursts | **PROVEN** (T1: 44,643 B / 12 clients, 0 forbidden sequences; T5: 8/8 contexts) | **LIKELY** (T1/T5 pass, but zmx captures **Ctrl+\\** as its detach chord — that keystroke never reaches the program, a small R2 deviation the harness does not probe) |
| R3 native select/copy/paste | byte contract (no mouse-tracking/alt-screen ⇒ terminal owns selection) | **PROVEN at byte level** (T1); end-to-end clipboard needs the human checklist (§2) | **PROVEN at byte level** (T1); same human residue |
| R4 pre-attach history in native scrollback | ≥190/200 markers replayed as plain lines, ascending, no `ESC[3J` after | **PROVEN** (T2: 200/200; smoke: replay across 24x80→30x100 resize, 60/60) | **PROVEN** (T2: 200/200, no ED3) — but untested with TUI repaints/resizes |
| R5 five named concurrent sessions | 5 sessions attach/kill/reattach + concurrent, zero cross-leaks | **PROVEN** (T4: 5/5 interactive, 0 isolation violations) | **PROVEN** (T4: 5/5, 0 violations) |

Naming note: `tmux-branch` was later renamed to `tmux/` in this repo (and its entry point
`att` to `dsh-tmux`); the researcher's earlier draft that previously occupied `tmux/` was
removed. tmux-branch had fixed a real bug in that draft: `stty size` returning `0 0` on a
0x0 pty made tmux abort with "width too small"; `dsh-tmux` falls back to 24x80.

Spec candidates B–D were not built, per the spec's own analysis: B (VS Code persistent
terminals) fails R1/R5 from GNOME Terminal; C (dtach/abduco) fails R4; D (mosh/ET) fails R1/R4.

## 2. What no machine test can prove — 30-second human checklist

The harness proves the **byte contract**: the tool never emits mouse-tracking
(`ESC[?1000/1002/1003/1005/1006/1015/1016h`) or alt-screen (`ESC[?47/1047/1049h`) enables, and
history is re-emitted as plain lines with no scrollback erase. On a standards-compliant emulator
that *implies* native select/wheel/clipboard, but four things still need eyes
(full list: `tests/README.md` §Limitations):

1. Real mouse/renderer behavior — incl. the VS Code 1.117 forwarding bug (see §4).
2. Clipboard end-to-end (no display in the harness).
3. Visual scrollback residency and replay cleanliness (TUI repaint aesthetics).
4. Feel/latency beyond the 5 s probe deadline.

Do once in **GNOME Terminal** and once in the **VS Code integrated terminal**:

1. `att work`; run `seq 1 200`; close the window/tab outright (no detach). Reopen, `att work`.
2. Wheel-scroll up past the attach seam — `1..200` and older content must be there, no copy-mode.
3. Drag-select one of those older lines — highlight must be the terminal's own.
4. Ctrl+Shift+C, paste into a local app — exact text appears.
5. Type immediately after all of the above — no mode to exit, every key reaches the shell.

## 3. Recommendation: adopt tmux-branch

Why over zmx: equal harness score, but tmux-branch is (a) adversarially verified including
injected mouse/alt-screen escapes from inner programs, TUI redraws, unicode and resize;
(b) stock tmux 3.4 + a 4 KB readable shell script, not a 9.6 MB third-party binary;
(c) free of the Ctrl+\\ capture; (d) documented (README, migration path). Keep zmx as a
fallback candidate — if you ever want it, verify it adversarially first and accept Ctrl+\\ loss.

### Day 1

```sh
ATT=/home/jerry/Tools/detachable-shell/tmux/dsh-tmux
$ATT work        # create-or-attach session "work" (private socket jerryspec)
$ATT             # list sessions
# optional: mkdir -p ~/bin && ln -s $ATT ~/bin/dsh-tmux   (~/bin is on PATH on Ubuntu)
```

- One session per terminal tab; 5 tabs = 5 named sessions (R5).
- Detach = just close the tab / drop SSH. Reattach with `dsh-tmux <name>` from anywhere; a stale
  client is kicked, full pre-attach history is replayed into native scrollback, then attach.
- End a session: `exit` inside it. Server exits itself when the last session ends.
- Env overrides: `ATT_SOCKET` (default `jerryspec`), `ATT_CONF`.

### Migrating your 5 live sessions (safe; old and new coexist on different sockets)

Nothing automatic was done — your 5 sessions on the **default** tmux socket are untouched.
They cannot adopt this config in place (per-pane `history-limit` is fixed; rebinding the shared
default server is unsafe). Per session, at a natural break in its Claude Code task:

```sh
# 1. salvage history (read-only, safe to run now):
tmux capture-pane -p -e -J -t OLDNAME -S - > ~/oldname-history.txt
# 2. cleanly stop the program, exit the old session's shell
#    (or: tmux kill-session -t OLDNAME   on the default socket, once sure)
# 3. dsh-tmux OLDNAME   # then relaunch the program inside
#    (optionally: cat ~/oldname-history.txt   first, to seed the new scrollback)
```

### Re-run acceptance tests anytime

```sh
python3 /home/jerry/Tools/detachable-shell/tests/harness.py \
  /home/jerry/Tools/detachable-shell/tests/configs/tmux-branch.json
```

## 4. Honest caveats

- **VS Code 1.117 mouse bug is out of scope server-side — by design.** The server sends
  *nothing but plain bytes*: no mouse-tracking or alt-screen enables ever reach the client
  (T1-proven, adversarially probed). Native selection/wheel therefore depends only on the
  emulator behaving like a bare terminal — the exact path the 1.117 bug does *not* affect
  (it breaks app-mouse-tracking forwarding, which this solution never requests). But a
  client-side regression in basic selection/scroll would hit a bare SSH shell identically
  and no server-side tool can fix it. Run the §2 checklist in VS Code once to confirm.
- **Attach cosmetics** (documented in `tmux/README.md`): one blank seam line at the
  history/live boundary (deliberate padding so tmux's attach-time clear can't eat the replay
  tail); a wrapped line straddling that boundary is cut there (nothing lost/duplicated); a
  TUI's history replays as periodic snapshots of scrolled-off lines, not a keystroke recording.
- **History bound**: replay ≤ `history-limit 100000` lines, and only for panes created on the
  `jerryspec` socket (migrated sessions start their history fresh unless seeded from salvage).
- **Alt-screen apps inside the session** (vim, less) still work; their screens correctly never
  enter history. Wheel behavior *inside* such an app is the app's affair (T1's ban applies to
  the persistence layer, which the harness proved never leaks even injected enables).
- **Exactly one client per session**: `att` kicks stale clients; concurrent multi-viewer attach
  is a spec non-goal.
- **zmx residual risk** if ever adopted: single harness run, no adversarial pass, Ctrl+\\
  captured for detach, sockets must live on a short path (`ZMX_DIR=$HOME/.zmx`; the default
  `/run/user/UID` is tmpfs and paths >~108 chars fail outright).

## 5. Artifact index

- Solution: `tmux/{dsh-tmux,native.tmux.conf,README.md}`
- Harness + docs: `tests/harness.py`, `tests/README.md`
- Results: `tests/results-tmux-branch.json` (all PASS), `tests/results-zmx-alt.json` (all PASS),
  `tests/results-control-bash.json` (control: T2/T3 FAIL as designed),
  `tests/results-fixture-tmux-replay.json`, `tests/results-neg-tmux-default.json`
  (default tmux correctly fails T1/T2 — the harness can fail)
- Superseded draft: removed (`tmux-branch` itself was renamed to `tmux/`, see §1 naming note)

## 6. shpool addendum (verified 2026-07-05, after the report above)

shpool 0.11.0 (`~/.cargo/bin/shpool`, built from crates.io) was harness-tested and then
independently adversarially verified — the same standard as tmux-branch. **Verdict: CONFIRMED.**

- **Harness**: all 5 tests PASS, twice (pre-started daemon and via the auto-daemonizing
  wrapper). Results: `tests/configs/results-shpool-alt.json`, `results-shpool-wrapper.json`.
- **Adversarial**: TUI-redraw history reattached across a size change — replay is a
  vt100-rendered snapshot (colors kept, redraw noise collapsed, zero malformed escapes, zero
  forbidden sequences); unicode/CJK/emoji + 600-char lines intact at narrower widths.
- **R2 transparency**: with the config as then shipped (`keybinding = []`) there are **zero**
  deviations — even lone Ctrl-Space reaches inner programs instantly. (The shpool *default*
  config swallows Ctrl-Space indefinitely as detach-chord prefix — fixed by our config.)
  Trade-off: no keyboard detach; detach by closing the terminal, or `shpool detach` elsewhere.
  *(Config updated 2026-07-06, user-requested: single-key Ctrl-q → detach — one deliberate R2
  exception, no chord latching. Re-verified 2026-07-16; see `shpool/README.md`.)*
- **Invisible**: zero attach-time noise with `prompt_prefix = ""`, `motd = "never"`.
  *(Since 2026-07-06 the config sets `prompt_prefix = "shpool:$SHPOOL_SESSION_NAME "` as a
  deliberate in-session indicator; it decorates the shell prompt, not the attach stream.)*
- **Caveats found**: (1) reattaching with *fewer rows* clips `old−new` trailing lines of the
  final screen (scrolled-off history is unaffected); tmux-branch has no such edge. (2) if the
  shpool daemon is SIGKILLed, sessions die within ~2s (tmux-branch has the same class of risk
  via the tmux server; both die on reboot). Mitigation: systemd user unit with
  `Restart=on-failure` (template in `shpool/README.md`) — `systemctl --user`
  works on this box. (3) replay bounded by `session_restore_mode` lines (10000 configured)
  vs tmux-branch's 100000.
- **Files**: `shpool/{config.toml,dsh-shpool,README.md}`.

**Head-to-head**: equal harness scores, both adversarially confirmed. tmux-branch edges ahead
on robustness details (no height-clip edge, 10× replay depth, battle-tested server binary);
shpool is architecturally purer (never owns the screen — the mouse dilemma is structurally
impossible) with a simpler mental model and cleaner colored-history replay. Either satisfies
the spec; pick tmux-branch for maximum proven robustness, shpool for maximum simplicity.

Usage (shpool): `shpool/dsh-shpool <name>` per terminal tab — auto-starts the
daemon; close the window to detach; `shpool --socket ~/.local/run/shpool/shpool.socket
list|detach|kill <name>` to manage from elsewhere.
