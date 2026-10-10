"""Environment discovery and status for the dashboard.

An environment is either
  * an instance folder (bin/local/new-instance): ~/Sites/<name>/ with a
    .platform-instance marker naming the shared platform repo, its own .env,
    magento/, nginx/, certs/, logs/, db/ — see bin/lib/paths.sh; or
  * a legacy checkout: <project>/platform (bin/platform + compose/ +
    env/.env.local) next to <project>/magento.

There is no registry — the platform's own facts are the source of truth:
  * Docker: every stack's containers carry com.docker.compose.project
    (= ${APP_NAME}-platform) and platform.instance_dir (the instance folder;
    stacks started before that label existed only have compose's
    working_dir = <checkout>/compose), so running *and stopped* stacks are
    found wherever they live;
  * the filesystem: stacks that are `down` (no containers) are found by
    scanning a few folders — by default the one holding the platform repo.

The environment id is its APP_NAME, which setup keeps unique per machine.
Nothing here changes anything; all commands are read-only.
"""

import json
import os
import re
import socket
import ssl
import subprocess
import threading
import time

import webcore

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
SERVICES = ("nginx", "php", "varnish", "mariadb", "opensearch", "rabbitmq", "valkey", "mailpit")
EDGE_PROJECT = "platform-edge"
VERSION_KEYS = ("PHP_VERSION", "MARIADB_VERSION", "OPENSEARCH_VERSION", "VALKEY_VERSION", "RABBITMQ_VERSION",
                "VARNISH_VERSION", "NGINX_VERSION", "COMPOSER_VERSION", "DEBIAN_RELEASE")
SKIP_DIRS = {".git", "node_modules", "vendor", "var", "generated", "pub", "db", "logs", ".cache"}


def run(argv, timeout=10, cwd=None, env=None):
    """(returncode, stdout) — never raises; returncode None if it couldn't run."""
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""


def parse_env_file(path):
    """KEY=value lines → dict (later lines win). Values are kept raw (quotes included)."""
    out = {}
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                if k.startswith("export "):
                    k = k[7:].strip()
                if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):
                    out[k] = v.strip()
    except OSError:
        pass
    return out


def is_checkout(d):
    """A legacy <project>/platform checkout that has been set up."""
    return (os.path.isfile(os.path.join(d, "bin", "platform"))
            and os.path.isfile(os.path.join(d, "compose", "docker-compose.yml"))
            and os.path.isfile(os.path.join(d, "env", ".env.local")))


def is_instance(d):
    """An instance folder made by bin/local/new-instance."""
    return os.path.isfile(os.path.join(d, ".platform-instance")) and os.path.isfile(os.path.join(d, ".env"))


def is_environment(d):
    return is_instance(d) or is_checkout(d)


def scan_roots(roots, depth=2):
    """Checkout folders up to `depth` levels below each root."""
    found = []

    def walk(d, level):
        if is_environment(d):
            found.append(os.path.realpath(d))
            return
        if level >= depth:
            return
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            if e.name in SKIP_DIRS or e.name.startswith("."):
                continue
            try:
                if e.is_dir(follow_symlinks=False):
                    walk(e.path, level + 1)
            except OSError:
                continue

    for root in roots:
        root = os.path.realpath(root)
        if root.startswith("/mnt/") or not os.path.isdir(root):
            continue  # Windows drives (drvfs): slow to walk, and flock isn't reliable there
        walk(root, 0)
    return found


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

class Docker:
    """`docker inspect` of every container, cached for a couple of seconds."""

    def __init__(self, ttl=2.0):
        self.ttl = ttl
        self.lock = threading.Lock()
        self.at = 0.0
        self.data = None
        self.error = None

    def containers(self):
        with self.lock:
            if self.data is not None and time.time() - self.at < self.ttl:
                return self.data, self.error
            code, out = run(["docker", "ps", "-aq", "--no-trunc"], timeout=10)
            if code != 0:
                self.data, self.error = [], ("Docker isn't reachable — is Docker Desktop running?"
                                             if code is not None else "The docker command isn't installed.")
            else:
                ids = out.split()
                info = []
                if ids:
                    code, out = run(["docker", "inspect"] + ids, timeout=20)
                    try:
                        info = json.loads(out) if code == 0 else []
                    except ValueError:
                        info = []
                self.data, self.error = info, None
            self.at = time.time()
            return self.data, self.error

    def invalidate(self):
        with self.lock:
            self.at = 0.0


