"""bin/local/magento-build — the Magento-only build — with a temp copy of the
scripts it needs and fake bin/magento, bin/composer, bin/platform and
bin/permissions/* that record what they're asked to do."""

import os
import shutil
import subprocess
import tempfile
import unittest

import helpers

RECORD = """#!/bin/bash
echo "%s $*" >> "$CALLS"
for f in ${FAIL_ON:-}; do [ "$*" = "$f" ] && exit 1; done
exit 0
"""
PLATFORM = """#!/bin/bash
echo "platform $*" >> "$CALLS"
if [ "$*" = "ps --status running --services" ]; then
  [ -n "${PHP_DOWN:-}" ] || echo php
fi
exit 0
"""


class MagentoBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mbuild-"))
        self.repo = os.path.join(self.tmp, "repo")
        for rel in ("bin/local/magento-build", "bin/lib/bootstrap.sh", "bin/lib/util.sh", "bin/lib/ui.sh", "bin/lib/env.sh", "bin/lib/paths.sh"):
            dst = os.path.join(self.repo, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(os.path.join(helpers.REPO, rel), dst)
        helpers.write(os.path.join(self.repo, "bin", "magento"), RECORD % "magento", 0o755)
        helpers.write(os.path.join(self.repo, "bin", "composer"), RECORD % "composer", 0o755)
        helpers.write(os.path.join(self.repo, "bin", "platform"), PLATFORM, 0o755)
        for p in ("unlock", "lock"):
            helpers.write(os.path.join(self.repo, "bin", "permissions", p), RECORD % ("permissions " + p), 0o755)
        self.inst = os.path.join(self.tmp, "shop")
        helpers.write(os.path.join(self.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % self.repo)
        helpers.write(os.path.join(self.inst, ".env"), "APP_NAME=shop\nAPP_DOMAIN=shop.test\nMAGENTO_MODE=developer\n")
        helpers.write(os.path.join(self.inst, "magento", "composer.lock"), "{}\n")
        self.calls = os.path.join(self.tmp, "calls.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, *args, **env):
        e = dict(os.environ, PLATFORM_INSTANCE_DIR=self.inst, CALLS=self.calls, **env)
        for k in ("WIZARD_LOCK_HELD", "SETUP_UI", "UI_FLOW"):
            e.pop(k, None)
        p = subprocess.run([os.path.join(self.repo, "bin", "local", "magento-build")] + list(args), env=e,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        try:
            with open(self.calls) as f:
                calls = [l.strip() for l in f if not l.startswith("platform ")]
        except OSError:
            calls = []
        return p, calls

    def test_order(self):
        p, calls = self.build()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["magento maintenance:enable", "magento setup:upgrade", "magento setup:di:compile",
                                 "magento setup:static-content:deploy -f", "magento cache:flush",
                                 "magento maintenance:disable"])

    def test_composer_first_and_production_lock(self):
        with open(os.path.join(self.inst, ".env"), "a") as f:
            f.write("MAGENTO_MODE=production\n")
        p, calls = self.build("--composer", "update", "amasty/shipping-rules")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls[:3], ["permissions unlock", "composer update --no-interaction amasty/shipping-rules",
                                     "magento maintenance:enable"])
        self.assertEqual(calls[-1], "permissions lock")

    def test_failure_turns_maintenance_off(self):
        p, calls = self.build(FAIL_ON="setup:di:compile")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("magento setup:static-content:deploy -f", calls)
        self.assertEqual(calls[-1], "magento maintenance:disable")
        self.assertIn("stopped at: setup:di:compile", p.stdout)

    def test_refusals(self):
        p, calls = self.build(PHP_DOWN="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls, [])
        self.assertIn("isn't running", p.stdout + p.stderr)
        for args in (["--composer", "remove"], ["--composer", "update", "a;b"], ["--composer", "install", "a/b"],
                     ["--composer"]):
            p, _ = self.build(*args)
            self.assertNotEqual(p.returncode, 0, args)


if __name__ == "__main__":
    unittest.main()
