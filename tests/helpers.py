"""Shared fixtures for the web-wizard / dashboard tests: fake checkouts whose
bin/ scripts only record how they were called, and a fake `docker`."""

import http.client
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB = os.path.join(REPO, "bin", "lib", "web-wizard")
if WEB not in sys.path:
    sys.path.insert(0, WEB)

# Records argv, cwd and the variables a leaked environment would carry.
RECORDER = """#!/bin/bash
{
  printf 'argv=%s\\n' "$0 $*"
  printf 'cwd=%s\\n' "$PWD"
  printf 'APP_NAME=%s COMPOSE_PROJECT_NAME=%s WIZARD_LOCK_HELD=%s\\n' "${APP_NAME-unset}" "${COMPOSE_PROJECT_NAME-unset}" "${WIZARD_LOCK_HELD-unset}"
  printf 'PLATFORM_INSTANCE_DIR=%s MAGENTO_DIR=%s\\n' "${PLATFORM_INSTANCE_DIR-unset}" "${MAGENTO_DIR-unset}"
} >> "$(dirname "$0")/../calls.log"
[ -n "${FAKE_SLEEP:-}" ] && sleep "$FAKE_SLEEP"
echo "password=supersecretvalue123 done"
exit 0
"""

FAKE_LAUNCH = """#!/bin/bash
echo "launch.sh $*" >> "$(dirname "$0")/../../../calls.log"
echo
echo "  Fake wizard is running."
echo
echo "  Open:  http://127.0.0.1:4242/?token=fakeTOKEN_123"
echo
sleep 3
exit 0
"""


def write(path, text, mode=0o644):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    os.chmod(path, mode)


def copy_scripts(repo, *rels):
    """Copies the real bin/lib/ (the shared helpers every script sources) plus
    the given files into a throwaway repo — so a new helper file never needs
    adding to a test's file list."""
    shutil.copytree(os.path.join(REPO, "bin", "lib"), os.path.join(repo, "bin", "lib"), dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__"))
    for rel in rels:
        dst = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(REPO, rel), dst)


def make_checkout(root, project, app_name, domain, extra_env="", with_launch=True):
    """<root>/<project>/platform (+ magento/) laid out like a real checkout."""
    pd = os.path.join(root, project, "platform")
    for rel in ("bin/platform", "bin/magento", "bin/composer", "bin/mode/show", "bin/mode/developer",
                "bin/mode/default", "bin/mode/production"):
        write(os.path.join(pd, rel), RECORDER, 0o755)
    # bin/mode/* sit one level deeper: point their calls.log at the same file
    for rel in ("bin/mode/show", "bin/mode/developer", "bin/mode/default", "bin/mode/production", "bin/local/magento-build",
                "bin/local/magento-module"):
        write(os.path.join(pd, rel), RECORDER.replace('"$(dirname "$0")/../calls.log"', '"$(dirname "$0")/../../calls.log"'), 0o755)
    write(os.path.join(pd, "compose", "docker-compose.yml"), "name: ${APP_NAME:-app}-platform\n")
    write(os.path.join(pd, "env", ".env.local"),
          "APP_NAME=%s\nAPP_DOMAIN=%s\nPHP_VERSION=8.4\nMAGENTO_MODE=developer\nDB_PASSWORD=dbpassword-%s-xyz\n%s"
          % (app_name, domain, app_name, extra_env))
    if with_launch:
        write(os.path.join(pd, "bin", "lib", "web-wizard", "launch.sh"), FAKE_LAUNCH, 0o755)
    md = os.path.join(root, project, "magento")
    write(os.path.join(md, "app", "etc", "env.php"),
          "<?php return ['MAGE_MODE' => 'developer', 'crypt' => ['key' => 'cryptkey0123456789abcdef'], "
          "'db' => ['connection' => ['default' => ['password' => 'envphp-db-password']]]];\n")
    write(os.path.join(md, "var", "log", "system.log"),
          "line with cryptkey0123456789abcdef inside\nmysql://user:hunter2hunter2@db/x\nplain line\n")
    return pd


def make_repo(root, name="platform-repo"):
    """A fake shared platform repo: recorder bin/ scripts, paths.sh marker, fake launch.sh."""
    repo = os.path.join(root, name)
    for rel in ("bin/platform", "bin/magento", "bin/composer"):
        write(os.path.join(repo, rel), RECORDER, 0o755)
    for rel in ("bin/mode/show", "bin/mode/developer", "bin/mode/default", "bin/mode/production"):
        write(os.path.join(repo, rel), RECORDER.replace('"$(dirname "$0")/../calls.log"', '"$(dirname "$0")/../../calls.log"'), 0o755)
    write(os.path.join(repo, "bin", "lib", "paths.sh"), "# marker\n")
    write(os.path.join(repo, "bin", "lib", "web-wizard", "launch.sh"), FAKE_LAUNCH, 0o755)
    return repo


