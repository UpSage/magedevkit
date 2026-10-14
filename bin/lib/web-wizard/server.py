#!/usr/bin/env python3
"""Local web front end for bin/local/setup and bin/refresh.

Runs the existing bash script as a child process with SETUP_UI=web. The
script's prompt/progress helpers (bin/lib/ui.sh) write one JSON event per
line to $SETUP_UI_DIR/events and read answers from $SETUP_UI_DIR/answer;
this server relays both to the page over SSE / a small JSON API.

Standard library only, so it works on a fresh machine before anything else
is installed. Started by bin/lib/web-wizard/launch.sh — never directly.

Security model (it carries passwords and access keys) — the shared parts
live in webcore.py:
  * listens on 127.0.0.1 only;
  * a token (from the environment, never argv) is exchanged for an HttpOnly
    SameSite=Strict session cookie named wiz_<port>, required on every request;
  * the Host header must be 127.0.0.1/localhost:<port> (DNS rebinding);
  * state-changing requests also need an X-Wizard header (CSRF);
  * answers are never logged; submitted passwords are redacted from the log.

Each run also keeps an operation record (logs/operations/<id>.json, no
secrets) so bin/local/dashboard can show it — see webcore.py.
"""

import http.server
import json
import os
import re
import secrets
import shutil
import signal
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # the platform repo
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import webcore  # noqa: E402

PLATFORM_VERSION = webcore.platform_version(CODE_DIR)

UI_DIR = os.path.join(HERE, "ui")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/kit.js": ("kit.js", "text/javascript; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/wizard.css": ("wizard.css", "text/css; charset=utf-8"),
}
LOG_KEEP = 4000          # log lines kept in memory for the page
STATE_LOG_TAIL = 400     # lines sent with /api/state
NOTE_LINES = 3           # log lines shown on a re-asked question
UPLOAD_CHUNK = 1 << 20
GZIP_MAGIC = b"\x1f\x8b"
JSON_MAX = 1 << 20      # an uploaded auth.json is a few hundred bytes
SECRET_TYPES = {"password"}
# Log lines worth showing on the next question card: validation messages
# from a re-ask, or anything that reads like a warning.
NOTE_RE = re.compile(r"⚠|isn.t|won.t work|didn.t|invalid|already (used|served)|can.t|no file|not found|please|error|failed|try again", re.I)
CANCEL_EXIT = 130
CHANGE_ANSWERS_EXIT = 75  # "Change answers" at a review step (bin/lib/ui.sh UI_CHANGE_ANSWERS_EXIT)


