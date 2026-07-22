# Test harness for the persistent-terminal-session spec

Automated, human-free compliance testing of a session-persistence tool against
the project spec in `WHY.md` (requirements R1–R5). Python 3 stdlib only, no root, no pip.

The harness simulates a terminal-emulator client: it spawns the tool's attach
command inside a fresh pty (120x32, `TERM=xterm-256color`), types bytes into
it, records **every byte** the tool emits over its whole lifetime, and kills
clients **abruptly** — `SIGHUP` to the client process group, the kernel-level
equivalent of the user closing the terminal window. No graceful detach is ever
used.

## The key idea: R2/R3 reduce to a byte contract

A standards-compliant terminal (GNOME Terminal, VS Code/xterm.js) keeps fully
native drag-select, Ctrl+Shift+C/V, right-click menu and wheel scrolling **by
construction** as long as the application on the pty never enables:

- mouse tracking — `ESC[?1000h`, `1002h`, `1003h`, `1005h`, `1006h`, `1015h`, `1016h`
- the alternate screen — `ESC[?47h`, `1047h`, `1049h`

If those bytes never occur, the terminal owns selection/scroll/clipboard 100%
of the time and there is nothing for the tool to "capture". So instead of a
mouse robot, T1 scans the complete output stream of every client for those
enable sequences. Explicitly **allowed**: bracketed paste (`2004`), focus
reporting (`1004`), synchronized update (`2026`), cursor/color/title/SGR
sequences — none of them affect selection or scrollback.

## Usage

```sh
python3 /home/jerry/Workspace/literature-review/session-spec-solutions/tests/harness.py CONFIG.json \
    [--out RESULTS.json] [--transcripts DIR]
```

- Exit code `0` iff **all five tests pass**; per-test PASS/FAIL summary on
  stdout; machine-readable details in the results JSON (default:
  `results-<name>.json` next to the config).
- `--transcripts DIR` dumps each simulated client's raw byte stream to
  `DIR/<client-label>.raw` for forensics.
- One run takes roughly 1.5–3 minutes; a 900 s watchdog aborts hung runs.

### Config format

```json
{
  "name":              "my-tool",
  "new_or_attach_cmd": "mytool attach-or-create {session}",
  "pre":               "optional shell command run once before the tests",
  "post":              "optional shell command run once after the tests"
}
```

`{session}` is replaced with a unique per-run session name (e.g.
`spa1b2c3-S1` … `-S5`); `{run_id}` is substituted in all three commands. The
command must **create the session if missing, else attach** — both branches
get exercised. Use `post` to tear down the tool's server.

Provided configs (in `configs/`):

| config | purpose | expected result |
|---|---|---|
| `control-bash.json` | plain `bash -i`, no persistence tool — proves the harness can fail | T1/T4/T5 PASS, **T2/T3 FAIL**, exit 1 |
| `fixture-tmux-replay.json` (+ `fixture-tmux-replay-attach.sh`) | minimal "candidate A" tmux replay-on-attach wrapper on a **private socket** `spec-harness-fixture` — proves the harness can pass | all PASS, exit 0 |

Validation runs on 2026-07-05 (tmux 3.4, Python 3.13.9) reproduced exactly
those outcomes; additionally, default-config tmux (`new-session -A`) was run
as a negative probe and correctly **failed T1** (12 × `ESC[?1049h`) and
**failed T2** (only the last screenful, 29/200 markers, replayed) — matching
the spec's "why tmux fails" analysis.

## The tests

