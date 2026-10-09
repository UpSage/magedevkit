"""Destroying an instance (bin/local/destroy) from the dashboard: what would
be lost (destroy_check), the guarded start (destroy) and its progress
(destroy_status). The run is recorded in the dashboard's own cache dir,
since the instance folder — and its logs/ — goes away."""

import os
import re
import subprocess
import sys
import time

import envs
import webcore
from locks import probe_lock
from oplogs import tail_file
from webcore import OpError

OPRUN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "oprun.py")


DESTROY_OP_RE = re.compile(r"^destroy-[a-z0-9_-]{1,40}-[0-9]{8}-[0-9]{6}$")


def _git_count(path, *args):
    """Non-empty output lines of a read-only git command, None if it failed."""
    code, out = envs.run(["git", "--no-optional-locks", "-C", path] + list(args), timeout=15, env=webcore.git_env())
    return len([l for l in out.splitlines() if l.strip()]) if code == 0 else None


def _tree_bytes(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def destroy_check(e):
    """What destroying this instance would lose — shown before asking."""
    if not e.is_instance:
        raise OpError(400, "Only instances can be destroyed.",
                      "This is a checkout of the platform repo itself; it's never deleted from here.")
    m = e.magento_dir
    repo = os.path.isdir(os.path.join(m, ".git"))
    out = {
        "name": e.id, "folder": e.platform_dir, "domain": e.env.get("APP_DOMAIN") or None, "project": e.project,
        "repo": repo,
        "uncommitted": _git_count(m, "status", "--porcelain") if repo else None,
        "unpushed": _git_count(m, "rev-list", "HEAD", "--not", "--remotes") if repo else None,
        "stashes": _git_count(m, "stash", "list") if repo else None,
        "backupsBytes": _tree_bytes(os.path.join(e.platform_dir, "db")),
        "volumes": [],
    }
    code, vols = envs.run(["docker", "volume", "ls", "-q", "--filter", "label=com.docker.compose.project=%s" % e.project],
                          timeout=10)
    if code == 0:
        out["volumes"] = sorted(v for v in vols.split() if v)
    out["dockerOk"] = code == 0
    out["risky"] = bool(out["uncommitted"] or out["unpushed"] or out["stashes"])
    return out


def destroy(e, body, ops_dir):
    check = destroy_check(e)
    e.require_writable()
    if (body or {}).get("confirm") != e.id:
        raise OpError(400, "Type the instance name (%s) to confirm." % e.id)
    if check["risky"] and not (body or {}).get("force"):
        raise OpError(409, "magento/ has work that isn't on GitHub.",
                      "%s uncommitted file(s), %s unpushed commit(s), %s stash(es). Push or save it first, or confirm "
                      "that it may be deleted." % (check["uncommitted"], check["unpushed"], check["stashes"]))
    if not check["dockerOk"]:
        raise OpError(409, "Docker isn't reachable, so the containers and volumes can't be removed. Start Docker first.")
    held, text = probe_lock(e)
    if held:
        raise OpError(409, "Another operation is running in this instance.", text)
    script = os.path.join(e.code_dir, "bin", "local", "destroy")
    if not os.access(script, os.X_OK):
        raise OpError(400, "This instance's platform repo has no bin/local/destroy.")
    os.makedirs(ops_dir, mode=0o700, exist_ok=True)
    op_id = "destroy-%s-%s" % (e.id, time.strftime("%Y%m%d-%H%M%S"))
    record, log = os.path.join(ops_dir, op_id + ".json"), os.path.join(ops_dir, op_id + ".log")
    webcore.write_record(record, {"id": op_id, "kind": "destroy", "label": "Destroy %s" % e.id, "status": "starting",
                                  "env": e.id, "folder": e.platform_dir, "startedAt": webcore.now_iso(),
                                  "domain": check["domain"]})
    cmd = [sys.executable, OPRUN, "--record", record, "--log", log, "--cwd", os.path.dirname(e.platform_dir),
           "--", script, "--yes"]
    env = e.script_env(envs.child_env([e]))
    with open(os.devnull, "rb") as devnull, webcore.open_private(log) as out:
        subprocess.Popen(cmd, cwd=os.path.dirname(e.platform_dir), env=env, stdin=devnull, stdout=out,
                         stderr=subprocess.STDOUT, start_new_session=True, close_fds=True)
    return op_id


def destroy_status(ops_dir, op_id):
    if not DESTROY_OP_RE.match(op_id or ""):
        raise OpError(404, "Unknown destroy operation.")
    rec = webcore.read_record(os.path.join(ops_dir, op_id + ".json"))
    if not rec:
        raise OpError(404, "Unknown destroy operation.")
    text = tail_file(os.path.join(ops_dir, op_id + ".log"), 200)
    # The instance (and so its literal secrets) may already be gone: patterns only.
    if text:
        text = "\n".join(webcore.redact_patterns(webcore.clean_line(l)) for l in text.split("\n"))
    return {"operation": rec, "text": text}