class Wizard:
    """All mutable state, guarded by one condition variable."""

    def __init__(self, flow, platform_dir, token):
        self.flow = flow
        self.platform_dir = platform_dir
        self.token = token
        self.cv = threading.Condition()
        self.seq = 0
        self.steps = []           # [{"title", "status"}], status: upcoming|current|done|skipped|failed
        self.current = 0          # 1-based current step, 0 = none yet
        self.tasks = {}           # step -> [{"label","status","started","secs"}]
        self.question = None      # pending question dict (with "id")
        self.qid = 0
        self.notes = []           # recent log lines since the last answer
        self.task_since_answer = False
        self.last_label = None
        self.results = []
        self.log = []
        self.log_total = 0
        self.status = "starting"  # starting|running|done|failed|cancelled
        self.exit_code = None
        self.error = None
        self.cancel_after_task = False
        self.secrets = set()
        self.uploads = []         # completed upload paths
        self.secret_uploads = []  # of those, the ones holding credentials (auth.json)
        self.upload_active = False
        self.keep_uploads = False
        self.clients = 0
        self.last_client_seen = time.time()
        self.closed = False
        self.started = time.time()
        self.child = None
        self.ui_dir = None
        self.log_file = None
        self.record_path = None   # logs/operations/<id>.json
        self.record_extra = {}
        # What to type to start this wizard again (shown on the stopped page).
        if os.path.isfile(os.path.join(platform_dir, ".platform-instance")):
            self.rerun_command = "mdk -i %s %s-wizard" % (os.path.basename(platform_dir), flow)
        else:
            self.rerun_command = "cd %s && bin/local/%s-wizard" % (platform_dir, flow)

    # -- helpers (call with self.cv held) ---------------------------------

    def bump(self):
        self.seq += 1
        self.cv.notify_all()

    def redact(self, line):
        return webcore.redact_literal(line, self.secrets)

    def save_record(self):
        """Writes the operation record (call with self.cv held). Never fatal."""
        if not self.record_path:
            return
        step = self.steps[self.current - 1]["title"] if 1 <= self.current <= len(self.steps) else None
        fields = dict(self.record_extra)
        fields.update({
            "status": self.status, "step": step, "stepNumber": self.current, "steps": len(self.steps),
            "exitCode": self.exit_code, "error": self.error,
            "endedAt": webcore.now_iso() if self.status in ("done", "failed", "cancelled") else None,
        })
        try:
            webcore.update_record(self.record_path, **fields)
        except OSError:
            pass

    def snapshot(self, with_log=True):
        snap = {
            "seq": self.seq,
            "flow": self.flow,
            "platformVersion": PLATFORM_VERSION,
            "steps": self.steps,
            "current": self.current,
            "tasks": {str(k): v for k, v in self.tasks.items()},
            "question": self.question,
            "results": self.results,
            "status": self.status,
            "exitCode": self.exit_code,
            "error": self.error,
            "cancelAfterTask": self.cancel_after_task,
            "uploadsKept": [p for p in self.uploads if os.path.exists(p) and p not in self.secret_uploads]
                           if self.status in ("failed", "cancelled") else [],
            "logTotal": self.log_total,
            "rerunCommand": self.rerun_command,
            "now": time.time(),
        }
        if with_log:
            snap["log"] = self.log[-STATE_LOG_TAIL:]
        return snap

    def set_step(self, n):
        n = int(n)
        for i, st in enumerate(self.steps, start=1):
            if i < n and st["status"] in ("current", "upcoming"):
                st["status"] = "done"
        if 1 <= n <= len(self.steps):
            if self.steps[n - 1]["status"] != "skipped":
                self.steps[n - 1]["status"] = "current"
            self.current = n
        self.tasks.setdefault(n, [])

    # -- events from bash -------------------------------------------------

    def on_event(self, ev):
        t = ev.get("t")
        with self.cv:
            if t == "flow":
                self.flow = ev.get("flow", self.flow)
                self.steps = [{"title": s, "status": "upcoming"} for s in ev.get("steps", [])]
            elif t == "step":
                self.set_step(ev.get("n", 0))
            elif t == "step_skip":
                n = int(ev.get("n", 0))
                if 1 <= n <= len(self.steps):
                    for i, st in enumerate(self.steps, start=1):
                        if i < n and st["status"] in ("current", "upcoming"):
                            st["status"] = "done"
                    self.steps[n - 1]["status"] = "skipped"
            elif t == "task_start":
                step = self.current or 0
                self.tasks.setdefault(step, []).append(
                    {"label": ev.get("label", ""), "status": "running", "started": time.time(), "secs": None})
                self.task_since_answer = True
            elif t in ("task_done", "task_fail"):
                for task in reversed(self.tasks.get(self.current or 0, [])):
                    if task["status"] == "running":
                        task["status"] = "done" if t == "task_done" else "failed"
                        task["secs"] = ev.get("secs", round(time.time() - task["started"]))
                        break
            elif t == "ask":
                self.qid += 1
                label = ev.get("label", "")
                reask = label == self.last_label
                notes = [] if self.task_since_answer else [
                    n for n in self.notes if reask or NOTE_RE.search(n)][-NOTE_LINES:]
                self.last_label = label
                q = {
                    "id": self.qid,
                    "type": ev.get("type", "text"),
                    "label": label,
                    "default": ev.get("default", ""),
                    "options": ev.get("options", []),
                    "notes": notes,
                    "step": self.current,
                }
                self.question = q
            elif t == "result":
                self.results.append({k: ev.get(k, "") for k in ("kind", "label", "value")})
            elif t == "error":
                self.error = ev.get("message", "")
            self.bump()
            if t in ("flow", "step", "step_skip", "error"):
                self.save_record()

    def on_log_line(self, raw):
        line = webcore.clean_line(raw)
        with self.cv:
            line = self.redact(line)
            self.log.append(line)
            self.log_total += 1
            if len(self.log) > LOG_KEEP:
                del self.log[: len(self.log) - LOG_KEEP]
            if line.strip():
                self.notes.append(line)
                del self.notes[:-10]
            self.bump()
        if self.log_file:
            try:
                self.log_file.write(line + "\n")
                self.log_file.flush()
            except OSError:
                pass
        # Mirror to the terminal that launched the wizard.
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except OSError:
            pass

    # -- actions from the page --------------------------------------------

    def answer(self, qid, value):
        with self.cv:
            q = self.question
            if q is None or q["id"] != qid:
                return 409, "That question was already answered (maybe in another tab)."
            if "\n" in value or "\r" in value:
                return 400, "Answers can't contain line breaks."
            if q["type"] in SECRET_TYPES and value:
                self.secrets.add(value)
            if q["type"] == "file" and value.startswith(os.path.join(self.platform_dir, "db", "db-refresh", "uploads")):
                if value not in self.uploads:
                    return 400, "Unknown upload."
            self.question = None
            self.notes = []
            self.task_since_answer = False
            self.bump()
        # Opening the FIFO blocks until bash opens it for reading, which it
        # does right after sending the question.
        def write():
            try:
                fd = os.open(os.path.join(self.ui_dir, "answer"), os.O_WRONLY)
                with os.fdopen(fd, "w") as f:
                    f.write(value + "\n")
            except OSError:
                pass
        threading.Thread(target=write, daemon=True).start()
        return 200, "ok"

    def cancel(self, mode):
        with self.cv:
            if self.status not in ("running", "starting"):
                return
            now = mode == "now" or self.question is not None
            if not now:
                self.cancel_after_task = True
                open(os.path.join(self.ui_dir, "cancel"), "w").close()
                self.bump()
                return
        self.kill_child()

    def kill_child(self):
        child = self.child
        if child and child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    # -- lifecycle ---------------------------------------------------------

    def finish(self, code):
        with self.cv:
            self.exit_code = code
            if code == 0:
                self.status = "done"
                for st in self.steps:
                    if st["status"] in ("current", "upcoming"):
                        st["status"] = "done"
            elif code == CANCEL_EXIT or code < 0 or self.cancel_after_task:
                self.status = "cancelled"
            else:
                self.status = "failed"
                if 1 <= self.current <= len(self.steps):
                    self.steps[self.current - 1]["status"] = "failed"
            for tasks in self.tasks.values():
                for task in tasks:
                    if task["status"] == "running":
                        task["status"] = "failed" if self.status == "failed" else "stopped"
            self.question = None
            if self.status == "done":
                self.delete_uploads()
            self.delete_uploads(self.secret_uploads)  # credentials never outlive the run
            self.bump()
            self.save_record()

    def reset_for_rerun(self):
        """Back to a fresh run on the same page (call with self.cv held). Kept:
        the log so far, typed-password redaction, and earlier uploads (a dump
        uploaded before a failure can be reused by its path)."""
        self.steps = []
        self.current = 0
        self.tasks = {}
        self.question = None
        self.notes = []
        self.task_since_answer = False
        self.last_label = None
        self.results = []
        self.status = "starting"
        self.exit_code = None
        self.error = None
        self.cancel_after_task = False
        self.started = time.time()
        try:
            os.remove(os.path.join(self.ui_dir, "cancel"))
        except OSError:
            pass
        self.bump()

    def delete_uploads(self, paths=None):
        for p in list(self.uploads if paths is None else paths):
            try:
                os.remove(p)
            except OSError:
                pass

    def uploads_dir(self):
        d = os.path.join(self.platform_dir, "db", "db-refresh", "uploads")
        os.makedirs(d, mode=0o700, exist_ok=True)
        return d


