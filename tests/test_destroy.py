"""Destroying an instance from the dashboard (ops.destroy + bin/local/destroy),
with this repo's real scripts and a fake `docker` that records its calls."""

import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers

FAKE_DOCKER = """#!/bin/sh
echo "docker $*" >> "%s"
case "$*" in
  "volume ls -q --filter name=^gone-db-data$") echo gone-db-data ;;
esac
exit 0
"""


class Destroy(unittest.TestCase):
    def setUp(self):
        self.root = os.path.realpath(tempfile.mkdtemp(prefix="destroy-"))
        self.calls = os.path.join(self.root, "docker-calls.log")
        self.fakebin = os.path.join(self.root, "fakebin")
        helpers.write(os.path.join(self.fakebin, "docker"), FAKE_DOCKER % self.calls, 0o755)
        self.legacy = helpers.make_checkout(self.root, "Legacy", "legacy", "legacy.test")
        self.inst = self.make_instance("gone")
        self.dash = helpers.Dashboard(helpers.REPO, self.root, self.fakebin)

    def tearDown(self):
        self.dash.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    def make_instance(self, name):
        d = os.path.join(self.root, name)
        helpers.write(os.path.join(d, ".platform-instance"), "PLATFORM_REPO=%s\n" % helpers.REPO)
        helpers.write(os.path.join(d, ".env"), "APP_NAME=%s\nAPP_DOMAIN=%s.test\nPHP_VERSION=8.4\nMAGENTO_MODE=developer\n"
                      % (name, name))
        for sub in ("logs", "db", "nginx", "certs", "magento"):
            os.makedirs(os.path.join(d, sub), exist_ok=True)
        helpers.write(os.path.join(d, "db", "backup.sql.gz"), "x" * 1000)
        return d

    def post(self, env, body):
        return self.dash.request("POST", "/api/environments/%s/destroy" % env, body)

    def wait(self, op):
        def done():
            code, st, _ = self.dash.request("GET", "/api/destroyed/" + op)
            return st if code == 200 and st["operation"]["status"] not in ("starting", "running") else None
        st = helpers.wait_for(done, timeout=60)
        self.assertIsNotNone(st, "destroy didn't finish")
        return st

    def test_check_and_refusals(self):
        code, c, _ = self.dash.request("GET", "/api/environments/gone/destroy-check")
        self.assertEqual(code, 200, c)
        self.assertEqual(c["folder"], self.inst)
        self.assertEqual(c["backupsBytes"], 1000)
        self.assertFalse(c["risky"])
        self.assertEqual(self.dash.request("GET", "/api/environments/legacy/destroy-check")[0], 400)
        self.assertEqual(self.post("legacy", {"confirm": "legacy"})[0], 400)
        self.assertEqual(self.post("gone", {"confirm": "wrong"})[0], 400)
        self.assertEqual(self.dash.request("GET", "/api/destroyed/../etc")[0], 404)

        fd = os.open(os.path.join(self.inst, ".wizard.lock"), os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.post("gone", {"confirm": "gone"})[0], 409)
        finally:
            os.close(fd)
        self.assertTrue(os.path.isdir(self.inst))

    def test_unpushed_work_needs_force(self):
        m = os.path.join(self.inst, "magento")
        subprocess.run(["git", "init", "-q", m], check=True)
        helpers.write(os.path.join(m, "a.txt"), "x\n")
        subprocess.run(["git", "-C", m, "add", "-A"], check=True)
        subprocess.run(["git", "-C", m, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "local only"], check=True)
        code, c, _ = self.dash.request("GET", "/api/environments/gone/destroy-check")
        self.assertTrue(c["risky"])
        self.assertEqual(c["unpushed"], 1)
        self.assertEqual(self.post("gone", {"confirm": "gone"})[0], 409)
        code, res, _ = self.post("gone", {"confirm": "gone", "force": True})
        self.assertEqual(code, 202, res)
        self.assertEqual(self.wait(res["operation"])["operation"]["status"], "done")
        self.assertFalse(os.path.exists(self.inst))

    def test_destroys_stack_volumes_and_folder(self):
        code, res, _ = self.post("gone", {"confirm": "gone"})
        self.assertEqual(code, 202, res)
        st = self.wait(res["operation"])
        self.assertEqual(st["operation"]["status"], "done", st["text"])
        self.assertIn("destroyed", st["text"])
        self.assertFalse(os.path.exists(self.inst))
        self.assertTrue(os.path.isdir(self.legacy))
        with open(self.calls) as f:
            calls = f.read()
        self.assertIn("down -v --remove-orphans", calls)
        self.assertIn("volume rm gone-db-data", calls)
        # The record outlives the folder (dashboard cache).
        self.assertTrue(os.path.isfile(os.path.join(self.dash.tmp, "destroyed", res["operation"] + ".json")))
        code, o, _ = self.dash.request("GET", "/api/environments")
        self.assertNotIn("gone", [e["id"] for e in o["environments"]])


if __name__ == "__main__":
    unittest.main()