def labels(c):
    return (c.get("Config") or {}).get("Labels") or {}


def by_project(containers):
    out = {}
    for c in containers:
        p = labels(c).get("com.docker.compose.project")
        if p:
            out.setdefault(p, []).append(c)
    return out


def container_view(c):
    lb = labels(c)
    st = c.get("State") or {}
    health = (st.get("Health") or {})
    ports = []
    for cport, binds in ((c.get("NetworkSettings") or {}).get("Ports") or {}).items():
        for b in binds or []:
            hp = b.get("HostPort")
            if hp and "%s→%s" % (hp, cport) not in ports:
                ports.append("%s→%s" % (hp, cport))
    detail = None
    log = health.get("Log") or []
    if log and lb.get("com.docker.compose.service") == "opensearch":
        m = re.search(r'"status"\s*:\s*"(green|yellow|red)"', log[-1].get("Output") or "")
        if m:
            detail = "cluster " + m.group(1)
    return {
        "name": (c.get("Name") or "").lstrip("/"),
        "service": lb.get("com.docker.compose.service", ""),
        "state": st.get("Status", "unknown"),
        "health": health.get("Status"),
        "healthDetail": detail,
        "startedAt": st.get("StartedAt"),
        "exitCode": st.get("ExitCode"),
        "ports": ports,
        "image": (c.get("Config") or {}).get("Image", ""),
    }


def service_status(view):
    """{level, text} for one service. level: ok | warn | bad | off | unknown."""
    if view is None:
        return {"level": "off", "text": "Not created"}
    state, health = view["state"], view["health"]
    if state == "running":
        if health == "healthy":
            text = "Healthy" + (" · " + view["healthDetail"] if view.get("healthDetail") else "")
            return {"level": "warn" if "yellow" in text or "red" in text else "ok", "text": text}
        if health == "starting":
            return {"level": "warn", "text": "Starting"}
        if health == "unhealthy":
            return {"level": "bad", "text": "Unhealthy"}
        return {"level": "ok", "text": "Running (no healthcheck)"}
    if state == "restarting":
        return {"level": "bad", "text": "Restarting"}
    if state in ("exited", "dead"):
        code = view.get("exitCode")
        if code not in (0, None, 137, 143):
            return {"level": "bad", "text": "Exited (%s)" % code}
        return {"level": "off", "text": "Stopped"}
    if state == "created":
        return {"level": "off", "text": "Created"}
    if state == "paused":
        return {"level": "warn", "text": "Paused"}
    return {"level": "unknown", "text": "Unknown"}


def stack_status(conts):
    total = len(conts)
    running = sum(1 for c in conts if (c.get("State") or {}).get("Status") == "running")
    if total == 0:
        state = "absent"
    elif running == total:
        state = "running"
    elif running == 0:
        state = "stopped"
    else:
        state = "partial"
    return {"state": state, "running": running, "total": total}


# ---------------------------------------------------------------------------
# Small cached probes (git, Magento files, HTTP health)
# ---------------------------------------------------------------------------

class TTLCache:
    MAX_KEYS = 256
    MAX_AGE = 3600  # longer than any ttl in use

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {}

    def get(self, key, ttl, fn):
        with self.lock:
            hit = self.data.get(key)
            if hit and time.time() - hit[0] < ttl:
                return hit[1]
        value = fn()
        with self.lock:
            now = time.time()
            self.data[key] = (now, value)
            if len(self.data) > self.MAX_KEYS:  # paths of removed instances, old repos …
                for k in [k for k, (at, _) in self.data.items() if now - at > self.MAX_AGE]:
                    del self.data[k]
        return value

    def drop(self, prefix):
        with self.lock:
            for k in [k for k in self.data if k[0] == prefix]:
                del self.data[k]


CACHE = TTLCache()


