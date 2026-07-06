#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harness.py — automated spec-compliance harness for persistent-terminal-session
tools (see WHY.md at the project root).

The harness simulates a terminal-emulator client: it spawns the target tool's
attach command inside a fresh pty (120x32, TERM=xterm-256color), types bytes
into it, records EVERY byte the tool emits, and kills clients abruptly with
SIGHUP to the client process group — the kernel-level equivalent of the user
closing the terminal window. No graceful detach is ever used.

Key idea for R2/R3 (native selection / copy / wheel): these reduce to a BYTE
CONTRACT. A standards-compliant terminal (GNOME Terminal, VS Code) keeps fully
native drag/select/copy/wheel behavior *by construction* as long as the
application never enables mouse tracking or the alternate screen. So T1 scans
every byte of every client's lifetime for those enabling sequences.

Tests:
  T1 byte-contract   R2/R3: output never contains ESC[?{1000,1002,1003,1005,
                     1006,1015,1016}h (mouse tracking) or ESC[?{47,1047,1049}h
                     (alternate screen). Bracketed paste (2004), focus (1004),
                     sync update (2026), cursor/color/title sequences are fine.
  T2 replay          R4: attach, emit 200 uniquely numbered marker lines, kill
                     the client abruptly; a new client on the same session must
                     receive >= 190 of the markers as plain output lines, in
                     ascending (first-occurrence) order.
  T3 persistence     R1: a python3 counter started inside the session keeps
                     running (same pid, file still advancing) after the client
                     is killed; the session is still interactive on re-attach.
  T4 sessions        R5: five named sessions; attach/kill/re-attach each; all
                     five re-attached concurrently; no session's markers ever
                     appear in another session's byte stream.
  T5 interactivity   R2: after EVERY attach/re-attach, a burst of stray bytes
                     (arrow keys, bare Esc) followed by an echo command gets a
                     response within 5 s — proves no modal capture.

Usage:
  python3 harness.py CONFIG.json [--out RESULTS.json] [--transcripts DIR]

Config JSON:
  {
    "name":              "tool-name",
    "new_or_attach_cmd": "command with {session} placeholder; must create the
                          session if missing, else attach to it",
    "pre":               "optional shell cmd run once before the tests",
    "post":              "optional shell cmd run once after the tests"
  }
  {session} is replaced by a unique per-run session name; {run_id} is
  substituted in pre/post/new_or_attach_cmd as well.

Output: per-test PASS/FAIL summary on stdout, JSON results file, exit code 0
only if all five tests pass. Python 3 stdlib only.
"""

import argparse
import fcntl
import shutil
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import tempfile
import termios
import time
import traceback

HARNESS_VERSION = "1.0"
COLS, ROWS = 120, 32

# DECSET parameters that break native select/copy/scroll if ever enabled.
FORBIDDEN_DECSET = {
    1000: "mouse click tracking",
    1002: "mouse button-event (drag) tracking",
    1003: "mouse any-event tracking",
    1005: "UTF-8 mouse coordinates",
    1006: "SGR mouse coordinates",
    1015: "urxvt mouse coordinates",
    1016: "SGR-pixel mouse coordinates",
    47:   "alternate screen (legacy)",
    1047: "alternate screen",
    1049: "alternate screen + save cursor",
}
DECSET_ENABLE_RE = re.compile(rb"\x1b\[\?([0-9;]+)h")

CLIENTS = []  # every PtyClient ever spawned; transcripts kept for T1


class PtyClient:
    """One simulated terminal window attached to the target tool."""

    def __init__(self, cmd, label):
        self.label = label
        self.cmd = cmd
        self.buf = bytearray()
        self.closed = False
        self.eof = False
        pid, master = pty.fork()
        if pid == 0:  # child = the "terminal's" shell running the attach cmd
            try:
                os.environ["TERM"] = "xterm-256color"
                os.environ.pop("TMUX", None)  # never look like a nested tmux
                fcntl.ioctl(0, termios.TIOCSWINSZ,
                            struct.pack("HHHH", ROWS, COLS, 0, 0))
                os.execvp("/bin/sh", ["/bin/sh", "-c", cmd])
            except Exception as exc:  # pragma: no cover
                os.write(2, ("harness child exec failed: %r\n" % exc).encode())
            finally:
                os._exit(127)
        self.pid = pid
        self.master = master
        fcntl.ioctl(master, termios.TIOCSWINSZ,
                    struct.pack("HHHH", ROWS, COLS, 0, 0))
        fl = fcntl.fcntl(master, fcntl.F_GETFL)
        fcntl.fcntl(master, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        CLIENTS.append(self)

    # -- byte plumbing ------------------------------------------------------

    def _pump(self, wait):
        """Move any pending output bytes into self.buf. True if data arrived."""
        if self.closed or self.eof:
            return False
        try:
            r, _, _ = select.select([self.master], [], [], wait)
        except OSError:
            self.eof = True
            return False
        if not r:
            return False
        try:
            data = os.read(self.master, 65536)
        except BlockingIOError:
            return False
        except OSError:          # EIO: pty gone
            self.eof = True
            return False
        if not data:
            self.eof = True
            return False
        self.buf += data
        return True

    def read_until(self, pattern=None, total=15.0, idle=None):
        """Read output until `pattern` (compiled bytes regex) matches the whole
        transcript, or timeouts expire. idle=N stops after N quiet seconds.
        Returns the match object or None."""
        deadline = time.time() + total
        last_data = time.time()
        while True:
            if pattern is not None:
                m = pattern.search(bytes(self.buf))
                if m:
                    return m
            if self.eof or self.closed:
                return None
            now = time.time()
            if now >= deadline:
                return None
            if idle is not None and now - last_data >= idle:
                return None
            if self._pump(0.1):
                last_data = time.time()

    def drain(self, seconds=1.0, idle=0.5):
        self.read_until(None, total=seconds, idle=idle)

    def send(self, data):
        if self.closed:
            raise RuntimeError("send() on closed client %s" % self.label)
        view = memoryview(bytes(data))
        while len(view):
            try:
                n = os.write(self.master, view)
            except BlockingIOError:
                select.select([], [self.master], [], 1.0)
                continue
            view = view[n:]

    # -- lifecycle ----------------------------------------------------------

    def kill_abrupt(self):
        """Simulate the user closing the terminal window: SIGHUP to the client
        process group, then close the pty master. NO graceful detach."""
        if self.closed:
            return
        self._pump(0.05)  # keep any last bytes for T1
        try:
            os.killpg(self.pid, signal.SIGHUP)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            os.close(self.master)
        except OSError:
            pass
        self.closed = True
        self._reap()

    def _reap(self, timeout=5.0):
        end = time.time() + timeout
        while time.time() < end:
            try:
                pid, _ = os.waitpid(self.pid, os.WNOHANG)
            except ChildProcessError:
                return
            if pid:
                return
            time.sleep(0.05)
        try:
            os.killpg(self.pid, signal.SIGKILL)
        except Exception:
            pass
        try:
            os.waitpid(self.pid, 0)
        except Exception:
            pass


# -- shell-interaction helpers ----------------------------------------------

def probe(client, timeout=5.0, tag="PING"):
    """Type an echo whose *typed* text can never match its own output (split
    with '' quotes), and wait for the output token. Returns (ok, token)."""
    token = "%s-%s" % (tag, os.urandom(4).hex())
    typed = "echo %s''%s\n" % (token[:2], token[2:])
    client.send(typed.encode())
    m = client.read_until(re.compile(re.escape(token.encode())), total=timeout)
    return m is not None, token


def wait_ready(client, attempts=4, per_probe=5.0):
    """Wait until the shell behind the client answers an echo probe."""
    client.drain(seconds=1.5, idle=0.8)
    for _ in range(attempts):
        ok, _ = probe(client, timeout=per_probe)
        if ok:
            return True
    return False


def t5_interactivity(client, context, t5_records):
    """R2 check, run after every attach: stray bytes (arrows, bare Esc) must
    not capture the session; an echo must answer within 5 s."""
    entry = {"context": context, "client": client.label, "pass": False}
    try:
        client.send(b"\x1b[A\x1b[B\x1b[C\x1b[D\x1b[H\x1b")  # arrows, Home, Esc
        time.sleep(0.3)
        client.send(b"\x03")  # Ctrl-C must reach the shell and clear the line
        time.sleep(0.2)
        ok, token = probe(client, timeout=5.0)
        entry["pass"] = ok
        entry["token"] = token
    except Exception as exc:
        entry["error"] = repr(exc)
    t5_records.append(entry)
    return entry["pass"]


def get_shell_pid(client, timeout=6.0):
    """Ask the shell for $$ using a per-call unique tag (so replayed history
    from an earlier query can never satisfy a later one). The typed line
    contains '' inside the tag and a literal $$, so the input echo can never
    match either. Returns the pid or None."""
    tag = "SH%s" % os.urandom(3).hex()
    client.send(("echo %s''%s_$$\n" % (tag[:2], tag[2:])).encode())
    m = client.read_until(
        re.compile((re.escape(tag) + r"_(\d+)").encode()), total=timeout)
    return int(m.group(1)) if m else None


def counter_last_value(path):
    try:
        with open(path, "r") as fh:
            lines = fh.read().split()
    except OSError:
        return None
    for item in reversed(lines):
        try:
            return int(item)
        except ValueError:
            continue
    return None


def pid_alive_running(pid, expect_substr):
    """True iff pid exists AND its cmdline contains expect_substr (guards
    against pid reuse)."""
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as fh:
            cmdline = fh.read().replace(b"\x00", b" ")
    except OSError:
        return False
    return expect_substr.encode() in cmdline


# -- the test run ------------------------------------------------------------

def run(cfg, out_path, transcripts_dir):
    name = cfg.get("name") or "unnamed-tool"
    run_id = "sp%s" % os.urandom(3).hex()

    def subst(text):
        return text.replace("{run_id}", run_id)

    cmd_tpl = subst(cfg["new_or_attach_cmd"])
    sessions = {n: "%s-S%d" % (run_id, n) for n in range(1, 6)}

    def spawn(n, label):
        return PtyClient(cmd_tpl.replace("{session}", sessions[n]), label)

    results = {
        "harness_version": HARNESS_VERSION,
        "tool": name,
        "run_id": run_id,
        "config": cfg,
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "pty": {"cols": COLS, "rows": ROWS, "term": "xterm-256color"},
        "tests": {
            t: {"pass": False, "detail": "not run"}
            for t in ("T1", "T2", "T3", "T4", "T5")
        },
    }
    t5_records = []
    counter = {"pid": None, "script": None}
    workdir = tempfile.mkdtemp(prefix="spec-harness-%s-" % run_id)

    def fail(test, detail):
        results["tests"][test] = {"pass": False, "detail": detail}

    old_alarm = signal.signal(signal.SIGALRM, _watchdog)
    signal.alarm(900)  # hard cap on a whole run
    try:
        if cfg.get("pre"):
            rc = os.system(subst(cfg["pre"]))
            results["pre_exit"] = rc

        mk = "MK%sN" % run_id            # T2 marker prefix
        mk_num_re = re.compile((re.escape(mk) + r"(\d+)\b").encode())

        # ---------------- T2: replay of pre-attach history (R4) -------------
        cli_a = spawn(1, "T2-A-S1")
        if not wait_ready(cli_a):
            fail("T2", "initial client never became interactive")
            fail("T3", "skipped: initial client never became interactive")
            fail("T4", "skipped: initial client never became interactive")
            cli_b = None
        else:
            t5_interactivity(cli_a, "initial-attach-S1", t5_records)
            cli_a.send(('for i in $(seq 1 200); do echo "%s$i"; done\n'
                        % mk).encode())
            m = cli_a.read_until(
                re.compile((re.escape(mk) + r"200\b").encode()), total=30.0)
            if m is None:
                fail("T2", "marker generator never printed marker 200 in "
                           "client A")
            time.sleep(0.8)              # let the tool absorb the output
            cli_a.kill_abrupt()          # user closes the terminal window
            time.sleep(1.2)

            cli_b = spawn(1, "T2-B-S1")
            cli_b.read_until(
                re.compile((re.escape(mk) + r"200\b").encode()),
                total=25.0, idle=4.0)
            stream_b = bytes(cli_b.buf)
            nums = [int(v) for v in mk_num_re.findall(stream_b)]
            firsts, seen = [], set()
            for v in nums:
                if 1 <= v <= 200 and v not in seen:
                    seen.add(v)
                    firsts.append(v)
            ascending = firsts == sorted(firsts)
            # A tool could replay history and then erase the terminal's
            # scrollback (ED3, ESC[3J) — that would defeat R4 on a real
            # terminal while still "showing" the markers in the byte stream.
            first_marker = mk_num_re.search(stream_b)
            ed3_after_replay = bool(
                first_marker
                and re.search(rb"\x1b\[(3|[0-9;]*;3)J",
                              stream_b[first_marker.start():]))
            results["tests"]["T2"] = {
                "pass": (len(seen) >= 190 and ascending
                         and not ed3_after_replay),
                "distinct_markers": len(seen),
                "required": 190,
                "ascending_first_occurrence": ascending,
                "scrollback_erase_after_replay": ed3_after_replay,
                "detail": "%d/200 distinct markers replayed to fresh client, "
                          "ascending=%s, ED3-after-replay=%s"
                          % (len(seen), ascending, ed3_after_replay),
            }
            if not wait_ready(cli_b):
                fail("T3", "client B not interactive after re-attach")
                cli_b.kill_abrupt()
                cli_b = None
            else:
                t5_interactivity(cli_b, "reattach-S1-after-abrupt-kill",
                                 t5_records)

        # ---------------- T3: persistence of running process (R1) -----------
        if cli_b is not None:
            counter["script"] = os.path.join(workdir, "counter.py")
            counter_out = os.path.join(workdir, "counter.out")
            with open(counter["script"], "w") as fh:
                fh.write(
                    "import sys, time\n"
                    "f = open(sys.argv[1], 'w', buffering=1)\n"
                    "i = 0\n"
                    "while True:\n"
                    "    i += 1\n"
                    "    f.write('%d\\n' % i)\n"
                    "    time.sleep(0.2)\n")
            # Session identity: the shell pid must be THE SAME after
            # re-attach. A tool that merely leaves orphans behind (or spawns
            # a fresh shell per attach, like bare bash) fails this.
            shell_pid_before = get_shell_pid(cli_b)
            # No nohup/setsid/disown: the counter must survive ONLY if the
            # tool truly keeps the session's shell alive.
            cli_b.send(("python3 %s %s & echo CP''ID_$!\n"
                        % (counter["script"], counter_out)).encode())
            m = cli_b.read_until(re.compile(rb"CPID_(\d+)"), total=10.0)
            t3 = {"pass": False}
            if m is None:
                t3["detail"] = "could not start counter / capture its pid"
            else:
                cpid = int(m.group(1))
                counter["pid"] = cpid
                t3["counter_pid"] = cpid
                end = time.time() + 6.0
                while counter_last_value(counter_out) is None \
                        and time.time() < end:
                    time.sleep(0.2)
                v_before = counter_last_value(counter_out)
                cli_b.kill_abrupt()      # user closes the terminal window
                time.sleep(3.0)
                alive = pid_alive_running(cpid, "counter.py")
                v1 = counter_last_value(counter_out)
                time.sleep(1.5)
                v2 = counter_last_value(counter_out)
                advancing = (v1 is not None and v2 is not None and v2 > v1)
                t3.update({
                    "started_counting": v_before is not None,
                    "pid_alive_after_client_kill": alive,
                    "count_before_kill": v_before,
                    "count_after_kill": [v1, v2],
                    "still_advancing": advancing,
                })
                cli_c = spawn(1, "T3-C-S1")
                reattach_ok = wait_ready(cli_c)
                shell_pid_after = get_shell_pid(cli_c) if reattach_ok else None
                same_shell = (shell_pid_before is not None
                              and shell_pid_after == shell_pid_before)
                if reattach_ok:
                    t5_interactivity(cli_c, "reattach-S1-after-T3-kill",
                                     t5_records)
                t3["interactive_after_reattach"] = reattach_ok
                t3["shell_pid_before_kill"] = shell_pid_before
                t3["shell_pid_after_reattach"] = shell_pid_after
                t3["same_shell_after_reattach"] = same_shell
                t3["pass"] = bool(v_before is not None and alive
                                  and advancing and reattach_ok
                                  and same_shell)
                t3["detail"] = ("counter pid %d alive=%s advancing=%s "
                                "reattach-interactive=%s same-shell=%s "
                                "($$ %s -> %s)"
                                % (cpid, alive, advancing, reattach_ok,
                                   same_shell, shell_pid_before,
                                   shell_pid_after))
                if counter["pid"] and pid_alive_running(counter["pid"],
                                                        "counter.py"):
                    try:
                        os.kill(counter["pid"], signal.SIGTERM)
                    except OSError:
                        pass
                cli_c.kill_abrupt()
            results["tests"]["T3"] = t3

        # ---------------- T4: five isolated named sessions (R5) -------------
        iso = {n: "ISO%sS%dX" % (run_id, n) for n in range(2, 6)}
        t4 = {"pass": False, "violations": [], "sessions": list(
            sessions.values())}
        setup_ok = True
        for n in range(2, 6):
            cl = spawn(n, "T4-first-S%d" % n)
            if not wait_ready(cl):
                setup_ok = False
                t4["violations"].append("S%d never became interactive on "
                                        "first attach" % n)
                cl.kill_abrupt()
                continue
            tok = iso[n]
            cl.send(("echo %s''%s\n" % (tok[:2], tok[2:])).encode())
            cl.read_until(re.compile(re.escape(tok.encode())), total=5.0)
            cl.kill_abrupt()             # user closes the terminal window
        time.sleep(1.0)

        finals = {}
        for n in range(1, 6):            # all five re-attached CONCURRENTLY
            finals[n] = spawn(n, "T4-re-S%d" % n)
        ready_map = {}
        for n in range(1, 6):
            ready_map[n] = wait_ready(finals[n])
            if ready_map[n]:
                t5_interactivity(finals[n],
                                 "concurrent-reattach-S%d" % n, t5_records)
            else:
                t4["violations"].append(
                    "S%d not interactive on concurrent re-attach" % n)
        for n in range(1, 6):
            stream = bytes(finals[n].buf)
            for m_ in range(2, 6):
                if m_ != n and iso[m_].encode() in stream:
                    t4["violations"].append(
                        "isolation breach: S%d marker seen in S%d stream"
                        % (m_, n))
            if n != 1 and mk_num_re.search(stream):
                t4["violations"].append(
                    "isolation breach: S1 T2-markers seen in S%d stream" % n)
        for n in range(1, 6):
            finals[n].kill_abrupt()
        t4["concurrently_interactive"] = sum(
            1 for v in ready_map.values() if v)
        t4["pass"] = setup_ok and not t4["violations"] and all(
            ready_map.values())
        t4["detail"] = ("%d/5 sessions concurrently interactive, "
                        "%d isolation violations"
                        % (t4["concurrently_interactive"],
                           len(t4["violations"])))
        results["tests"]["T4"] = t4

    except Exception:
        results["fatal_error"] = traceback.format_exc()
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old_alarm)
        for cl in CLIENTS:
            try:
                cl.kill_abrupt()
            except Exception:
                pass
        if counter["pid"] and pid_alive_running(counter["pid"], "counter.py"):
            try:
                os.kill(counter["pid"], signal.SIGTERM)
            except OSError:
                pass
        if cfg.get("post"):
            results["post_exit"] = os.system(subst(cfg["post"]))
        try:
            shutil.rmtree(workdir)
        except OSError:
            pass

    # ---------------- T5 aggregate (R2) --------------------------------
    results["tests"]["T5"] = {
        "pass": bool(t5_records) and all(r["pass"] for r in t5_records),
        "contexts": t5_records,
        "detail": "%d/%d attach contexts answered within 5s after stray-byte "
                  "burst" % (sum(1 for r in t5_records if r["pass"]),
                             len(t5_records)),
    }

    # ---------------- T1 byte-contract scan (R2/R3), whole lifecycle ----
    violations = []
    total_bytes = 0
    for cl in CLIENTS:
        data = bytes(cl.buf)
        total_bytes += len(data)
        for m in DECSET_ENABLE_RE.finditer(data):
            params = {int(p) for p in m.group(1).split(b";") if p}
            bad = sorted(params & set(FORBIDDEN_DECSET))
            if bad:
                violations.append({
                    "client": cl.label,
                    "offset": m.start(),
                    "sequence": m.group(0).decode("latin-1")
                                 .replace("\x1b", "ESC"),
                    "forbidden_params": [
                        {"param": p, "meaning": FORBIDDEN_DECSET[p]}
                        for p in bad],
                })
    results["tests"]["T1"] = {
        "pass": not violations,
        "bytes_scanned": total_bytes,
        "clients_scanned": len(CLIENTS),
        "violations": violations,
        "detail": "%d bytes over %d clients scanned; %d forbidden "
                  "mouse/alt-screen enable sequences"
                  % (total_bytes, len(CLIENTS), len(violations)),
    }

    if transcripts_dir:
        os.makedirs(transcripts_dir, exist_ok=True)
        for cl in CLIENTS:
            with open(os.path.join(
                    transcripts_dir, "%s.raw" % cl.label), "wb") as fh:
                fh.write(bytes(cl.buf))

    results["all_pass"] = all(
        results["tests"][t]["pass"] for t in ("T1", "T2", "T3", "T4", "T5"))
    results["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    with open(out_path, "w") as fh:
        json.dump(results, fh, indent=2)

    labels = {
        "T1": "byte-contract (R2/R3: no mouse/alt-screen enables)",
        "T2": "history replay on re-attach (R4)",
        "T3": "process persistence across client kill (R1)",
        "T4": "5 named sessions, isolated (R5)",
        "T5": "interactivity after stray bytes, every attach (R2)",
    }
    print("== harness %s | tool: %s | run: %s ==" % (
        HARNESS_VERSION, name, run_id))
    for t in ("T1", "T2", "T3", "T4", "T5"):
        r = results["tests"][t]
        print("%s  %-52s %s   %s" % (
            t, labels[t], "PASS" if r["pass"] else "FAIL",
            r.get("detail", "")))
    if "fatal_error" in results:
        print("FATAL ERROR during run:\n%s" % results["fatal_error"])
    print("OVERALL: %s" % ("PASS" if results["all_pass"] else "FAIL"))
    print("Results JSON: %s" % out_path)
    return 0 if results["all_pass"] else 1


def _watchdog(signum, frame):
    raise TimeoutError("harness watchdog: run exceeded 900s")


def main():
    ap = argparse.ArgumentParser(
        description="Spec-compliance harness for persistent terminal "
                    "session tools")
    ap.add_argument("config", help="JSON config describing the target tool")
    ap.add_argument("--out", default=None,
                    help="results JSON path (default: results-<name>.json "
                         "next to the config)")
    ap.add_argument("--transcripts", default=None,
                    help="directory to dump raw per-client byte transcripts")
    args = ap.parse_args()
    with open(args.config) as fh:
        cfg = json.load(fh)
    if "new_or_attach_cmd" not in cfg:
        print("config error: 'new_or_attach_cmd' is required", file=sys.stderr)
        return 2
    name = cfg.get("name") or os.path.splitext(
        os.path.basename(args.config))[0]
    cfg.setdefault("name", name)
    out_path = args.out or os.path.join(
        os.path.dirname(os.path.abspath(args.config)),
        "results-%s.json" % name)
    return run(cfg, out_path, args.transcripts)


if __name__ == "__main__":
    sys.exit(main())