def make_instance(root, name, repo, domain=None):
    """An instance folder like bin/local/new-instance makes (no shims needed here)."""
    d = os.path.join(root, name)
    write(os.path.join(d, ".platform-instance"), "PLATFORM_REPO=%s\n" % repo)
    write(os.path.join(d, ".env"), "APP_NAME=%s\nAPP_DOMAIN=%s\nPHP_VERSION=8.4\nMAGENTO_MODE=developer\n"
          % (name, domain or name + ".test"))
    for sub in ("logs", "db", "nginx", "certs"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    write(os.path.join(d, "magento", "app", "etc", "env.php"), "<?php return ['MAGE_MODE' => 'production'];\n")
    return d


def fake_docker_dir(tmp, containers=None):
    """A PATH dir with a `docker` that lists `containers` (inspect JSON)."""
    d = os.path.join(tmp, "fakebin")
    data = os.path.join(tmp, "docker.json")
    with open(data, "w") as f:
        json.dump(containers or [], f)
    write(os.path.join(d, "docker"), """#!/usr/bin/env python3
import json, sys
data = json.load(open(%r))
args = sys.argv[1:]
if args[:2] == ["ps", "-aq"]:
    print("\\n".join(c["Id"] for c in data)); sys.exit(0)
if args[:1] == ["inspect"]:
    print(json.dumps([c for c in data if c["Id"] in args[1:]])); sys.exit(0)
sys.exit(1)
""" % data, 0o755)
    return d


def container(cid, project, service, working_dir, state="running", health=None, instance_dir=None):
    lb = {"com.docker.compose.project": project, "com.docker.compose.service": service,
          "com.docker.compose.project.working_dir": working_dir}
    if instance_dir:
        lb["platform.instance_dir"] = instance_dir
    c = {
        "Id": cid, "Name": "/%s-%s-1" % (project, service),
        "Config": {"Labels": lb, "Image": "img"},
        "State": {"Status": state, "StartedAt": "2026-10-08T10:00:00Z", "ExitCode": 0},
        "NetworkSettings": {"Ports": {}}, "Mounts": [],
    }
    if health:
        c["State"]["Health"] = {"Status": health, "Log": []}
    return c


class Dashboard:
    """Runs dashboard.py on a free port against fake checkouts."""

    def __init__(self, checkout, scan, path_dir, extra_env=None):
        self.tmp = tempfile.mkdtemp(prefix="dash-test-")
        self.state = os.path.join(self.tmp, "state.json")
        self.token = "test-token-0123456789"
        # Its own release cache, and no network for the background release
        # check (releases.py) — the tests never touch ~/.cache or Adobe.
        env = dict(os.environ, DASHBOARD_TOKEN=self.token, PATH=path_dir + os.pathsep + os.environ["PATH"],
                   XDG_CACHE_HOME=os.path.join(self.tmp, "cache"), HTTPS_PROXY="http://127.0.0.1:9",
                   https_proxy="http://127.0.0.1:9")
        env.update(extra_env or {})
        self.proc = subprocess.Popen(
            [sys.executable, os.path.join(WEB, "dashboard.py"), "--checkout", checkout, "--scan", scan,
             "--state", self.state], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            if os.path.exists(self.state):
                break
            time.sleep(0.05)
        with open(self.state) as f:
            self.port = json.load(f)["port"]
        self.cookie = "dash_%d=%s" % (self.port, self.token)

    def request(self, method, path, body=None, headers=None, cookie=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        h = {"Host": host or "127.0.0.1:%d" % self.port}
        if cookie:
            h["Cookie"] = self.cookie
        if method == "POST":
            h["X-Wizard"] = "1"
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        h = {k: v for k, v in h.items() if v is not None}
        data = json.dumps(body if body is not None else {}).encode() if method == "POST" else None
        conn.request(method, path, body=data, headers=h)
        res = conn.getresponse()
        raw = res.read()
        conn.close()
        try:
            payload = json.loads(raw)
        except ValueError:
            payload = raw.decode("utf-8", "replace")
        return res.status, payload, dict(res.getheaders())

    def stop(self):
        self.proc.terminate()
        self.proc.wait(timeout=10)
        shutil.rmtree(self.tmp, ignore_errors=True)


def read(path):
    with open(path) as f:
        return f.read()


def read_json(path):
    with open(path) as f:
        return json.load(f)


def wait_for(fn, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        v = fn()
        if v:
            return v
        time.sleep(0.1)
    return None
