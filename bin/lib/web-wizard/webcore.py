"""Pieces shared by the wizard server (server.py) and the dashboard
(dashboard.py): the security model, log-line cleanup, secret redaction and
operation records.

Standard library only. Imported by absolute sibling path — both servers put
this directory on sys.path themselves.

Security model (both servers):
  * listen on 127.0.0.1 only;
  * a token (from the environment, never argv) is exchanged for an HttpOnly
    SameSite=Strict session cookie, required on every request. Cookies ignore
    ports, so the cookie name carries the port — several wizards and the
    dashboard on 127.0.0.1 don't overwrite each other's cookie;
  * the Host header must be 127.0.0.1/localhost:<port> (DNS rebinding);
  * state-changing requests also need an X-Wizard header (CSRF).
"""

import hmac
import http.server
import json
import os
import re
import tempfile
import time
import urllib.parse

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07")
MAX_JSON = 65536
MASK = "••••"


class OpError(Exception):
    """A request the servers refuse or can't carry out: status code, a
    one-line message for the page, optional detail (already redacted)."""

    def __init__(self, code, message, detail=None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def pid_alive(pid, marker):
    """Whether pid is running a command line that contains marker (so a
    recycled pid of some other program doesn't count)."""
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        return False
    return marker in cmd


# Launcher secrets: each server pops its own at start-up, and no child process
# may ever inherit one (git hooks, composer plugins and scripts all run code).
SECRET_ENV_KEYS = ("DASHBOARD_TOKEN", "WIZARD_TOKEN")
# AWS logins exported in the shell that started the dashboard. No child gets
# them: a store's scripts use their own .env settings, and MageDevKit's AWS
# entry points name their connection explicitly (bin/lib/aws.sh), so a stray
# key can never act on the wrong account.
AWS_ENV_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN",
                "AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_CONFIG_FILE", "AWS_SHARED_CREDENTIALS_FILE",
                "AWS_CREDENTIAL_EXPIRATION", "AWS_ROLE_ARN", "AWS_WEB_IDENTITY_TOKEN_FILE")