def git_info(path, with_status=False):
    """Branch / short commit / working-tree state — read-only, no fetch."""
    def probe():
        if not os.path.exists(os.path.join(path, ".git")):
            return None
        g = ["git", "--no-optional-locks", "-C", path]
        code, branch = run(g + ["rev-parse", "--abbrev-ref", "HEAD"], timeout=5, env=webcore.git_env())
        if code != 0:
            return {"branch": None, "commit": None, "tree": None, "error": "git couldn't read this repository"}
        _, commit = run(g + ["rev-parse", "--short", "HEAD"], timeout=5, env=webcore.git_env())
        _, subject = run(g + ["log", "-1", "--format=%s"], timeout=5, env=webcore.git_env())
        info = {"branch": branch.strip() or None, "commit": commit.strip() or None,
                "subject": subject.strip()[:120] or None, "tree": None}
        if info["branch"] == "HEAD":
            info["branch"] = "(detached)"
        return info

    info = CACHE.get(("git", path), 30, probe)
    if info and with_status and info.get("commit"):
        def status():
            code, out = run(["git", "--no-optional-locks", "-C", path, "status", "--porcelain"], timeout=15, env=webcore.git_env())
            if code != 0:
                return None
            n = len([l for l in out.splitlines() if l.strip()])
            return "Clean" if n == 0 else "Modified (%d file%s)" % (n, "" if n == 1 else "s")
        info = dict(info, tree=CACHE.get(("gitstatus", path), 60, status))
    return info


def magento_version_from_lock(magento_dir):
    path = os.path.join(magento_dir, "composer.lock")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None

    def parse():
        try:
            with open(path, encoding="utf-8") as f:
                pkgs = json.load(f).get("packages") or []
        except (OSError, ValueError, AttributeError):
            return None
        base = None
        for p in pkgs:
            name = p.get("name", "")
            if re.match(r"^magento/product-(community|enterprise)-edition$", name):
                return p.get("version")
            if name == "magento/magento2-base":
                base = p.get("version")
        return base

    return CACHE.get(("lock", path, mtime), 3600, parse)


ENV_PHP_MODE_RE = re.compile(r"""['"]MAGE_MODE['"]\s*=>\s*['"](\w+)['"]""")


def actual_mode(magento_dir):
    path = os.path.join(magento_dir, "app", "etc", "env.php")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(262144)
    except FileNotFoundError:
        return None, "Magento isn't installed yet (no app/etc/env.php)"
    except OSError:
        return None, "app/etc/env.php isn't readable"
    m = ENV_PHP_MODE_RE.search(text)
    return (m.group(1), None) if m else ("default", "MAGE_MODE not set in env.php")


def http_health(domain, port=443, timeout=4.0):
    """GET /health_check.php through the edge proxy (TLS passthrough on SNI),
    the same request a browser makes. Certificate checks are off (mkcert's
    CA isn't in Python's store) — this only reads a status code."""
    def probe():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=timeout) as raw:
                with ctx.wrap_socket(raw, server_hostname=domain) as s:
                    s.settimeout(timeout)
                    s.sendall(("GET /health_check.php HTTP/1.1\r\nHost: %s\r\nUser-Agent: platform-dashboard\r\n"
                               "Connection: close\r\n\r\n" % domain).encode())
                    head = s.recv(256).decode("latin-1", "replace")
        except (OSError, ssl.SSLError) as e:
            return {"level": "bad", "text": "Not reachable (%s)" % (e.strerror or e.__class__.__name__)}
        m = re.match(r"HTTP/\d\.\d (\d{3})", head)
        if not m:
            return {"level": "unknown", "text": "Unknown response"}
        code = int(m.group(1))
        if code == 200:
            return {"level": "ok", "text": "Healthy (health_check.php 200)"}
        if code == 503:
            return {"level": "warn", "text": "Maintenance or unavailable (503)"}
        return {"level": "bad", "text": "health_check.php returned %d" % code}

    return CACHE.get(("http", domain), 15, probe)


def disk_usage(paths, volumes_prefix):
    """du of each path plus this stack's Docker volumes — slow, call on request."""
    out = {}
    for label, p in paths.items():
        code, text = run(["du", "-sxb", p], timeout=180)
        out[label] = int(text.split()[0]) if code == 0 and text.split() else None
    code, text = run(["docker", "system", "df", "-v", "--format", "{{json .Volumes}}"], timeout=120)
    vols = {}
    if code == 0:
        try:
            for v in json.loads(text) or []:
                name = v.get("Name", "")
                if name.startswith(volumes_prefix):
                    vols[name] = v.get("Size")
        except ValueError:
            pass
    out["volumes"] = vols
    return out


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------

