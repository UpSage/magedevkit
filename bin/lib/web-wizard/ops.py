"""Dashboard operations: the whitelisted actions, the per-checkout lock,
operation history and log views.

Every action is a fixed argv of one of the checkout's own wrappers (bin/platform,
bin/magento, bin/composer, bin/mode/*) — the dashboard never builds a shell
command and never runs anything that isn't in ACTIONS. Setup / refresh /
version run the checkout's own web wizard (bin/lib/web-wizard/launch.sh).

Mutating actions take the checkout lock (locks.py). Log views are redacted
(oplogs.py); destroying an instance lives in destroy.py. The names those
modules export are re-exported here, so callers only need `ops`.
"""

import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import tempfile
import time

import webcore
# Re-exported, so callers keep using ops.<name>.
from destroy import destroy, destroy_check, destroy_status  # noqa: F401
from envs import SERVICES, child_env  # noqa: F401
from locks import acquire_lock, lock_path, probe_lock  # noqa: F401
from modules import parse_modules, update_specs
from oplogs import Redactor, tail_file  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
OPRUN = os.path.join(HERE, "oprun.py")
MODES = ("default", "developer", "production")
LOG_LINES_MAX = 2000
MAGENTO_LOGS = ("system", "exception", "debug")
LOG_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}\.log$")
# <flow>[-<n>]-<stamp>.log: bin/lib/ui.sh ui_log_to_file and server.py (which
# adds -<n> when two runs start in the same second).
RUN_LOG_RE = re.compile(r"^(setup|refresh|version|pipeline|rebuild|ownership)(?:-\d+)?-(\d{8}-\d{6})\.log$")

# name → definition. argv paths are relative to the checkout. `mutating`
# actions take the checkout lock; `confirm` is the dialog text the page shows.
# Only `cancellable` actions can be stopped from the page: the others run
# inside the php container (docker compose exec), where stopping the client
# would leave the command running in the container without the lock.
ACTIONS = {
    "start": {"label": "Start environment", "argv": ["bin/platform", "up", "-d"], "mutating": True, "cancellable": True},
    "stop": {"label": "Stop environment", "argv": ["bin/platform", "stop"], "mutating": True, "cancellable": True,
             "confirm": "Stops every container of this stack. The site is unavailable until you start it again. Data volumes are kept."},
    "restart": {"label": "Restart", "argv": ["bin/platform", "restart"], "mutating": True, "cancellable": True, "arg": "service",
                "confirm": "Restarts the containers. The site is briefly unavailable."},
    "cache_flush": {"label": "Flush Magento cache", "argv": ["bin/magento", "cache:flush"], "mutating": True},
    "cache_clean": {"label": "Clean Magento cache", "argv": ["bin/magento", "cache:clean"], "mutating": True},
    "indexer_status": {"label": "Indexer status", "argv": ["bin/magento", "indexer:status"], "mutating": False},
    "reindex": {"label": "Reindex", "argv": ["bin/magento", "indexer:reindex"], "mutating": True,
                "confirm": "Runs a full reindex. It can take several minutes and the storefront may be slower meanwhile."},
    "mode_show": {"label": "Show Magento mode", "argv": ["bin/mode/show"], "mutating": False},
    "mode_set": {"label": "Switch Magento mode", "argv": ["bin/mode/{mode}"], "mutating": True, "arg": "mode",
                 "confirm": "Switches the Magento mode. Production mode compiles code and deploys static content, which takes several minutes."},
    # Magento-only build (bin/local/magento-build): maintenance on, setup:upgrade,
    # di:compile, static content, cache flush, maintenance off — optionally
    # after composer install/update. Never touches the containers.
    "magento_build": {"label": "Build Magento", "argv": ["bin/local/magento-build"], "mutating": True, "arg": "build",
                      "confirm": "Runs setup:upgrade, setup:di:compile, setup:static-content:deploy -f and cache:flush with "
                                 "maintenance mode on. The containers aren't touched; the store shows a maintenance page "
                                 "while it builds (a few minutes)."},
    "composer_validate": {"label": "composer validate", "argv": ["bin/composer", "validate", "--no-check-publish"], "mutating": False},
    "composer_install": {"label": "composer install", "argv": ["bin/composer", "install", "--no-interaction"], "mutating": True,
                         "confirm": "Installs the packages from composer.lock into vendor/."},
    "composer_update": {"label": "composer update", "argv": ["bin/composer", "update", "--no-interaction"], "mutating": True,
                        "arg": "packages",
                        "confirm": "Updates packages to the newest versions composer.json allows and rewrites composer.lock. "
                                   "Test the store afterwards, then commit composer.lock in magento/. "
                                   "Upgrading Magento itself belongs to the version wizard."},
    # bin/local/magento-module: module:enable|disable --clear-static-content
    # (never --force), with `build` the Magento-only build around it.
    "module_enable": {"label": "Enable modules", "argv": ["bin/local/magento-module", "enable"], "mutating": True,
                      "arg": "modules"},
    "module_disable": {"label": "Disable modules", "argv": ["bin/local/magento-module", "disable"], "mutating": True,
                       "arg": "modules"},
    # composer outdated --direct --all → logs/module-updates.json. Locked:
    # it uses Composer's cache and the network, so it never overlaps an update.
    "module_check_updates": {"label": "Check for module updates", "argv": ["bin/local/magento-module", "check-updates"],
                             "mutating": True},
    # composer require --update-with-dependencies for the chosen packages
    # (modules.update_specs), as a dry run or followed by the build.
    "module_update": {"label": "Update packages", "argv": ["bin/local/magento-module", "update"], "mutating": True,
                      "arg": "update"},
}

