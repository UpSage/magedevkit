"""The dashboard's Provision workflow (ops.WORKFLOWS["provision"],
ops.start_workflow): it takes only an environment the instance's kit lists,
and nothing else reaches the wizard's command line. Fast — no wizard runs;
every case here is refused before anything starts."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers
import envs
import ops


class ProvisionWorkflow(unittest.TestCase):
    def setUp(self):
        self.d = os.path.realpath(tempfile.mkdtemp(prefix="ops-provision-"))
        self.inst = os.path.join(self.d, "shop")
        helpers.write(os.path.join(self.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % helpers.REPO)
        helpers.write(os.path.join(self.inst, ".env"), "APP_NAME=shop\nAPP_DOMAIN=shop.test\n")
        self.mdir = os.path.join(self.inst, "magento")
        helpers.write(os.path.join(self.mdir, "deploy", "pipeline.json"), json.dumps(
            {"templateVersion": 3, "files": {}, "targets": {"dev": {"app": "shop"}, "staging": {"app": "shop"}}}))
        subprocess.run(["git", "init", "-q", self.mdir], check=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def start(self, body):
        with self.assertRaises(ops.OpError) as err:
            ops.start_workflow(envs.Environment(self.inst), "provision", [], body)
        return err.exception

    def test_is_a_workflow_that_needs_the_repo_and_a_target(self):
        spec = ops.WORKFLOWS["provision"]
        self.assertTrue(spec["requires_env"])
        self.assertTrue(spec["requires_repo"])
        self.assertEqual(spec["arg"], "target")
        self.assertRegex("provision-20261014-120000.log", ops.RUN_LOG_RE)

    def test_only_the_kits_environments(self):
        for body in ({}, {"target": None}, {"target": "prod"}, {"target": "dev; rm -rf /"}, {"target": "../dev"},
                     {"target": ["dev"]}, {"target": "--destroy"}):
            e = self.start(body)
            self.assertEqual(e.code, 400, body)
            self.assertIn("dev, staging", e.detail or "")

    def test_no_kit_no_targets(self):
        os.remove(os.path.join(self.mdir, "deploy", "pipeline.json"))
        e = self.start({"target": "dev"})
        self.assertEqual(e.code, 400)
        self.assertIn("none", e.detail)


if __name__ == "__main__":
    unittest.main()
