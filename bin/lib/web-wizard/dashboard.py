#!/usr/bin/env python3
"""Persistent management dashboard for every platform checkout on this
machine. Started by bin/local/dashboard — never directly.

  dashboard.py --checkout DIR [--port N] [--port-fallback] [--scan DIR ...] --state FILE

Read-only status comes from Docker and each checkout's files (envs.py);
actions and workflows run the checkout's own scripts (ops.py, oprun.py) —
the dashboard holds no business logic of its own. It doesn't exit after an
operation; Ctrl+C (or SIGTERM) stops it, and running operations carry on.

Security: the same model as the wizard (webcore.py) — 127.0.0.1 only, a
token from the environment exchanged for an HttpOnly SameSite=Strict
cookie (dash_<port>), Host allow-list, X-Wizard header on every POST, strict
CSP. It is NOT built for remote access: there are no user accounts, so never
publish its port or put it behind a proxy without real authentication in
front (see README "Dashboard → Remote use").
"""

import argparse
import concurrent.futures
import http.server
import json
import logging
import os
import re
import secrets
import signal
import socketserver
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import awsaccounts  # noqa: E402
import envs  # noqa: E402
import ops  # noqa: E402
import gitrepo  # noqa: E402
import modules  # noqa: E402
import pipeline  # noqa: E402
import releases  # noqa: E402
import webcore  # noqa: E402

UI_DIR = os.path.join(HERE, "ui")
CODE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))  # this platform repo
NEW_INSTANCE = os.path.join(CODE_DIR, "bin", "local", "new-instance")
STATIC = {
    "/": ("dashboard.html", "text/html; charset=utf-8"),
    "/kit.js": ("kit.js", "text/javascript; charset=utf-8"),
    "/dash-core.js": ("dash-core.js", "text/javascript; charset=utf-8"),
    "/dash-env.js": ("dash-env.js", "text/javascript; charset=utf-8"),
    "/dash-git.js": ("dash-git.js", "text/javascript; charset=utf-8"),
    "/dash-modules.js": ("dash-modules.js", "text/javascript; charset=utf-8"),
    "/dash-aws.js": ("dash-aws.js", "text/javascript; charset=utf-8"),
    "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
    "/theme.js": ("theme.js", "text/javascript; charset=utf-8"),
    "/wizard.css": ("wizard.css", "text/css; charset=utf-8"),
}
def code_stamp():
    """When the dashboard's own code last changed: the newest mtime of its
    Python files. Taken once at start (STARTED_CODE); a newer value later means
    this process still runs the old code — bin/local/dashboard restarts it, and
    the page asks for a restart."""
    try:
        return max(int(os.path.getmtime(os.path.join(HERE, n))) for n in os.listdir(HERE) if n.endswith(".py"))
    except (OSError, ValueError):
        return 0


STARTED_CODE = code_stamp()
ENV_ROUTE = re.compile(r"^/api/environments/([^/]+)(?:/(.*))?$")
RELEASES_CATALOG = os.path.join(CODE_DIR, "m2")
LOG = logging.getLogger("dashboard")