WORKFLOWS = {
    "setup": {"label": "Setup", "requires_env": False},
    "refresh": {"label": "Refresh database", "requires_env": True},
    "version": {"label": "Switch Magento version", "requires_env": True},
    # Runs bin/ownership/<model> in the wizard, which asks for the sudo
    # password on its page (the dashboard itself can't prompt).
    "ownership": {"label": "Switch file ownership", "requires_env": True, "arg": "model"},
    # Writes the pipeline kit into magento/deploy/ (bin/local/pipeline) —
    # needs magento/ to be a git checkout, since the kit is committed with it.
    "pipeline": {"label": "Make pipeline-ready", "requires_env": True, "requires_repo": True},
    # Full rebuild (bin/local/rebuild), optionally after composer install /
    # update — composer alone never finishes a change (setup:upgrade, DI
    # compile and static content have to follow).
    "rebuild": {"label": "Rebuild Magento", "requires_env": True, "arg": "composer"},
}
OWNERSHIP_MODELS = ("single", "two-owner")
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9*][a-z0-9._*-]*$")
MAX_PACKAGES = 20


OpError = webcore.OpError  # re-exported: dashboard.py and gitrepo raise ops.OpError


# ---------------------------------------------------------------------------
# Operations (records + detached runner)
# ---------------------------------------------------------------------------

pid_alive = webcore.pid_alive


def resolve(rec):
    """The record with its status checked against the processes it names:
    a 'running' record whose process is gone becomes 'interrupted'."""
    rec = dict(rec)
    status = rec.get("status")
    if status == "starting" and not rec.get("pid"):
        # Just created; the runner hasn't written its pid yet.
        started = _parse_time(rec.get("startedAt"))
        if started is None or time.time() - started > 60:
            rec["status"] = "interrupted"
    elif status in ("running", "starting"):
        alive = pid_alive(rec.get("pid"), "oprun.py") or pid_alive(rec.get("serverPid"), "server.py")
        if not alive:
            rec["status"] = "interrupted"
    return rec


def _parse_time(iso):
    try:
        return time.mktime(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))
    except (TypeError, ValueError):
        return None