class Environment:
    """One instance or legacy checkout.

    platform_dir is where its state lives (env file, logs/, db/, lock) — the
    instance folder, or the legacy checkout itself; code_dir is the platform
    repo whose bin/ scripts run it (for a legacy checkout, the same folder)."""

    def __init__(self, path):
        self.platform_dir = path
        self.is_instance = is_instance(path)
        self.readonly = None
        if self.is_instance:
            self.env_file = os.path.join(path, ".env")
            marker = parse_env_file(os.path.join(path, ".platform-instance"))
            repo = os.path.realpath(marker.get("PLATFORM_REPO", "") or "/nonexistent")
            self.code_dir = repo
            if not os.path.isfile(os.path.join(repo, "bin", "lib", "paths.sh")):
                self.readonly = "Its platform repo (%s, from .platform-instance) isn't there any more." % repo
            self.stack = {}
            self.magento_dir = os.path.join(path, "magento")
            self.magento_dir_source = "<instance>/magento"
        else:
            self.env_file = os.path.join(path, "env", ".env.local")
            self.code_dir = path
            stack_file = os.path.join(path, "env", "stack.env")
            self.stack = parse_env_file(stack_file) if os.path.isfile(stack_file) else {}
            if os.path.isfile(os.path.join(path, "env", ".env.dev")):
                self.readonly = "This checkout is set up as a dev server (env/.env.dev) — manage it there."
            self.magento_dir = os.path.realpath(os.path.join(path, "..", "magento"))
            self.magento_dir_source = "<checkout>/../magento"
        self.env = parse_env_file(self.env_file)
        raw_name = self.env.get("APP_NAME", "")
        self.app_name = raw_name
        self.id = raw_name if ID_RE.match(raw_name) else None
        if self.is_instance and self.id and self.id != os.path.basename(path):
            self.readonly = self.readonly or ("Its APP_NAME (%s) doesn't match the folder name — fix .env or recreate it." % self.id)

    # -- facts the API guards check (one place, one wording) ------------------

    @property
    def is_repo(self):
        """magento/ is a git checkout (.git dir, or a gitfile for worktrees)."""
        return os.path.exists(os.path.join(self.magento_dir, ".git"))

    @property
    def two_owner(self):
        return (self.env.get("FILE_OWNERSHIP") or "single") == "two-owner"

    @property
    def production(self):
        return (self.env.get("MAGENTO_MODE") or "") == "production"

    def require_writable(self):
        """OpError 409 unless the dashboard may change this environment."""
        if self.readonly:
            raise webcore.OpError(409, "This environment is read-only here.", self.readonly)

    def require_repo(self, detail=None):
        if not self.is_repo:
            raise webcore.OpError(409, "magento/ isn't a git checkout.", detail)

    @property
    def env_name(self):
        return ".env" if self.is_instance else "env/.env.local"

    def cmd_hint(self, *args):
        """What to type in a terminal for this environment (see bin/lib/paths.sh cmd_hint)."""
        if self.is_instance:
            return "mdk -i %s %s" % (os.path.basename(self.platform_dir), " ".join(args))
        sub, rest = args[0], list(args[1:])
        if sub in ("setup", "setup-wizard", "refresh-wizard", "version-wizard", "pipeline", "pipeline-wizard", "rebuild", "rebuild-wizard"):
            path = "bin/local/" + sub
        elif sub in ("mode", "ownership", "permissions"):
            path, rest = "bin/%s/%s" % (sub, rest[0]), rest[1:]
        else:
            path = "bin/" + sub
        return "cd %s && %s" % (self.platform_dir, " ".join([path] + rest))

    def script_env(self, base):
        """Environment for running this one's bin/ scripts (base is already scrubbed)."""
        env = dict(base)
        if self.is_instance:
            env["PLATFORM_INSTANCE_DIR"] = self.platform_dir
        return env

    @property
    def project(self):
        return "%s-platform" % self.app_name

    def value(self, key):
        """A stack version as bin/lib/target.sh resolves it."""
        if self.stack and self.env.get("STACK_LOCAL_OVERRIDE") != "1" and key in self.stack:
            return self.stack[key]
        return self.env.get(key)

    def secret_values(self):
        """Values that must never appear in a log view."""
        keys = [k for k in self.env if re.search(r"PASSWORD|PASS$|SECRET|TOKEN|_KEY$", k)]
        return [self.env[k].strip("\"'") for k in keys]

    def has(self, rel):
        """Whether the platform code running this one has <rel> (e.g. bin/mode/show)."""
        return os.path.exists(os.path.join(self.code_dir, rel))