class Dashboard:
    def __init__(self, roots, token):
        self.token = token
        self.instance = secrets.token_hex(8)
        self.docker = envs.Docker()
        self.discovery = envs.Discovery(roots, self.docker)
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=8)
        self.disk = {}       # env id -> {"status", "at", "data"}
        self.disk_lock = threading.Lock()
        self.releases_lock = threading.Lock()  # one release check at a time
        self.aws = awsaccounts.Accounts(CODE_DIR)
        self.stop = None  # set by main(): stops the server (the Turn off button)

    def all_envs(self):
        return list(self.environments()[0].values())

    def environments(self):
        return self.discovery.environments()

    def env(self, env_id):
        if not envs.ID_RE.match(env_id or ""):
            raise ops.OpError(404, "Unknown environment.")
        found, _, projects, _ = self.environments()
        e = found.get(env_id)
        if not e:
            raise ops.OpError(404, "Unknown environment.")
        return e, projects.get(e.project, []), list(found.values())

    def overview(self):
        found, warnings, projects, derr = self.environments()

        def one(e):
            s = envs.summarize(e, projects.get(e.project, []), docker_ok=derr is None)
            held, text = ops.probe_lock(e)
            recent = ops.list_operations(e, limit=5)
            running = next((r for r in recent if r.get("status") == "running"), None)
            s["lock"] = {"held": held, "text": text}
            s["operation"] = {"running": running, "last": recent[0] if recent else None}
            rel = self.env_releases(e, s["magentoVersion"]["value"])
            s["releaseUpdate"] = rel["latestCompatible"] if rel else None
            return s

        self.releases_status()  # starts the daily background check when it's due
        items = list(self.pool.map(one, sorted(found.values(), key=lambda e: e.id)))
        return {"environments": items, "edge": envs.edge_status(projects), "warnings": warnings,
                "docker": {"available": derr is None, "error": derr}, "roots": self.discovery.roots,
                "instancesRoot": self.instances_root(), "generatedAt": time.time(),
                "platformVersion": webcore.platform_version(CODE_DIR),
                "codeOutdated": code_stamp() > STARTED_CODE}

    # -- Magento releases (releases.py) ------------------------------------------
    # The page only ever reads the saved copy; the download runs in the
    # background at most once a day (releases.refresh --if-stale, which also
    # waits an hour after a failed try), or when Check now asks for it.

    def releases_status(self, kick=True):
        data = releases.load()
        stale = releases.age(data.get("fetchedAt")) > releases.STALE_AFTER
        if kick and stale and not self.releases_lock.locked():
            threading.Thread(target=self.refresh_releases, kwargs={"if_stale": True, "quiet": True}, daemon=True).start()
        return {"fetchedAt": data.get("fetchedAt"), "lastAttempt": data.get("lastAttempt"), "lastError": data.get("lastError"),
                "stale": stale, "refreshing": self.releases_lock.locked()}

    def refresh_releases(self, if_stale=False, quiet=False):
        if not self.releases_lock.acquire(blocking=False):
            if quiet:
                return None
            raise ops.OpError(409, "A release check is already running.")
        try:
            releases.refresh(RELEASES_CATALOG, if_stale=if_stale, timeout=8)
        except Exception:  # a background check never takes the dashboard down
            LOG.exception("release check")
            if not quiet:
                raise
        finally:
            self.releases_lock.release()
        return self.releases_status(kick=False)

    def env_releases(self, e, installed):
        """releases.classify for one environment: what it could move to with
        its own platform repo's m2/ catalog. None when that can't be read."""
        if not installed:
            return None  # nothing to compare with until Magento is there
        edition, php = envs.magento_edition(e.magento_dir), envs.project_php(e.magento_dir)
        saved = os.path.join(releases.cache_root(), "releases.json")
        try:
            mtime = os.path.getmtime(saved)
        except OSError:
            mtime = None
        key = ("releases", e.code_dir, mtime, installed, edition, php)
        try:
            return envs.CACHE.get(key, 300, lambda: releases.classify(installed, edition, php, os.path.join(e.code_dir, "m2")))
        except Exception:
            LOG.exception("releases for %s", e.id)
            return None

    @staticmethod
    def instances_root():
        return os.environ.get("PLATFORM_INSTANCES_ROOT") or os.path.dirname(CODE_DIR)

    def new_instance(self, name):
        """bin/local/new-instance NAME --no-setup, then the setup wizard for it."""
        if not isinstance(name, str) or not re.match(r"^[a-z0-9][a-z0-9_-]{0,39}$", name):
            raise ops.OpError(400, "Name: lowercase letters, digits, _ and - (max 40), starting with a letter or digit.")
        found, _, _, _ = self.environments()
        if name in found:
            raise ops.OpError(409, "There is already an environment called %s." % name, found[name].platform_dir)
        try:
            env = dict(ops.child_env(found.values()), PLATFORM_INSTANCES_ROOT=self.instances_root())
            p = subprocess.run([NEW_INSTANCE, name, "--no-setup"], cwd=CODE_DIR, env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ops.OpError(500, "Couldn't create the instance.", str(e))
        out = webcore.redact_patterns("\n".join(webcore.clean_line(l) for l in p.stdout.decode("utf-8", "replace").splitlines()))
        if p.returncode != 0:
            raise ops.OpError(400, "Couldn't create the instance.", out.strip())
        path = os.path.join(self.instances_root(), name)
        if not any(os.path.realpath(path).startswith(os.path.realpath(r) + os.sep) for r in self.discovery.roots):
            self.discovery.roots.append(os.path.realpath(self.instances_root()))
        self.discovery.invalidate()
        found, _, _, _ = self.environments()
        e = found.get(name)
        if not e:
            raise ops.OpError(500, "The instance was created but the dashboard can't see it.", path)
        op_id, url = ops.start_workflow(e, "setup", list(found.values()))
        return {"ok": True, "id": name, "path": e.platform_dir, "operation": op_id, "url": url}

    def detail(self, env_id):
        e, conts, _ = self.env(env_id)
        s = envs.summarize(e, conts, detail=True, docker_ok=self.docker.containers()[1] is None)
        held, text = ops.probe_lock(e)
        s["lock"] = {"held": held, "text": text}
        s["operations"] = ops.list_operations(e)
        # Right after a pipeline run, show the repo as it is now, not as cached.
        if any((op.get("kind") == "pipeline" or str(op.get("kind", "")).startswith("git-"))
               and (op.get("status") in ("starting", "running")
               or webcore.seconds_since(op.get("endedAt")) < 90) for op in s["operations"]):
            pipeline.drop_cache()
            s["git"] = envs.git_info(e.magento_dir, with_status=True)
        s["pipeline"] = pipeline.info(e)
        rel = self.env_releases(e, s["magentoVersion"]["value"])
        s["releases"] = dict(rel, check=self.releases_status()) if rel else None
        s["olderLogs"] = ops.older_logs(e)
        s["logSources"] = ops.log_sources(e)
        s["actions"] = {k: {"label": v["label"], "confirm": v.get("confirm"), "mutating": v["mutating"],
                            "available": e.has(v["argv"][0].format(mode="developer"))}
                        for k, v in ops.ACTIONS.items()}
        s["workflows"] = {k: {"label": v["label"],
                              "available": e.has("bin/lib/web-wizard/launch.sh") and (not v["requires_env"] or os.path.exists(e.env_file))
                              and (not v.get("requires_repo") or e.is_repo)}
                          for k, v in ops.WORKFLOWS.items()}
        return s

    def disk_usage(self, env_id):
        e, _, _ = self.env(env_id)
        with self.disk_lock:
            cur = self.disk.get(env_id)
            if cur and (cur["status"] == "computing" or (cur["status"] == "done" and time.time() - cur["at"] < 600)):
                return cur
            cur = {"status": "computing", "at": time.time(), "data": None}
            self.disk[env_id] = cur

        def work():
            try:
                data = envs.disk_usage({"magento": e.magento_dir, "backups": os.path.join(e.platform_dir, "db")},
                                       "%s-" % e.id)
                done = {"status": "done", "at": time.time(), "data": data}
            except Exception as ex:  # never leave it "computing" forever
                logging.exception("disk usage for %s", env_id)
                done = {"status": "error", "at": time.time(), "data": None, "error": str(ex)[:200]}
            with self.disk_lock:
                self.disk[env_id] = done

        threading.Thread(target=work, daemon=True).start()
        return cur


def make_handler(dash, port):
    class Handler(webcore.SecureHandler):
        server_version = "dashboard"
        hosts = webcore.allowed_hosts(port)
        token = dash.token
        cookie_name = "dash_%d" % port
        csp_header = webcore.csp()
        static = STATIC
        ui_dir = UI_DIR
        invalid_link_text = "Invalid link — use the one printed by bin/local/dashboard."
        page_forbidden_text = "The dashboard only opens from the link printed by bin/local/dashboard."
        forbidden_text = "Open the link printed by bin/local/dashboard."

        def log_message(self, fmt, *args):  # DASHBOARD_DEBUG=1 logs every request
            if os.environ.get("DASHBOARD_DEBUG"):
                LOG.info("%s %s", self.address_string(), webcore.redact_patterns(fmt % args))

        def fail(self, err):
            body = {"error": err.message}
            if err.detail:
                body["detail"] = err.detail
            return self.send(err.code, body)

        def safely(self, fn):
            t0 = time.time()
            try:
                return fn()
            except ops.OpError as err:
                return self.fail(err)
            except (BrokenPipeError, ConnectionResetError):
                return None  # the page went away mid-reply — nothing to tell it
            except Exception:  # never a traceback on the page; full detail in the server log
                LOG.error("%s %s\n%s", self.command, self.path, traceback.format_exc())
                return self.send(500, {"error": "Something went wrong in the dashboard.",
                                       "detail": "The details are in logs/dashboard-server.log."})
            finally:
                if time.time() - t0 > 2:
                    LOG.warning("slow request: %s %s took %.1fs", self.command, self.path, time.time() - t0)

        # -- GET -------------------------------------------------------------

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            if url.path == "/api/ping":
                if not self.host_ok():
                    return self.send(403, {"error": "Forbidden host."})
                return self.send(200, {"app": "platform-dashboard", "instance": dash.instance, "code": STARTED_CODE})
            if self.login_or_static(url):
                return
            if not self.guard():
                return
            self.safely(lambda: self.get_api(url))

        def get_api(self, url):
            qs = urllib.parse.parse_qs(url.query)
            if url.path == "/api/environments":
                return self.send(200, dash.overview())
            if url.path == "/api/releases":
                return self.send(200, dash.releases_status())
            if url.path == "/api/aws/accounts":
                return self.send(200, dash.aws.status(dash.all_envs(), recheck=qs.get("check", ["0"])[0] == "1"))
            if url.path.startswith("/api/destroyed/"):
                return self.send(200, ops.destroy_status(dash.destroy_dir, url.path[len("/api/destroyed/"):]))
            m = ENV_ROUTE.match(url.path)
            if not m:
                return self.send(404, {"error": "Not found."})
            env_id, rest = m.group(1), m.group(2) or ""
            if rest == "":
                return self.send(200, dash.detail(env_id))
            if rest == "disk":
                return self.send(200, dash.disk_usage(env_id))
            e, _, all_envs = dash.env(env_id)
            if rest == "logs":
                text = ops.read_log(e, qs.get("source", [""])[0], qs.get("lines", ["300"])[0], all_envs)
                return self.send(200, {"text": text})
            parts = rest.split("/")
            if rest == "operations":
                return self.send(200, {"operations": ops.list_operations(e), "olderLogs": ops.older_logs(e)})
            if parts[0] == "operations" and len(parts) == 2:
                rec = ops.get_operation(e, parts[1])
                url_ = ops.wizard_url(e, parts[1])
                if url_ and (ops.pid_alive(rec.get("pid"), "oprun.py") or ops.pid_alive(rec.get("serverPid"), "server.py")):
                    rec["url"] = url_
                return self.send(200, rec)
            if rest == "git":
                return self.send(200, gitrepo.status(e))
            if rest == "modules":
                return self.send(200, modules.listing(e))
            if rest == "destroy-check":
                return self.send(200, ops.destroy_check(e))
            if rest == "git/diff":
                return self.send(200, gitrepo.diff(e, qs.get("path", [""])[0], qs.get("staged", ["0"])[0] == "1"))
            if parts[0] == "operations" and len(parts) == 3 and parts[2] == "log":
                text = ops.read_log(e, "op:" + parts[1], qs.get("lines", ["400"])[0], all_envs)
                return self.send(200, {"text": text, "operation": ops.get_operation(e, parts[1])})
            return self.send(404, {"error": "Not found."})

        # -- POST ------------------------------------------------------------

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            if not self.guard(mutating=True):
                return
            body = self.read_json()
            if body is None or not isinstance(body, dict):
                return self.send(400, {"error": "Bad request."})
            self.safely(lambda: self.post_api(url, body))

        def post_api(self, url, body):
            if url.path == "/api/shutdown":
                # Reply first, then stop: operations and wizard pages it
                # started run detached and carry on.
                LOG.info("dashboard stopped from the page")
                threading.Timer(0.3, dash.stop).start()
                return self.send(200, {"ok": True})
            if url.path == "/api/releases/refresh":
                res = dash.refresh_releases()
                LOG.info("release check: %s", res.get("lastError") or "ok")
                return self.send(200, res)
            if url.path == "/api/instances":
                res = dash.new_instance(body.get("name"))
                LOG.info("new instance %s → %s", res["id"], res["operation"])
                return self.send(202, res)
            if url.path == "/api/aws/accounts/wizard":
                op_id, link = dash.aws.start(body.get("action"), body.get("name"), dash.all_envs())
                LOG.info("aws connection wizard %s → %s", body.get("action"), op_id)
                return self.send(202, {"ok": True, "operation": op_id, "url": link})
            if url.path == "/api/aws/accounts/remove":
                res = dash.aws.remove(body.get("name"), dash.all_envs())
                LOG.info("aws connection removed: %s", body.get("name"))
                return self.send(200, res)
            m = ENV_ROUTE.match(url.path)
            if not m:
                return self.send(404, {"error": "Not found."})
            env_id, rest = m.group(1), (m.group(2) or "").split("/")
            e, _, all_envs = dash.env(env_id)
            if len(rest) == 2 and rest[0] == "actions":
                op_id = ops.run_action(e, rest[1], body, all_envs)
                LOG.info("action %s on %s → %s", rest[1], e.id, op_id)
                dash.discovery.invalidate()
                return self.send(202, {"ok": True, "operation": op_id})
            if len(rest) == 2 and rest[0] == "workflows":
                op_id, link = ops.start_workflow(e, rest[1], all_envs, body)
                LOG.info("workflow %s on %s → %s", rest[1], e.id, op_id)
                return self.send(202, {"ok": True, "operation": op_id, "url": link})
            if rest == ["destroy"]:
                op_id = ops.destroy(e, body, dash.destroy_dir)
                LOG.info("destroy %s → %s", e.id, op_id)
                dash.discovery.invalidate()
                return self.send(202, {"ok": True, "operation": op_id})
            if len(rest) == 2 and rest[0] == "git":
                if rest[1] in gitrepo.NETWORK:
                    op_id = gitrepo.network(e, rest[1], body, all_envs)
                    LOG.info("git %s on %s → %s", rest[1], e.id, op_id)
                    return self.send(202, {"ok": True, "operation": op_id})
                res = gitrepo.write(e, rest[1], body)
                pipeline.drop_cache()
                LOG.info("git %s on %s", rest[1], e.id)
                return self.send(200, dict(res, ok=True))
            if len(rest) == 3 and rest[0] == "operations" and rest[2] == "cancel":
                ops.cancel(e, rest[1])
                LOG.info("cancel %s on %s", rest[1], e.id)
                return self.send(200, {"ok": True})
            return self.send(404, {"error": "Not found."})

    return Handler


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True  # TIME_WAIT only — Linux still refuses a second listener


def write_state(path, data):
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkout", required=True)
    ap.add_argument("--port", type=int, default=0)
    ap.add_argument("--port-fallback", action="store_true", help="use any free port if --port is taken")
    ap.add_argument("--scan", action="append", default=[])
    ap.add_argument("--state", required=True)
    args = ap.parse_args()
    token = os.environ.pop("DASHBOARD_TOKEN")

    checkout = os.path.realpath(args.checkout)
    logs = os.path.join(checkout, "logs")
    os.makedirs(logs, exist_ok=True)
    logging.basicConfig(filename=os.path.join(logs, "dashboard-server.log"), level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    # Default scan root: the folder holding this checkout's project — for the
    # usual <project>/platform + <project>/magento layout that is the folder
    # holding <project> (e.g. ~/Sites), so sibling projects are found too.
    parent = os.path.dirname(checkout)
    if os.path.basename(checkout) == "platform":
        parent = os.path.dirname(parent)
    roots = [os.path.realpath(r) for r in args.scan] or [parent]

    dash = Dashboard(roots, token)
    # Destroy records live outside the instance — its folder is deleted.
    dash.destroy_dir = os.path.join(os.path.dirname(os.path.realpath(args.state)), "destroyed")
    try:
        httpd = Server(("127.0.0.1", args.port), make_handler(dash, args.port))
    except OSError:
        if not args.port_fallback or args.port == 0:
            sys.stderr.write("Port %d is already in use — pick another with --port.\n" % args.port)
            return 1
        httpd = Server(("127.0.0.1", 0), make_handler(dash, 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(dash, port)
    url = "http://127.0.0.1:%d/?token=%s" % (port, token)
    write_state(args.state, {"pid": os.getpid(), "port": port, "token": token, "instance": dash.instance,
                             "url": url, "checkout": checkout})
    LOG.info("dashboard started on port %d (scan roots: %s)", port, ", ".join(roots))

    def stop(*_):
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    dash.stop = stop

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGHUP, stop)
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        # The state file stays: bin/local/dashboard reuses its port next time
        # (a stable port keeps the dash_<port> cookie valid); it sees this pid
        # is gone before trusting anything else in it.
        LOG.info("dashboard stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