def base_env(**extra):
    """os.environ as it is now (not at import) minus launcher secrets and AWS
    logins, plus extra."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV_KEYS and k not in AWS_ENV_KEYS}
    env.update(extra)
    return env


def git_env(**extra):
    """Environment for a non-interactive git call: never prompts, never takes
    optional locks, C locale so output parsing is stable."""
    return base_env(GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0", LC_ALL="C", **extra)


def csp(connect_extra=""):
    return ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
            "connect-src 'self'%s; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" % connect_extra)


def allowed_hosts(port):
    """Host headers accepted on <port> — call with the real port, after bind."""
    return frozenset({"127.0.0.1:%d" % port, "localhost:%d" % port})


def clean_line(raw):
    """Strips colors/escape codes; for progress-bar lines keeps what follows the last \\r."""
    line = ANSI_RE.sub("", raw).rstrip("\n")
    if "\r" in line:
        line = line.split("\r")[-1]
    return line


def redact_literal(line, secrets):
    """Replaces each known secret (3+ characters, longest first) with a mask."""
    for s in sorted((s for s in secrets if s and len(s) >= 3), key=len, reverse=True):
        line = line.replace(s, MASK)
    return line


# Patterns for secrets nobody typed into a form: config dumps, URLs with
# credentials, presigned links, bearer tokens. Used by the dashboard's log
# views on top of the literal secrets it reads from the checkout.
SECRET_PATTERNS = [
    (re.compile(r"(?i)\b((?:db_|mysql_|root_|admin_|rabbitmq_default_)?(?:password|passwd|pwd|pass|secret|token|api[_-]?key|access[_-]?key|secret[_-]?key|private[_-]?key|client[_-]?secret)"
                r"[\"']?\s*(?:=>|[:=])\s*[\"']?)([^\s\"',;]{3,})"), r"\1" + MASK),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), MASK),
    (re.compile(r"(?i)\b(aws_\w*(?:secret|token|key)\w*[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{3,}"), r"\1" + MASK),
    (re.compile(r"(?i)([\"']?(?:SecretAccessKey|SessionToken|AccessToken|RefreshToken|ClientSecret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',;]{3,}"),
     r"\1" + MASK),
    (re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1" + MASK),
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+:)[^\s@/]+@"), r"\1" + MASK + "@"),
    (re.compile(r"(?i)([?&](?:token|x-amz-signature|x-amz-credential|x-amz-security-token|signature|sig)=)[^&\s\"']+"), r"\1" + MASK),
]


def redact_patterns(line):
    for rx, repl in SECRET_PATTERNS:
        line = rx.sub(repl, line)
    return line


class SecureHandler(http.server.BaseHTTPRequestHandler):
    """Base request handler. A factory subclasses it and sets the class
    attributes below once the real port is known."""

    server_version = "wizard"
    sys_version = ""
    hosts = frozenset()
    token = ""
    cookie_name = "wiz"
    csp_header = csp()
    forbidden_text = "Open the link printed in your terminal."

    def log_message(self, *args):  # keep the terminal for script output
        pass

    def send(self, code, body=b"", ctype="application/json", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", self.csp_header)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def host_ok(self):
        return self.headers.get("Host", "") in self.hosts

    def authed(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            k, _, v = part.strip().partition("=")
            if k == self.cookie_name and hmac.compare_digest(v, self.token):
                return True
        return False

    def guard(self, mutating=False):
        if not self.host_ok():
            self.send(403, {"error": "Forbidden host."})
            return False
        if not self.authed():
            self.send(403, {"error": self.forbidden_text})
            return False
        if mutating and self.headers.get("X-Wizard") != "1":
            self.send(403, {"error": "Missing X-Wizard header."})
            return False
        return True

    # Set by the factory: {url path: (file in ui_dir, content type)}.
    static = {}
    ui_dir = ""
    invalid_link_text = "Invalid link — use the one printed in your terminal."
    page_forbidden_text = "This wizard only opens from the link printed in your terminal."

    def login_or_static(self, url):
        """Answers the one-time link (/?token=… → session cookie, redirect
        to /) and the page's static files. True when it answered."""
        if url.path == "/" and url.query:
            if not self.host_ok():
                self.send(403, {"error": "Forbidden host."})
            elif self.token_ok(urllib.parse.parse_qs(url.query).get("token", [""])[0]):
                self.send(303, b"", "text/plain", {"Location": "/", "Set-Cookie": self.login_cookie()})
            else:
                self.send(403, self.invalid_link_text, "text/plain")
            return True
        if url.path not in self.static:
            return False
        if not self.host_ok():
            self.send(403, {"error": "Forbidden host."})
        elif not self.authed():
            if url.path == "/":
                self.send(403, self.page_forbidden_text, "text/plain; charset=utf-8")
            else:
                self.send(403, b"", "text/plain")
        else:
            name, ctype = self.static[url.path]
            with open(os.path.join(self.ui_dir, name), "rb") as f:
                self.send(200, f.read(), ctype)
        return True

    def token_ok(self, token):
        return hmac.compare_digest(token, self.token)

    def login_cookie(self):
        return "%s=%s; HttpOnly; SameSite=Strict; Path=/" % (self.cookie_name, self.token)

    def read_json(self):
        """The request's JSON body, or None if missing-length/oversized/invalid."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n < 0 or n > MAX_JSON:
            return None
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Operation records: <platform>/logs/operations/<id>.json — one small JSON
# file per setup/refresh/version run or dashboard action, so the dashboard
# can show what ran (and whether it is still running) after any restart.
# Never holds secrets.
# ---------------------------------------------------------------------------

OP_ID_RE = re.compile(r"^[0-9]{8}-[0-9]{6}-[a-z0-9_-]{1,48}$")


def operations_dir(platform_dir, create=False):
    d = os.path.join(platform_dir, "logs", "operations")
    if create:
        os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def new_op_id(kind, stamp=None):
    return "%s-%s" % (stamp or time.strftime("%Y%m%d-%H%M%S"), kind)


def read_record(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def write_record(path, data):
    """Atomically writes a record (0600)."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".rec-", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def update_record(path, **fields):
    """Merges fields into a record (creating it if needed)."""
    data = read_record(path) or {}
    data.update(fields)
    write_record(path, data)
    return data


def open_private(path, mode="ab", encoding=None):
    """Opens a log for appending, creating it 0600 — run output can hold
    wizard links (with their token) and other things only the owner may read."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    return os.fdopen(fd, mode, encoding=encoding)


def platform_version(code_dir):
    """The platform's version (the VERSION file at the repo root), or None."""
    try:
        with open(os.path.join(code_dir, "VERSION")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def seconds_since(iso):
    """Seconds since a now_iso() timestamp (infinity when missing or unreadable)."""
    try:
        return time.time() - time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return float("inf")