class Discovery:
    def __init__(self, roots, docker):
        self.roots = roots
        self.docker = docker
        self.lock = threading.Lock()
        self.at = 0.0
        self.result = None

    def environments(self, ttl=5.0):
        """(envs dict id → Environment, warnings, containers by project, docker error)."""
        with self.lock:
            containers, derr = self.docker.containers()
            if self.result is not None and time.time() - self.at < ttl:
                envs, warnings = self.result
                return envs, warnings, by_project(containers), derr
            projects = by_project(containers)
            dirs, warnings, orphans = [], [], []
            for proj, conts in projects.items():
                if proj == EDGE_PROJECT or not proj.endswith("-platform"):
                    continue
                inst = next((labels(c).get("platform.instance_dir") for c in conts
                             if labels(c).get("platform.instance_dir")), "")
                wd = labels(conts[0]).get("com.docker.compose.project.working_dir", "")
                pd = os.path.realpath(inst) if inst else (os.path.realpath(os.path.dirname(wd)) if wd else "")
                if pd and is_environment(pd):
                    dirs.append(pd)
                else:
                    orphans.append((proj, pd))
            for d in scan_roots(self.roots):
                if d not in dirs:
                    dirs.append(d)
            envs = {}
            for d in dirs:
                e = Environment(d)
                if not e.id:
                    warnings.append("Skipped %s: APP_NAME in %s is missing or not a plain name (%r)."
                                    % (d, e.env_name, e.app_name[:40]))
                    continue
                if e.id in envs:
                    warnings.append("Skipped %s: APP_NAME %r is also used by %s." % (d, e.id, envs[e.id].platform_dir))
                    continue
                php = next((c for c in projects.get(e.project, [])
                            if labels(c).get("com.docker.compose.service") == "php"), None)
                for m in (php or {}).get("Mounts") or []:
                    if m.get("Destination") == "/var/www/html" and m.get("Source") and os.path.isdir(m["Source"]):
                        e.magento_dir = os.path.realpath(m["Source"])
                        e.magento_dir_source = "php container mount"
                envs[e.id] = e
            for proj, pd in orphans:
                if proj[:-len("-platform")] not in envs:
                    warnings.append("Containers of compose project %s belong to %s, which isn't an instance or platform checkout any more."
                                    % (proj, pd or "an unknown folder"))
            self.result = (envs, warnings)
            self.at = time.time()
            return envs, warnings, projects, derr

    def invalidate(self):
        with self.lock:
            self.at = 0.0
        self.docker.invalidate()


