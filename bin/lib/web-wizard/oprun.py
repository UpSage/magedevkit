#!/usr/bin/env python3
"""Runs one dashboard operation detached from the dashboard and records it.

  oprun.py --record FILE --log FILE --cwd DIR [--lock-fd N] [--workflow] -- ARGV...

The dashboard starts this in its own session (so a dashboard restart, a
closed terminal or Ctrl+C there never stops a running action), with the
target checkout's lock already taken and passed in as --lock-fd. The lock
stays held for as long as this process — and the command it runs — lives.

The record (webcore operation record) is written at start and finish, with
the exit code; output goes to --log only (never a pipe, so nothing breaks if
the dashboard goes away).

--workflow: ARGV is a checkout's web-wizard launch.sh. The wizard takes the
checkout lock itself, so no --lock-fd. Its "Open: <url>" line is copied to
<record>.url (0600) for the dashboard to re-offer while the wizard runs, and
the record is finalized here only if the wizard server didn't (older
checkouts' servers don't write records).
"""

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import webcore  # noqa: E402

CANCEL_EXIT = 130
OPEN_RE = re.compile(r"Open:\s+(http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+)")


def watch_url(log_path, url_path, proc):
    """Copies the wizard's link from the launcher output to url_path."""
    deadline = time.time() + 60
    while time.time() < deadline and proc.poll() is None:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                m = OPEN_RE.search(f.read(65536))
        except OSError:
            m = None
        if m:
            fd = os.open(url_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(m.group(1))
            return
        time.sleep(0.2)


def cleanup(path):
    """Deletes a one-time login dir — only ever a dash-git-* temp dir."""
    if path and os.path.basename(path.rstrip("/")).startswith("dash-git-"):
        shutil.rmtree(path, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--lock-fd", type=int)
    ap.add_argument("--lock-label", default="")
    ap.add_argument("--workflow", action="store_true")
    ap.add_argument("--cleanup", help="a private temp dir (e.g. a one-time git login) to delete when the command ends")
    ap.add_argument("argv", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv

    if args.lock_fd is not None:
        # Same text format as bin/lib/ui.sh wizard_lock, so its "already
        # running" message names this process.
        text = "PID %d, started %s (dashboard: %s)\n" % (
            os.getpid(), time.strftime("%Y-%m-%d %H:%M:%S"), args.lock_label)
        os.ftruncate(args.lock_fd, 0)
        os.pwrite(args.lock_fd, text.encode(), 0)

    url_path = args.record[:-len(".json")] + ".url" if args.record.endswith(".json") else args.record + ".url"
    webcore.update_record(args.record, status="running", pid=os.getpid(), startedAt=webcore.now_iso())

    cancelled = threading.Event()
    log = open(args.log, "ab")
    log.write(("$ %s\n" % " ".join(argv)).encode())
    log.flush()
    try:
        proc = subprocess.Popen(argv, cwd=args.cwd, stdin=subprocess.DEVNULL, stdout=log,
                                stderr=subprocess.STDOUT, close_fds=True,
                                pass_fds=(args.lock_fd,) if args.lock_fd is not None else ())
    except OSError as e:
        cleanup(args.cleanup)
        log.write(("Could not start: %s\n" % e).encode())
        log.close()
        webcore.update_record(args.record, status="failed", exitCode=None, error=str(e), endedAt=webcore.now_iso())
        return 1

    def on_term(signum, frame):
        cancelled.set()
        try:
            proc.send_signal(signal.SIGTERM)
        except OSError:
            pass

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)

    if args.workflow:
        threading.Thread(target=watch_url, args=(args.log, url_path, proc), daemon=True).start()

    code = proc.wait()
    cleanup(args.cleanup)
    if args.lock_fd is not None:
        os.close(args.lock_fd)  # free the checkout before reporting "done"
    status = "done" if code == 0 else ("cancelled" if cancelled.is_set() or code in (CANCEL_EXIT, -15) else "failed")
    log.write(("\n[%s] exit %d\n" % (status, code)).encode())
    log.close()
    rec = webcore.read_record(args.record) or {}
    if not args.workflow or rec.get("status") in (None, "running", "starting"):
        webcore.update_record(args.record, status=status, exitCode=code, endedAt=webcore.now_iso())
    if args.workflow:
        try:
            os.remove(url_path)
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
