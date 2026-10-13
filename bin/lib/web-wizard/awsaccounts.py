"""The dashboard's AWS accounts page: MageDevKit's own AWS connections — the
logins it uses to create and control AWS environments (bin/lib/aws.sh).

They live in ~/.config/magedevkit/aws, apart from ~/.aws and from any
store's own AWS keys, and belong to no instance. So:
  * the list comes from bin/lib/aws-credentials.py (local files, no secrets)
    and each connection's account and status from bin/lib/aws-profiles.py,
    run in the background and kept for 5 minutes (it calls AWS);
  * adding, importing and SSO sign-in run bin/local/aws-account in the web
    wizard (launch.sh account …), where secrets are typed on its page and
    never logged; removing runs `aws-account remove NAME --yes`.
Those runs keep their record and log in this platform repo (Home), which is
where the wizard launcher puts a run that has no instance. Every child gets
envs.child_env: no AWS login from the dashboard's own environment.
"""

import json
import os
import re
import subprocess
import threading
import time

import ops
import webcore

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")  # bin/lib/util.sh valid_aws_profile
CHECK_TTL = 300
WIZARD_ACTIONS = {"add": "Add an AWS connection", "import": "Import an AWS profile", "login": "Sign in to SSO again"}


class Home:
    """The stand-in 'environment' ops uses for these runs: their records and
    logs go to <repo>/logs, their lock is the repo's .wizard.lock."""

    id = "aws-accounts"
    name = "AWS accounts"
    env = {}
    stack = {}

    def __init__(self, code_dir):
        self.platform_dir = self.code_dir = code_dir
        self.magento_dir = os.path.join(code_dir, ".no-magento")  # nothing to read: Redactor finds no env.php

    @staticmethod
    def secret_values():
        return []


class Accounts:
    def __init__(self, code_dir):
        self.home = Home(code_dir)
        self.lib = os.path.join(code_dir, "bin", "lib")
        self.script = os.path.join(code_dir, "bin", "local", "aws-account")
        self.launch = os.path.join(code_dir, "bin", "lib", "web-wizard", "launch.sh")
        self.lock = threading.Lock()
        self.check = {"status": "never", "at": 0, "rows": {}, "error": None}

    # -- reading -----------------------------------------------------------

    def _env(self, all_envs):
        return ops.child_env(all_envs)

    def _connections(self, all_envs):
        try:
            p = subprocess.run(["python3", os.path.join(self.lib, "aws-credentials.py"), "list"], env=self._env(all_envs),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            rows = json.loads(p.stdout) if p.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired):
            rows = None
        if rows is None:
            raise ops.OpError(500, "Couldn't read MageDevKit's AWS connections.")
        return rows

    @staticmethod
    def _cli():
        try:
            p = subprocess.run(["aws", "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10,
                               env=webcore.base_env(AWS_PAGER=""))
            m = re.search(r"aws-cli/([0-9.]+)", p.stdout + p.stderr)
            return {"installed": True, "version": m.group(1) if m else ""}
        except (OSError, subprocess.TimeoutExpired):
            return {"installed": False, "version": ""}

    def _run_check(self, all_envs):
        try:
            p = subprocess.run(["python3", os.path.join(self.lib, "aws-profiles.py")], env=self._env(all_envs),
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=90)
            rows = json.loads(p.stdout) if p.returncode == 0 else None
            result = {"status": "done", "at": time.time(), "error": None,
                      "rows": {r["name"]: r for r in rows}} if rows is not None else \
                     {"status": "error", "at": time.time(), "rows": {}, "error": webcore.redact_patterns(p.stderr.strip())[:300]}
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            result = {"status": "error", "at": time.time(), "rows": {}, "error": e.__class__.__name__}
        with self.lock:
            self.check = result

    def status(self, all_envs, recheck=False):
        conns = self._connections(all_envs)
        with self.lock:
            # Re-check when it's old, or when a connection was added or removed since.
            stale = time.time() - self.check["at"] > CHECK_TTL or (
                self.check["status"] == "done" and set(self.check["rows"]) != {c["name"] for c in conns})
            if self.check["status"] != "checking" and conns and (recheck or stale):
                self.check = dict(self.check, status="checking")
                threading.Thread(target=self._run_check, args=(all_envs,), daemon=True).start()
            check = dict(self.check)
        for c in conns:
            r = check["rows"].get(c["name"])
            c.update({k: r.get(k, "") for k in ("status", "account", "alias", "error")} if r else
                     {"status": "unchecked", "account": "", "alias": "", "error": ""})
        recent = [o for o in ops.list_operations(self.home, limit=20) if o.get("kind") == "account"][:5]
        return {"connections": conns, "check": {"status": check["status"], "checkedAt": check["at"] or None,
                                                "error": check["error"]},
                "cli": self._cli(), "dir": "~/.config/magedevkit/aws", "operations": recent}

    # -- changing ----------------------------------------------------------

    def start(self, action, name, all_envs):
        """The AWS connection wizard for ACTION (add, import, login NAME)."""
        if action not in WIZARD_ACTIONS:
            raise ops.OpError(400, "Unknown AWS connection action.")
        args = [action]
        if action == "login":
            if not isinstance(name, str) or not NAME_RE.match(name):
                raise ops.OpError(400, "Name the connection to sign in to.")
            args.append(name)
        held, text = ops.probe_lock(self.home)
        if held:
            raise ops.OpError(409, "Another wizard is running in the platform repo.", text)
        op_id = ops._spawn(self.home, "account", [self.launch, "account"] + args + ["--no-browser"], self._env(all_envs),
                           workflow=True, label=WIZARD_ACTIONS[action] + (" (%s)" % name if action == "login" else ""),
                           extra={"args": args})
        deadline = time.time() + 20
        while time.time() < deadline:
            url = ops.wizard_url(self.home, op_id)
            if url:
                self.forget()
                return op_id, url
            if ops.get_operation(self.home, op_id).get("status") not in ("starting", "running"):
                break
            time.sleep(0.2)
        rec = ops.get_operation(self.home, op_id)
        raise ops.OpError(500, "The wizard didn't start.",
                          ops.Redactor(self.home)(ops.tail_file(os.path.join(self.home.platform_dir, rec.get("logFile", "")), 30) or ""))

    def remove(self, name, all_envs):
        if not isinstance(name, str) or not NAME_RE.match(name):
            raise ops.OpError(400, "Unknown connection.")
        if name not in {c["name"] for c in self._connections(all_envs)}:
            raise ops.OpError(404, "There's no MageDevKit connection called %s." % name)
        try:
            p = subprocess.run([self.script, "remove", name, "--yes"], env=self._env(all_envs), cwd=self.home.platform_dir,
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ops.OpError(500, "Couldn't remove the connection.", e.__class__.__name__)
        if p.returncode != 0:
            lines = [webcore.redact_patterns(webcore.clean_line(l)) for l in p.stderr.splitlines() if l.strip()]
            raise ops.OpError(400, "Couldn't remove the connection.", "\n".join(lines[-3:]))
        self.forget()
        return {"ok": True}

    def forget(self):
        """Next view checks again (a connection was added, signed in or removed)."""
        with self.lock:
            if self.check["status"] != "checking":
                self.check = dict(self.check, at=0)