def summarize(e, conts, detail=False, docker_ok=True):
    """Everything the dashboard shows for one environment. Unknown → None."""
    views = {}
    for c in conts:
        v = container_view(c)
        views.setdefault(v["service"], v)
    domain = e.env.get("APP_DOMAIN") or None
    nginx_up = (views.get("nginx") or {}).get("state") == "running"
    if not docker_ok:
        stack = {"state": "unknown", "running": 0, "total": 0}
        services = {s: {"level": "unknown", "text": "Unknown (Docker unavailable)"} for s in SERVICES}
    else:
        stack = stack_status(conts)
        services = {s: service_status(views.get(s)) for s in SERVICES}
    if not docker_ok:
        app = {"level": "unknown", "text": "Unknown (Docker unavailable)"}
    elif domain and nginx_up:
        app = http_health(domain)
    elif stack["state"] in ("stopped", "absent"):
        app = {"level": "off", "text": "Stack is not running"}
    else:
        app = {"level": "bad" if domain else "unknown", "text": "Nginx is not running" if domain else "Unknown"}
    mversion = e.env.get("MAGENTO_VERSION") or None
    msource = e.env_name if mversion else None
    if not mversion:
        mversion = magento_version_from_lock(e.magento_dir)
        msource = "magento/composer.lock" if mversion else None
    mode, mode_note = actual_mode(e.magento_dir)
    git = git_info(e.magento_dir, with_status=detail)
    out = {
        "id": e.id,
        "name": e.app_name,
        "domain": domain,
        "readonly": e.readonly,
        "platformDir": e.platform_dir,
        "layout": "instance" if e.is_instance else "checkout",
        "codeDir": e.code_dir,
        "magentoDir": e.magento_dir,
        "magentoVersion": {"value": mversion, "source": msource},
        "phpVersion": e.value("PHP_VERSION"),
        "mode": {"configured": e.env.get("MAGENTO_MODE") or None, "actual": mode, "note": mode_note},
        "ownership": e.env.get("FILE_OWNERSHIP") or "single",
        "git": git,
        "docker": stack,
        "services": services,
        "magento": app,
        "project": e.project,
    }
    if detail:
        out.update({
            "containers": sorted((container_view(c) for c in conts), key=lambda v: (SERVICES.index(v["service"])
                                 if v["service"] in SERVICES else 99, v["name"])),
            "platformGit": git_info(e.code_dir, with_status=True),
            "versions": {k: e.value(k) for k in VERSION_KEYS},
            "versionsFrom": "env/stack.env" if e.stack and e.env.get("STACK_LOCAL_OVERRIDE") != "1" else e.env_name,
            "envFile": e.env_file,
            "magentoDirSource": e.magento_dir_source,
            "adminUri": e.env.get("MAGENTO_ADMIN_URI") or None,
            "mailpitPort": e.env.get("MAILPIT_HTTP_PORT") or None,
            "rabbitmqPort": e.env.get("RABBITMQ_MANAGEMENT_PORT") or None,
            "storage": e.env.get("REMOTE_STORAGE_DRIVER") if e.env.get("REMOTE_STORAGE_DRIVER") not in (None, "", "CHANGE_ME") else None,
        })
    return out


def edge_status(projects):
    conts = projects.get(EDGE_PROJECT, [])
    if not conts:
        return {"level": "off", "text": "Not running — starts with the first `bin/platform up`"}
    v = container_view(conts[0])
    st = service_status(v)
    wd = labels(conts[0]).get("com.docker.compose.project.working_dir")
    return dict(st, name=v["name"], workingDir=wd)


# ---------------------------------------------------------------------------
# Child environment for an environment's bin/ scripts
# ---------------------------------------------------------------------------

SCRUB_PREFIXES = ("COMPOSE_", "WIZARD_", "SETUP_UI", "DASHBOARD_", "PRESET_", "DOCKER_DEFAULT_PLATFORM")
SCRUB_KEYS = {"UI_PLATFORM_DIR", "STACK_LOCAL_OVERRIDE", "UI_FLOW", "UI_LOG_FILE", "ENV_FILE", "PLATFORM_DIR",
              "MAGENTO_DIR", "PROJECT", "EXEC_USER", "PHP_UID", "PHP_GID",
              # bin/lib/paths.sh — recomputed by every script; set per run below
              "PLATFORM_INSTANCE_DIR", "PLATFORM_MODE", "INSTANCE_DIR", "ENV_NAME", "NGINX_CONF_DIR", "CERTS_DIR",
              "NGINX_CONF", "LOGS_DIR", "DB_DIR", "LOCK_FILE", "PLATFORM_INSTANCES_ROOT"}


def child_env(all_envs):
    """The dashboard's environment minus anything that could steer a
    checkout's scripts to the wrong stack: compose interpolation prefers the
    shell environment over --env-file, COMPOSE_PROJECT_NAME overrides the
    project name, and an inherited WIZARD_LOCK_HELD would skip the lock."""
    keys = set(SCRUB_KEYS)
    for e in all_envs:
        keys.update(e.env.keys())
        keys.update(e.stack.keys())
    env = {k: v for k, v in os.environ.items() if k not in keys and not k.startswith(SCRUB_PREFIXES)}
    env["SUDO_ASKPASS"] = "/bin/false"  # nothing here may prompt for a password
    env.pop("DISPLAY", None)
    return env