def list_operations(e, limit=30):
    d = webcore.operations_dir(e.platform_dir)
    out = []
    try:
        names = sorted((n for n in os.listdir(d) if n.endswith(".json") and webcore.OP_ID_RE.match(n[:-5])), reverse=True)
    except OSError:
        names = []
    for n in names[:limit]:
        rec = webcore.read_record(os.path.join(d, n))
        if rec:
            rec.setdefault("id", n[:-5])
            rec = resolve(rec)
            # The wizard page stays usable after a failure (it offers Run again)
            # for as long as its server — and so the .url file — is there.
            rec["hasUrl"] = os.path.exists(os.path.join(d, n[:-5] + ".url")) and (
                pid_alive(rec.get("pid"), "oprun.py") or pid_alive(rec.get("serverPid"), "server.py"))
            out.append(rec)
    return out


def older_logs(e, limit=10):
    """Run logs without a record (terminal runs, older wizards): name + time only."""
    d = os.path.join(e.platform_dir, "logs")
    try:
        names = [n for n in os.listdir(d) if LOG_NAME_RE.match(n)]
    except OSError:
        return []
    out = []
    for n in names:
        m = RUN_LOG_RE.match(n)
        if m:
            out.append({"kind": m.group(1), "stamp": m.group(2), "logFile": "logs/" + n})
    return sorted(out, key=lambda r: r["stamp"], reverse=True)[:limit]


def get_operation(e, op_id):
    if not webcore.OP_ID_RE.match(op_id or ""):
        raise OpError(404, "Unknown operation.")
    path = os.path.join(webcore.operations_dir(e.platform_dir), op_id + ".json")
    rec = webcore.read_record(path)
    if not rec:
        raise OpError(404, "Unknown operation.")
    rec.setdefault("id", op_id)
    return resolve(rec)


def wizard_url(e, op_id):
    if not webcore.OP_ID_RE.match(op_id or ""):
        return None
    try:
        with open(os.path.join(webcore.operations_dir(e.platform_dir), op_id + ".url"), encoding="utf-8") as f:
            url = f.read().strip()
    except OSError:
        return None
    return url if re.match(r"^http://127\.0\.0\.1:\d+/\?token=[A-Za-z0-9_-]+$", url) else None


def _spawn(e, kind, argv, env, lock_fd=None, workflow=False, label="", extra=None, oprun_args=()):
    op_id = webcore.new_op_id("%s-%s" % (kind, secrets.token_hex(2)))
    ops_dir = webcore.operations_dir(e.platform_dir, create=True)
    record = os.path.join(ops_dir, op_id + ".json")
    log = os.path.join(e.platform_dir, "logs", "dashboard-%s.log" % op_id)
    rec = {"id": op_id, "kind": kind, "label": label, "source": "dashboard", "status": "starting",
           "env": e.id, "startedAt": webcore.now_iso(), "logFile": os.path.relpath(log, e.platform_dir)}
    rec.update(extra or {})
    webcore.write_record(record, rec)
    cmd = [sys.executable, OPRUN, "--record", record, "--log", log, "--cwd", e.platform_dir, "--lock-label", label]
    if lock_fd is not None:
        cmd += ["--lock-fd", str(lock_fd)]
    if workflow:
        cmd.append("--workflow")
        env = dict(env, WIZARD_OPERATION_ID=op_id)
    cmd += list(oprun_args) + ["--"] + argv
    with open(os.devnull, "rb") as devnull, webcore.open_private(log) as out:
        subprocess.Popen(cmd, cwd=e.platform_dir, env=env, stdin=devnull, stdout=out, stderr=subprocess.STDOUT,
                         start_new_session=True, close_fds=True,
                         pass_fds=(lock_fd,) if lock_fd is not None else ())
    return op_id


# git fetch/pull/push for the Git tab (gitrepo.network). With `unlock` (a
# pull in production mode) the instance's permission scripts open the code
# for the pull and lock it again after.
GIT_UNLOCKED = '"$1" || exit 1; lock="$2"; dir="$3"; shift 3; git -C "$dir" "$@"; rc=$?; "$lock"; exit "$rc"'
ASKPASS = '#!/bin/sh\nd="$(dirname "$0")"\ncase "$1" in Username*) cat "$d/user" ;; *) cat "$d/token" ;; esac\n'