def free_bytes(path):
    override = os.environ.get("WIZARD_DISK_FREE_OVERRIDE")
    if override:
        return int(override)
    return shutil.disk_usage(path).free


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


WIZARD_CSP = webcore.csp(" https:")  # https: for the finish page's site-reachability probe


def make_handler(wiz, port, shutdown, restart=None):
    class Handler(webcore.SecureHandler):
        hosts = webcore.allowed_hosts(port)
        token = wiz.token
        cookie_name = "wiz_%d" % port
        csp_header = WIZARD_CSP
        static = STATIC
        ui_dir = UI_DIR

        # -- routes ----------------------------------------------------------

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if self.login_or_static(url):
                return
            if not self.guard():
                return
            if url.path == "/api/state":
                with wiz.cv:
                    return self.send(200, wiz.snapshot())
            if url.path == "/api/log":
                with wiz.cv:
                    body = "\n".join(wiz.log) + "\n"
                return self.send(200, body, "text/plain; charset=utf-8", {
                    "Content-Disposition": 'attachment; filename="%s-%s.log"' % (
                        wiz.flow, time.strftime("%Y%m%d-%H%M%S", time.localtime(wiz.started)))})
            if url.path == "/api/events":
                return self.events(url)
            return self.send(404, {"error": "Not found."})

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            if not self.guard(mutating=True):
                return
            body = self.read_json()
            if not isinstance(body, dict):
                return self.send(400, {"error": "Bad request."})
            if url.path == "/api/answer":
                try:
                    qid = int(body.get("id", -1))
                except (TypeError, ValueError):
                    return self.send(400, {"error": "Bad request."})
                code, msg = wiz.answer(qid, str(body.get("value", "")))
                return self.send(code, {"ok": code == 200, "error": None if code == 200 else msg})
            if url.path == "/api/cancel":
                wiz.cancel(body.get("mode", "after_task"))
                return self.send(200, {"ok": True})
            if url.path == "/api/keep-uploads":
                with wiz.cv:
                    wiz.keep_uploads = bool(body.get("keep", True))
                return self.send(200, {"ok": True})
            if url.path == "/api/restart":
                code, msg = restart() if restart else (404, "Not found.")
                return self.send(code, {"ok": code == 200, "error": None if code == 200 else msg})
            if url.path == "/api/quit":
                self.send(200, {"ok": True})
                threading.Thread(target=shutdown, args=("Closed from the wizard page.",), daemon=True).start()
                return
            return self.send(404, {"error": "Not found."})

        def do_PUT(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path != "/api/upload":
                return self.send(404, {"error": "Not found."})
            if not self.guard(mutating=True):
                return
            qs = urllib.parse.parse_qs(url.query)
            try:
                qid = int(qs.get("q", ["-1"])[0])
                size = int(self.headers.get("Content-Length") or -1)
            except ValueError:
                return self.send(400, {"error": "Bad request."})
            name = os.path.basename(qs.get("name", ["dump.sql.gz"])[0]) or "dump.sql.gz"
            with wiz.cv:
                q = wiz.question
                if q is None or q["id"] != qid or q["type"] != "file":
                    return self.send(409, {"error": "This upload isn't expected right now — the question changed."})
                if wiz.upload_active:
                    return self.send(409, {"error": "Another upload is already running."})
                if size <= 0:
                    return self.send(411, {"error": "Upload size is required."})
                # What the question expects: a gzipped SQL dump (default) or,
                # with option "kind=json", a JSON file such as auth.json.
                kind = "json" if "kind=json" in (q.get("options") or []) else "sqlgz"
                updir = wiz.uploads_dir()
                if kind == "json":
                    if size > JSON_MAX:
                        self.close_connection = True
                        return self.send(413, {"error": "That file is too big for an auth.json (max 1 MB)."})
                else:
                    need = size * 9
                    have = free_bytes(updir)
                    if have < need:
                        return self.send(507, {"error": "Not enough disk space for this dump: need about %s free, have %s." % (human(need), human(have))})
                wiz.upload_active = True
            if kind == "json":
                return self.upload_json(updir, size, name)
            dest = os.path.join(updir, secrets.token_hex(8) + ".sql.gz")
            part = dest + ".part"
            ok = False
            try:
                fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    remaining = size
                    first = self.rfile.read(min(2, remaining))
                    if first != GZIP_MAGIC[: len(first)] or len(first) < 2:
                        # Drain nothing more; the client sees the error at once.
                        self.close_connection = True
                        return self.send(415, {"error": "That isn't a gzip file — the dump must be a .sql.gz."})
                    out.write(first)
                    remaining -= len(first)
                    while remaining > 0:
                        chunk = self.rfile.read(min(UPLOAD_CHUNK, remaining))
                        if not chunk:
                            raise ConnectionError("upload interrupted")
                        out.write(chunk)
                        remaining -= len(chunk)
                os.replace(part, dest)
                ok = True
                with wiz.cv:
                    wiz.uploads.append(dest)
                return self.send(200, {"ok": True, "path": dest, "name": name, "size": size})
            except (OSError, ConnectionError):
                self.close_connection = True
                try:
                    return self.send(400, {"error": "The upload was interrupted."})
                except OSError:
                    return None
            finally:
                if not ok:
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                with wiz.cv:
                    wiz.upload_active = False

        def upload_json(self, updir, size, name):
            """A small JSON upload (auth.json): validated in memory, stored 0600,
            deleted when the run ends whatever happens."""
            try:
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ConnectionError("upload interrupted")
                try:
                    parsed = json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, ValueError):
                    parsed = None
                if not isinstance(parsed, dict):
                    return self.send(415, {"error": "That isn't a valid JSON file — choose your auth.json."})
                dest = os.path.join(updir, secrets.token_hex(8) + ".json")
                fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(data)
                with wiz.cv:
                    wiz.uploads.append(dest)
                    wiz.secret_uploads.append(dest)
                return self.send(200, {"ok": True, "path": dest, "name": name, "size": size})
            except (OSError, ConnectionError):
                self.close_connection = True
                try:
                    return self.send(400, {"error": "The upload was interrupted."})
                except OSError:
                    return None
            finally:
                with wiz.cv:
                    wiz.upload_active = False

        def events(self, url):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            with wiz.cv:
                wiz.clients += 1
                seq = -1
                log_sent = wiz.log_total
            try:
                while True:
                    with wiz.cv:
                        if wiz.seq == seq and not wiz.closed:
                            wiz.cv.wait(timeout=15)
                        if wiz.closed:
                            self.wfile.write(b"event: closed\ndata: {}\n\n")
                            self.wfile.flush()
                            return
                        if wiz.seq == seq:
                            payload = b": keepalive\n\n"
                        else:
                            seq = wiz.seq
                            new_lines = min(wiz.log_total - log_sent, len(wiz.log))
                            lines = wiz.log[len(wiz.log) - new_lines:] if new_lines > 0 else []
                            log_sent = wiz.log_total
                            snap = wiz.snapshot(with_log=False)
                            snap["newLog"] = lines
                            payload = ("event: state\ndata: %s\n\n" % json.dumps(snap)).encode()
                    self.wfile.write(payload)
                    self.wfile.flush()
            except (OSError, ConnectionError):
                return
            finally:
                with wiz.cv:
                    wiz.clients -= 1
                    wiz.last_client_seen = time.time()

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def read_events(wiz, path):
    fd = os.open(path, os.O_RDWR)  # RDWR: never sees EOF between writers
    buf = b""
    while True:
        try:
            data = os.read(fd, 65536)
        except OSError:
            return
        if not data:
            time.sleep(0.05)
            continue
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            try:
                event = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(event, dict):
                try:
                    wiz.on_event(event)
                except (TypeError, ValueError, KeyError, AttributeError):
                    pass  # one malformed event must not stop the reader


