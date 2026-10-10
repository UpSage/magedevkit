"""bin/local/magento-module — turning modules on or off — with fake
bin/magento, bin/platform and bin/permissions/* that record their calls.
With --build it runs inside bin/local/magento-build (--module)."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers

RECORD = """#!/bin/bash
echo "%s${*:+ $*}" >> "$CALLS"
[ -n "${FAIL_ON:-}" ] && [ "$*" = "$FAIL_ON" ] && exit 1
exit 0
"""
COMPOSER = """#!/bin/bash
echo "composer${*:+ $*}" >> "$CALLS"
[ -n "${FAIL_ON:-}" ] && [ "$*" = "$FAIL_ON" ] && { echo "composer: conflict" >&2; exit 2; }
case "$1" in
  outdated) cat "$OUTDATED" ;;
  require) [ -n "${DRY:-}" ] || echo changed >> "$MAGENTO_DIR_/composer.lock" ;;
esac
exit 0
"""
PLATFORM = """#!/bin/bash
if [ "$*" = "ps --status running --services" ]; then
  [ -n "${PHP_DOWN:-}" ] || echo php
fi
exit 0
"""
BUILD = ["magento maintenance:enable", "magento setup:upgrade", "magento setup:di:compile",
         "magento setup:static-content:deploy -f", "magento cache:flush", "magento maintenance:disable"]


class MagentoModule(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="mmodule-"))
        self.repo = os.path.join(self.tmp, "repo")
        helpers.copy_scripts(self.repo, "bin/local/magento-module", "bin/local/magento-build")
        helpers.write(os.path.join(self.repo, "bin", "magento"), RECORD % "magento", 0o755)
        helpers.write(os.path.join(self.repo, "bin", "platform"), PLATFORM, 0o755)
        helpers.write(os.path.join(self.repo, "bin", "composer"), COMPOSER, 0o755)
        for p in ("unlock", "lock"):
            helpers.write(os.path.join(self.repo, "bin", "permissions", p), RECORD % ("permissions " + p), 0o755)
        self.inst = os.path.join(self.tmp, "shop")
        helpers.write(os.path.join(self.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % self.repo)
        self.mode("developer")
        self.md = os.path.join(self.inst, "magento")
        helpers.write(os.path.join(self.md, "composer.lock"), "{}\n")
        helpers.write(os.path.join(self.md, "composer.json"), '{"require": {}}\n')
        self.calls = os.path.join(self.tmp, "calls.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def mode(self, mode):
        helpers.write(os.path.join(self.inst, ".env"), "APP_NAME=shop\nAPP_DOMAIN=shop.test\nMAGENTO_MODE=%s\n" % mode)

    def run_module(self, *args, **env):
        e = dict(os.environ, PLATFORM_INSTANCE_DIR=self.inst, CALLS=self.calls, MAGENTO_DIR_=self.md, **env)
        for k in ("WIZARD_LOCK_HELD", "SETUP_UI", "UI_FLOW"):
            e.pop(k, None)
        p = subprocess.run([os.path.join(self.repo, "bin", "local", "magento-module")] + list(args), env=e,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        calls = helpers.read(self.calls).splitlines() if os.path.exists(self.calls) else []
        return p, calls

    def test_without_build(self):
        p, calls = self.run_module("disable", "Amasty_Base", "dotdigitalgroup_Email")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["magento module:disable --clear-static-content Amasty_Base dotdigitalgroup_Email"])
        self.assertIn("Build Magento before using the store", p.stdout)

    def test_with_build(self):
        p, calls = self.run_module("enable", "--build", "Amasty_Base")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["magento module:enable --clear-static-content Amasty_Base"] + BUILD)

    def test_production_builds_with_the_code_unlocked(self):
        self.mode("production")
        p, calls = self.run_module("enable", "Amasty_Base")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls, [])
        self.assertIn("add --build", p.stdout + p.stderr)
        p, calls = self.run_module("enable", "--build", "Amasty_Base")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["permissions unlock", "magento module:enable --clear-static-content Amasty_Base"]
                         + BUILD + ["permissions lock"])

    def test_refused_change_stops_before_the_build(self):
        p, calls = self.run_module("disable", "--build", "Amasty_Base",
                                   FAIL_ON="module:disable --clear-static-content Amasty_Base")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("magento maintenance:enable", calls)
        self.assertIn("stopped at: module:disable Amasty_Base", p.stdout)

    def test_check_updates_saves_the_result(self):
        out = os.path.join(self.tmp, "outdated.json")
        helpers.write(out, json.dumps({"installed": [
            {"name": "amasty/base", "version": "1.18.0", "latest": "1.19.2", "latest-status": "semver-safe-update", "abandoned": False}]}))
        p, calls = self.run_module("check-updates", OUTDATED=out)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["composer outdated --direct --all --format=json --no-interaction"])
        saved = json.loads(helpers.read(os.path.join(self.inst, "logs", "module-updates.json")))
        self.assertEqual(saved["exitCode"], 0)
        self.assertEqual(saved["packages"]["amasty/base"]["latestStatus"], "semver-safe-update")
        self.assertIsNone(saved["error"])
        # A failed check is saved as failed, with composer's message.
        p, _ = self.run_module("check-updates", OUTDATED=out, FAIL_ON="outdated --direct --all --format=json --no-interaction")
        self.assertNotEqual(p.returncode, 0)
        saved = json.loads(helpers.read(os.path.join(self.inst, "logs", "module-updates.json")))
        self.assertIsNone(saved["packages"])
        self.assertIn("conflict", saved["error"])

    def test_update_dry_run_changes_nothing(self):
        lock = helpers.read(os.path.join(self.md, "composer.lock"))
        p, calls = self.run_module("update", "--dry-run", "amasty/base:^1.17", "amasty/shipping:^3.0.1", DRY="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["composer require --dry-run --update-with-dependencies --no-interaction amasty/base:^1.17 amasty/shipping:^3.0.1"])
        self.assertEqual(helpers.read(os.path.join(self.md, "composer.lock")), lock)

    def test_update_then_build(self):
        p, calls = self.run_module("update", "amasty/base:^1.17")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["composer require --update-with-dependencies --no-interaction amasty/base:^1.17"] + BUILD)
        os.remove(self.calls)
        p, calls = self.run_module("update", "--no-build", "amasty/base:^1.17")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls, ["composer require --update-with-dependencies --no-interaction amasty/base:^1.17",
                                 "magento maintenance:enable", "magento setup:upgrade", "magento cache:flush",
                                 "magento maintenance:disable"])

    def test_failed_update_puts_composer_files_back(self):
        lock = helpers.read(os.path.join(self.md, "composer.lock"))
        p, calls = self.run_module("update", "amasty/base:^1.17",
                                   FAIL_ON="require --update-with-dependencies --no-interaction amasty/base:^1.17")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls, ["composer require --update-with-dependencies --no-interaction amasty/base:^1.17",
                                 "composer install --no-interaction"])
        self.assertEqual(helpers.read(os.path.join(self.md, "composer.lock")), lock)
        # Composer fine, build failed: the new composer.lock is kept.
        os.remove(self.calls)
        p, calls = self.run_module("update", "amasty/base:^1.17", FAIL_ON="setup:di:compile")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("composer install --no-interaction", calls)
        self.assertIn("changed", helpers.read(os.path.join(self.md, "composer.lock")))
        self.assertIn("composer.lock is kept", p.stdout)

    def test_update_in_production(self):
        self.mode("production")
        p, calls = self.run_module("update", "--no-build", "amasty/base:^1.17")
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(calls, [])
        p, calls = self.run_module("update", "--dry-run", "amasty/base:^1.17", DRY="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(calls[0], "permissions unlock")
        self.assertEqual(calls[-1], "permissions lock")

    def test_refusals(self):
        for args in ([], ["remove", "A_B"], ["enable"], ["enable", "--build"], ["enable", "a/b"], ["enable", "A_B;x"],
                     ["enable", "--force", "A_B"], ["update"], ["update", "amasty/base"], ["update", "amasty/base:^1;rm -rf /"],
                     ["update", "--dry-run", "Amasty/Base:^1"], ["check-updates", "x"]):
            p, calls = self.run_module(*args)
            self.assertNotEqual(p.returncode, 0, args)
            self.assertEqual(calls, [], args)
        p, calls = self.run_module("enable", "Amasty_Base", PHP_DOWN="1")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("isn't running", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