def spawn_git(e, kind, label, git_args, login, all_envs, unlock=False):
    if unlock:
        perms = os.path.join(e.code_dir, "bin", "permissions")
        argv = ["bash", "-c", GIT_UNLOCKED, "git-op", os.path.join(perms, "unlock"), os.path.join(perms, "lock"),
                e.magento_dir] + list(git_args)
    else:
        argv = ["git", "-C", e.magento_dir] + list(git_args)
    env = e.script_env(child_env(all_envs))
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", LC_ALL="C", SSH_ASKPASS="/bin/false")
    lock_fd = acquire_lock(e)  # before the login touches disk: a 409 leaves nothing behind
    creds = None
    try:
        oprun_args = []
        if login:
            # The login lives only in this private dir, which oprun deletes when
            # git ends — never in argv, the environment of the record, or a log.
            creds = write_login(login)
            env["GIT_ASKPASS"] = os.path.join(creds, "askpass")
            oprun_args = ["--cleanup", creds]
        return _spawn(e, kind, argv, env, lock_fd=lock_fd, label=label, oprun_args=oprun_args,
                      extra={"cancellable": True, "git": True})
    except BaseException:
        if creds:
            shutil.rmtree(creds, ignore_errors=True)
        raise
    finally:
        os.close(lock_fd)