def read_output(wiz, stream):
    for raw in iter(stream.readline, b""):
        wiz.on_log_line(raw.decode("utf-8", "replace"))


FLOW_NAMES = {"setup": "Setup", "refresh": "Refresh", "version": "Version switch", "ownership": "Ownership switch",
              "pipeline": "Pipeline kit", "rebuild": "Magento rebuild", "account": "AWS connection",
              "provision": "AWS environment"}


def rerun_hint(platform_dir, script):
    """What to type to run an ownership switch again (it has no browser
    wizard of its own to re-run): bin/ownership/<model>."""
    model = os.path.basename(script)
    if os.path.isfile(os.path.join(platform_dir, ".platform-instance")):
        return "mdk -i %s ownership %s" % (os.path.basename(platform_dir), model)
    return "cd %s && bin/ownership/%s" % (platform_dir, model)


class Session:
    """One wizard server: the HTTP server, the flow's script (re-launched on
    Run again / Change answers on the same page) and their clean-up."""

    def __init__(self, flow, platform_dir, script, token, port, idle_secs, url_file):
        self.flow, self.platform_dir, self.script, self.idle_secs = flow, platform_dir, script, idle_secs
        self.name = FLOW_NAMES.get(flow, "Setup")
        self.wiz = wiz = Wizard(flow, platform_dir, token)
        if flow == "ownership":
            wiz.rerun_command = rerun_hint(platform_dir, script)
        wiz.ui_dir = tempfile.mkdtemp(prefix="web-wizard-")
        os.chmod(wiz.ui_dir, 0o700)
        for name in ("events", "answer"):
            os.mkfifo(os.path.join(wiz.ui_dir, name), 0o600)
        self.logs = os.path.join(platform_dir, "logs")
        os.makedirs(self.logs, exist_ok=True)
        self.done = threading.Event()
        self.launch_lock = threading.Lock()
        self.link_files = []
        self.last_int = 0.0

        self.httpd = Server(("127.0.0.1", port), make_handler(wiz, port, self.shutdown))
        port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = make_handler(wiz, port, self.shutdown, self.restart)
        self.url = "http://127.0.0.1:%d/?token=%s" % (port, token)
        if url_file:
            with open(url_file, "w") as f:
                f.write(self.url)
        self.env = webcore.base_env(SETUP_UI="web", SETUP_UI_DIR=wiz.ui_dir, UI_PLATFORM_DIR=CODE_DIR)

    # -- runs ------------------------------------------------------------------

    def launch(self, op_id=""):
        """Starts one run of the script: new log file, new operation record."""
        wiz, flow = self.wiz, self.flow
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = flow
        n = 1
        # A quick "Run again" can land in the same second: never share a log/record.
        while os.path.exists(os.path.join(self.logs, "%s-%s.log" % (name, stamp))):
            n += 1
            name = "%s-%d" % (flow, n)
        log_path = os.path.join(self.logs, "%s-%s.log" % (name, stamp))
        if wiz.log_file:
            try:
                wiz.log_file.close()
            except OSError:
                pass
        wiz.log_file = webcore.open_private(log_path, "a", encoding="utf-8")
        # Operation record for the dashboard (the first run may reuse the
        # record the dashboard created; the server fills in the progress).
        if not webcore.OP_ID_RE.match(op_id):
            op_id = webcore.new_op_id(name, stamp)
        wiz.record_path = os.path.join(webcore.operations_dir(self.platform_dir, create=True), op_id + ".json")
        existing = webcore.read_record(wiz.record_path) or {}
        # The page's link next to the record (0600, removed when the wizard
        # closes), so the dashboard can reopen this run — also after Run again.
        link_file = wiz.record_path[:-len(".json")] + ".url"
        try:
            fd = os.open(link_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.url)
            self.link_files.append(link_file)
        except OSError:
            pass
        wiz.record_extra = {
            "id": op_id, "kind": flow, "label": FLOW_NAMES.get(flow, flow),
            "source": existing.get("source") or wiz.record_extra.get("source", "wizard"),
            "startedAt": existing.get("startedAt") or webcore.now_iso(),
            "serverPid": os.getpid(), "logFile": os.path.relpath(log_path, self.platform_dir),
        }
        wiz.child = subprocess.Popen(
            [self.script], cwd=self.platform_dir, env=self.env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        with wiz.cv:
            wiz.status = "running"
            wiz.record_extra["childPid"] = wiz.child.pid
            wiz.bump()
            wiz.save_record()
        reader = threading.Thread(target=read_output, args=(wiz, wiz.child.stdout), daemon=True)
        reader.start()
        threading.Thread(target=self.wait_child, args=(wiz.child, reader, log_path), daemon=True).start()

    def wait_child(self, child, reader, log_path):
        code = child.wait()
        reader.join(timeout=5)
        if code == CHANGE_ANSWERS_EXIT and not self.done.is_set():
            # Close this run's record (stopped at the review on purpose), then
            # start the flow again as a new run on the same page.
            self.wiz.finish(CANCEL_EXIT)
            self.restart_for_answers()
            return
        self.wiz.finish(code)
        word = {"done": "finished", "failed": "failed", "cancelled": "was cancelled"}[self.wiz.status]
        say("%s %s (exit %d). The page stays open for the summary — click Close wizard (or Run again) there, or press Ctrl+C here."
            % (self.name, word, code))
        say("Full log: %s" % os.path.relpath(log_path, self.platform_dir), blank=False)

    def restart_for_answers(self):
        """Change answers — the script stopped at its review step so the flow
        can start over with the answers given so far as the defaults."""
        with self.launch_lock:
            with self.wiz.cv:
                if self.done.is_set():
                    return
                self.wiz.reset_for_rerun()
            self.wiz.on_log_line("──────── Changing answers: starting the %s again ────────"
                                 % FLOW_NAMES.get(self.flow, "wizard").lower())
            say("Starting again from the first question to change answers (same page).")
            self.launch()

    def restart(self):
        """Run again — offered on the page after a failure or cancel."""
        wiz = self.wiz
        with self.launch_lock:
            with wiz.cv:
                if wiz.status not in ("failed", "cancelled") or (wiz.child and wiz.child.poll() is None):
                    return 409, "The wizard can only run again after it stopped."
                if self.done.is_set():
                    return 409, "The wizard is closing."
                wiz.reset_for_rerun()
            wiz.on_log_line("──────── Running the %s again ────────" % FLOW_NAMES.get(self.flow, "wizard").lower())
            self.launch()
        return 200, "ok"

    # -- stopping --------------------------------------------------------------

    def shutdown(self, reason):
        if self.done.is_set():
            return
        self.done.set()
        say(reason)
        self.wiz.kill_child()
        with self.wiz.cv:
            self.wiz.closed = True
            self.wiz.bump()
        time.sleep(0.3)
        self.httpd.shutdown()

    def shutdown_async(self, reason):
        threading.Thread(target=self.shutdown, args=(reason,), daemon=True).start()

    def idle_watch(self):
        wiz = self.wiz
        while not self.done.is_set():
            time.sleep(5)
            with wiz.cv:
                finished = wiz.status in ("done", "failed", "cancelled")
                idle = wiz.clients == 0 and time.time() - wiz.last_client_seen > self.idle_secs
            if finished and idle:
                self.shutdown("No wizard page open for %d minutes — closing." % (self.idle_secs // 60))

    def on_sigint(self, signum, frame):
        wiz = self.wiz
        now = time.time()
        if wiz.child.poll() is None and now - self.last_int > 5:
            self.last_int = now
            with wiz.cv:
                step = wiz.steps[wiz.current - 1]["title"] if 1 <= wiz.current <= len(wiz.steps) else "starting"
                where = "Step %d/%d · %s" % (wiz.current, len(wiz.steps), step) if wiz.current else step
            say("%s is running (%s). Press Ctrl+C again within 5 s to stop it, or use Cancel on the page." % (self.name, where))
            return
        self.shutdown_async("Stopped from the terminal.")

    def cleanup(self):
        """After the server stopped: end the child, finish its record, remove
        uploads (unless kept), links, the FIFO dir; close the log."""
        wiz = self.wiz
        if wiz.child.poll() is None:
            wiz.kill_child()
            try:
                wiz.child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(wiz.child.pid, signal.SIGKILL)
                wiz.child.wait()
        if wiz.status in ("running", "starting"):
            wiz.finish(wiz.child.returncode)
        # Uploaded dumps go when the wizard closes, unless the page's "Keep the
        # uploaded file" box was ticked (offered after a failure, so a re-run
        # can use it without uploading again).
        if not wiz.keep_uploads:
            wiz.delete_uploads()
        wiz.delete_uploads(wiz.secret_uploads)
        for link_file in self.link_files:
            try:
                os.remove(link_file)
            except OSError:
                pass
        shutil.rmtree(wiz.ui_dir, ignore_errors=True)
        try:
            wiz.log_file.close()
        except OSError:
            pass

    def run(self, first_op_id):
        threading.Thread(target=read_events, args=(self.wiz, os.path.join(self.wiz.ui_dir, "events")), daemon=True).start()
        self.launch(first_op_id)
        threading.Thread(target=self.idle_watch, daemon=True).start()
        signal.signal(signal.SIGINT, self.on_sigint)
        signal.signal(signal.SIGTERM, lambda *a: self.shutdown_async("Terminated."))
        signal.signal(signal.SIGHUP, lambda *a: self.shutdown_async("Terminal closed."))
        try:
            self.httpd.serve_forever(poll_interval=0.5)
        finally:
            self.cleanup()
        code = self.wiz.child.returncode
        return 0 if code == 0 else (CANCEL_EXIT if self.wiz.status == "cancelled" else 1)


def say(text, blank=True):
    """A line for the terminal that started the wizard."""
    sys.stdout.write(("\n" if blank else "") + text + "\n")
    sys.stdout.flush()


def main():
    # server.py FLOW PLATFORM_DIR SCRIPT. PLATFORM_DIR is the instance folder
    # (bin/lib/paths.sh INSTANCE_DIR; in the legacy layout the checkout
    # itself): logs, operation records, uploads and the child's cwd live there.
    flow, platform_dir, script = sys.argv[1], os.path.abspath(sys.argv[2]), sys.argv[3]
    # The dashboard passes the id of the record it created for the first run
    # (popped first, so the script's environment doesn't inherit it).
    first_op_id = os.environ.pop("WIZARD_OPERATION_ID", "")
    session = Session(flow, platform_dir, script,
                      token=os.environ.pop("WIZARD_TOKEN"),
                      port=int(os.environ.get("WIZARD_PORT", "0")),
                      idle_secs=int(os.environ.get("WIZARD_IDLE_SECS", "1800")),
                      url_file=os.environ.get("WIZARD_URL_FILE"))
    sys.exit(session.run(first_op_id))


if __name__ == "__main__":
    main()