| ID | spec | what it proves |
|---|---|---|
| **T1 byte-contract** | R2, R3 | Across every client's entire lifetime (attach, work, kill, re-attach, 5 concurrent sessions) the output never contains a mouse-tracking or alternate-screen **enable** sequence. Multi-parameter `DECSET` lists (`ESC[?2004;1049h`) are parsed, not just literal matches. Any violation is reported with client, byte offset and decoded meaning. |
| **T2 replay** | R4 | Client A attaches to S1, emits 200 uniquely numbered marker lines, and is killed abruptly (SIGHUP, no detach). A fresh client B on S1 must receive ≥ 190 distinct markers as plain output lines, in ascending first-occurrence order (tolerates a screen repaint re-showing the tail), and must **not** erase scrollback (`ESC[3J`) after replaying — that would silently undo R4 on a real terminal. |
| **T3 persistence** | R1 | A `python3` counter started (plain `&`, no `nohup`/`setsid`/`disown`) inside S1 keeps running after the client is SIGHUP-killed: same pid still alive (verified via `/proc/<pid>/cmdline`, guards pid reuse), tmpfile counter still advancing. On re-attach, `$$` must be the **same shell pid** as before the kill — a tool that merely leaves orphans behind, or spawns a fresh shell per attach (the bash control does exactly this), fails. Re-attached session must answer probes. |
| **T4 sessions** | R5 | Five named sessions are created, each attached/killed/re-attached; all five are then attached **concurrently** and must be interactive. Unique isolation tokens echoed into each session (plus S1's 200 T2 markers) must never appear in any other session's byte stream. |
| **T5 interactivity** | R2 | After **every** attach/re-attach (8 contexts per run), the harness first sends a burst of stray bytes (arrow keys, Home, bare Esc, Ctrl-C) and then an `echo` probe; the response must arrive within 5 s. Proves keystrokes always reach the shell and no modal capture (copy-mode etc.) can be triggered. Probe commands are typed with a `''` split (`echo PI''NG-…`) so the local echo of the typed line can never satisfy the check — only real shell output can. |

## Limitations — what this harness CANNOT prove

Honest gaps versus a human with a real mouse and clipboard:

1. **No real mouse, no real renderer.** The harness verifies the *byte
   contract*, not pixels. "If no mouse-tracking/alt-screen enables are sent,
   selection/wheel/copy are native" is true for standards-compliant emulators
   (GNOME Terminal, VS Code's xterm.js), but the harness cannot detect an
   emulator's own bugs or nonstandard behavior — notably the VS Code 1.117
   mouse-forwarding bug environment is not reproduced here.
2. **Clipboard is unverified.** R3's "copied text lands in the local system
   clipboard" cannot be tested without a display; there is no X/Wayland
   clipboard in the harness. (The byte contract implies terminal-native
   selection, which implies native clipboard — but only a human can confirm
   end-to-end.)
3. **Scrollback residency is inferred, not observed.** T2 proves the history
   bytes are re-emitted as plain lines (and that no `ESC[3J` erases scrollback
   afterwards), which on a normal screen puts them into the terminal's
   scrollback buffer. Whether the emulator actually retains them, and how the
   post-replay repaint *looks*, only a human scrolling a real terminal can
   confirm.
4. **Visual cleanliness is not judged.** Replayed history that is technically
   present but full of TUI repaint garbage would pass T2. Marker lines here
   are plain text; a real Claude Code session's replay aesthetics need eyes.
5. **Wheel semantics on the alt screen.** T1 forbids the alt screen entirely,
   which is stricter than strictly necessary for programs the *user* runs
   inside the session (a full-screen program legitimately uses it). The
   harness only runs plain shell commands, so a violation means the *tool*
   itself misbehaves — but it cannot judge interactions between an inner
   full-screen app and the persistence layer.
6. **Human-scale timing/feel** (latency of echo, smoothness of scroll) is not
   measured beyond the 5 s probe deadline.

### 30-second manual checklist (do this once per real terminal)

This is the user's six-gesture ritual from the spec (README § "The spec",
items 2–4). In GNOME Terminal *and* in the VS Code integrated terminal:

1. Attach to a session that has old output; run `seq 1 200`; close the
   window/tab (no detach). Reopen a terminal, re-attach.
2. **Wheel-scroll up past the attach boundary** — the pre-attach lines
   (`1..200` and older content) must be there, with no copy-mode indicator,
   and must **read as they did when live** (no interleaved fragments or
   duplicated blocks in newly-written history).
3. **Drag-select one of those older lines** with the mouse — the highlight
   must be the terminal's own (still there after you keep typing).
4. Press **Ctrl+Shift+C**, paste into a local app (browser/editor) — the
   exact text must appear. Repeat once with a **long wrapped line (a file
   path)**: it must paste as one line, no newline injected at the wrap.
5. Type immediately after all of the above — keystrokes must reach the shell
   with no mode to exit (no `q`/`Esc` needed at any point).
6. While a program is streaming output, wheel up and read: the view must
   **stay put** (not get yanked to the bottom by new output). Separately,
   watch a Claude Code edit/update block stream: no flicker or scrambled
   mid-frames (compare bare ssh if unsure).

### TUI fidelity machine test (spec-2 storage layer)

`bash tests/tui-fidelity.sh` (exit 0 = PASS) runs the tagged workload
`tests/tui-sim.sh` — transcript lines plus in-place block repaints tagged by
generation — in a throwaway tmux server on the repo config, and asserts zero
transient-frame leakage, zero duplicates, and zero wrap fragments, both at
stable size and across a 123x43→98x33→123x43 resize round-trip. Oversized
blocks (taller than the viewport) are reported but unscored: frames that
scroll off the top are archived by any history-keeping terminal
(app-inherent; REPORT.md §7).

## Safety notes

- The harness itself never runs tmux; only configs do. Both shipped tmux
  configs use **private sockets** (`-L spec-harness-fixture`,
  `-L spec-harness-neg`) and never touch the default tmux socket or the
  user's live sessions. Keep it that way in new configs.
- Session names are unique per run (`sp<hex>-S1..S5`), so concurrent harness
  runs don't collide; each run kills its own clients, its counter process,
  and its temp dir on exit.