def write_login(login):
    """A private (0700) temp dir holding user, token (0600) and the askpass
    script that reads them. The caller owns the dir and must delete it."""
    d = tempfile.mkdtemp(prefix="dash-git-")
    try:
        os.chmod(d, 0o700)
        for name, text, mode in (("user", login[0], 0o600), ("token", login[1], 0o600), ("askpass", ASKPASS, 0o700)):
            fd = os.open(os.path.join(d, name), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            with os.fdopen(fd, "w") as f:
                f.write(text)
    except BaseException:
        shutil.rmtree(d, ignore_errors=True)
        raise
    return d


def parse_packages(body):
    """body["packages"] (list, or a space-separated string) → validated list."""
    pkgs = (body or {}).get("packages") or []
    if isinstance(pkgs, str):
        pkgs = pkgs.split()
    if not isinstance(pkgs, list) or len(pkgs) > MAX_PACKAGES \
            or not all(isinstance(p, str) and PACKAGE_RE.match(p) for p in pkgs):
        raise OpError(400, "Package names must look like vendor/package (at most %d)." % MAX_PACKAGES)
    return pkgs


def parse_composer(body):
    """body["composer"] ("", install, update) + packages → (mode, packages).
    Same rules as bin/local/magento-build and bin/local/rebuild --composer."""
    mode = (body or {}).get("composer") or ""
    if mode not in ("", "install", "update"):
        raise OpError(400, "composer must be install or update.")
    pkgs = parse_packages(body)
    if pkgs and mode != "update":
        raise OpError(400, "Package names only go with composer update.")
    return mode, pkgs


def run_action(e, name, body, all_envs):
    e.require_writable()
    spec = ACTIONS.get(name)
    if not spec:
        raise OpError(404, "Unknown action.")
    argv = list(spec["argv"])
    label = spec["label"]
    if spec.get("arg") == "service":
        svc = (body or {}).get("service")
        if svc:
            if svc not in SERVICES:
                raise OpError(400, "Unknown service.")
            argv.append(svc)
            label = "Restart %s" % svc
    elif spec.get("arg") == "packages":
        pkgs = parse_packages(body)
        argv.extend(pkgs)
        if pkgs:
            label = "composer update %s" % " ".join(pkgs)
    elif spec.get("arg") == "build":
        mode, pkgs = parse_composer(body)
        if mode:
            argv += ["--composer", mode] + pkgs
            label = "composer %s%s + build" % (mode, " " + " ".join(pkgs) if pkgs else "")
    elif spec.get("arg") == "modules":
        mods = parse_modules(e, body)
        build = (body or {}).get("build", True)
        if not isinstance(build, bool):
            raise OpError(400, "build must be true or false.")
        if not build and e.production:
            raise OpError(400, "In production mode a module change needs the build too (the code is locked and compiled).")
        argv += (["--build"] if build else []) + mods
        label = "%s %s%s" % (label.split()[0], mods[0] if len(mods) == 1 else "%d modules" % len(mods),
                             " + build" if build else "")
    elif spec.get("arg") == "update":
        # Dry run unless the page says otherwise: a real update must be asked
        # for explicitly (the page sends dryRun: false only after a dry run
        # of the same packages passed).
        dry, build = (body or {}).get("dryRun", True), (body or {}).get("build", True)
        if not isinstance(dry, bool) or not isinstance(build, bool):
            raise OpError(400, "dryRun and build must be true or false.")
        if not dry and not build and e.production:
            raise OpError(400, "In production mode an update needs the full build (di:compile and static content).")
        specs = update_specs(e, body)
        argv += (["--dry-run"] if dry else []) + ([] if dry or build else ["--quick"]) + specs
        what = specs[0].split(":")[0] if len(specs) == 1 else "%d packages" % len(specs)
        label = "Dry run: update %s" % what if dry else "Update %s%s" % (what, " + build" if build else "")
    elif spec.get("arg") == "mode":
        mode = (body or {}).get("mode")
        if mode not in MODES:
            raise OpError(400, "Mode must be default, developer or production.")
        if e.two_owner:
            raise OpError(400, "Switching mode needs sudo with two-owner file ownership.",
                          "Run it in the terminal instead: %s" % e.cmd_hint("mode", mode))
        argv[0] = argv[0].format(mode=mode)
        label = "Switch to %s mode" % mode
    script = os.path.join(e.code_dir, argv[0])
    if not os.path.isfile(script) or not os.access(script, os.X_OK):
        raise OpError(400, "This environment's platform scripts don't have %s." % argv[0])
    argv[0] = script
    env = e.script_env(child_env(all_envs))
    lock_fd = acquire_lock(e) if spec["mutating"] else None
    if lock_fd is not None:
        env["WIZARD_LOCK_HELD"] = "1"  # the runner holds the lock for the child's whole run
    try:
        return _spawn(e, name, argv, env, lock_fd=lock_fd, label=label,
                      extra={"cancellable": bool(spec.get("cancellable"))})
    finally:
        if lock_fd is not None:
            os.close(lock_fd)  # the runner holds its inherited copy now


def start_workflow(e, flow, all_envs, body=None):
    e.require_writable()
    if flow not in WORKFLOWS:
        raise OpError(404, "Unknown workflow.")
    args, label = [], WORKFLOWS[flow]["label"]
    if WORKFLOWS[flow].get("arg") == "model":
        model = (body or {}).get("model")
        if model not in OWNERSHIP_MODELS:
            raise OpError(400, "Ownership must be single or two-owner.")
        if model == (e.env.get("FILE_OWNERSHIP") or "single"):
            raise OpError(409, "This instance already uses %s ownership." % model)
        args, label = [model], "Switch file ownership to %s" % model
    elif WORKFLOWS[flow].get("arg") == "composer":
        mode, pkgs = parse_composer(body)
        args = ([mode] if mode else []) + pkgs
        label = {"": "Rebuild Magento", "install": "composer install + rebuild",
                 "update": "composer update%s + rebuild" % (" " + " ".join(pkgs) if pkgs else "")}[mode]
    if WORKFLOWS[flow].get("requires_repo"):
        e.require_repo("The pipeline kit is committed with the store, so magento/ has to be a clone of its repository.")
    launch = os.path.join(e.code_dir, "bin", "lib", "web-wizard", "launch.sh")
    if not os.path.isfile(launch):
        raise OpError(400, "This checkout has no browser wizard.",
                      "Run it in the terminal: %s" % e.cmd_hint(flow))
    held, text = probe_lock(e)
    if held:
        raise OpError(409, "Another operation is running in this checkout.", text)
    op_id = _spawn(e, flow, [launch, flow] + args + ["--no-browser"], e.script_env(child_env(all_envs)), workflow=True,
                   label=label, extra={"args": args})
    deadline = time.time() + 20
    while time.time() < deadline:
        url = wizard_url(e, op_id)
        if url:
            return op_id, url
        rec = get_operation(e, op_id)
        if rec.get("status") not in ("starting", "running"):
            break
        time.sleep(0.2)
    rec = get_operation(e, op_id)
    raise OpError(500, "The wizard didn't start.",
                  Redactor(e)(tail_file(os.path.join(e.platform_dir, rec.get("logFile", "")), 30) or ""))


def cancel(e, op_id):
    rec = get_operation(e, op_id)
    if rec.get("source") != "dashboard" or rec.get("kind") in WORKFLOWS:
        raise OpError(400, "Only dashboard actions can be cancelled here — use Cancel on the wizard page.")
    if not rec.get("cancellable"):
        raise OpError(400, "This command runs inside the container and can't be stopped from here.",
                      "Wait for it to finish. Stopping only the client would leave it running in the container.")
    if rec.get("status") != "running" or not pid_alive(rec.get("pid"), "oprun.py"):
        raise OpError(409, "This operation isn't running.")
    try:
        os.killpg(rec["pid"], signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        raise OpError(409, "This operation isn't running.")
    return True


def log_sources(e):
    out = [{"id": "service:" + s, "label": "Container · %s" % s} for s in SERVICES]
    out += [{"id": "magento:" + n, "label": "Magento · var/log/%s.log" % n} for n in MAGENTO_LOGS]
    d = os.path.join(e.platform_dir, "logs")
    try:
        names = sorted((n for n in os.listdir(d) if LOG_NAME_RE.match(n)), reverse=True)[:40]
    except OSError:
        names = []
    out += [{"id": "file:" + n, "label": "Platform · logs/%s" % n} for n in names]
    return out


def read_log(e, source, lines, all_envs):
    try:
        lines = max(10, min(LOG_LINES_MAX, int(lines or 300)))
    except (TypeError, ValueError):
        raise OpError(400, "lines must be a number.")
    kind, _, name = (source or "").partition(":")
    if kind == "service":
        if name not in SERVICES:
            raise OpError(400, "Unknown log source.")
        code, text = _run(e, ["bin/platform", "logs", "--no-color", "--timestamps", "--tail", str(lines), name],
                          all_envs, timeout=20)
        if code is None:
            raise OpError(500, "Couldn't read the container log.", "docker compose logs timed out")
    elif kind == "magento":
        if name not in MAGENTO_LOGS:
            raise OpError(400, "Unknown log source.")
        base = os.path.realpath(os.path.join(e.magento_dir, "var", "log"))
        text = tail_file(os.path.join(base, name + ".log"), lines)
        if text is None:
            text = "(var/log/%s.log doesn't exist yet)" % name
    elif kind in ("file", "op"):
        base = os.path.realpath(os.path.join(e.platform_dir, "logs"))
        if kind == "op":
            name = get_operation(e, name).get("logFile", "")
            if name.startswith("logs/"):
                name = name[len("logs/"):]
        if not LOG_NAME_RE.match(name or ""):
            raise OpError(400, "Unknown log source.")
        path = os.path.realpath(os.path.join(base, name))
        if os.path.dirname(path) != base or not os.path.isfile(path):
            raise OpError(400, "Unknown log source.")
        text = tail_file(path, lines) or ""
    else:
        raise OpError(400, "Unknown log source.")
    return Redactor(e)(text)


def _run(e, argv, all_envs, timeout=20):
    script = os.path.join(e.code_dir, argv[0])
    try:
        p = subprocess.run([script] + argv[1:], cwd=e.platform_dir, env=e.script_env(child_env(all_envs)),
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           timeout=timeout, start_new_session=True)
        return p.returncode, p.stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired):
        return None, ""
