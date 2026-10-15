"""Out-of-date compiled code (bin/lib/generated.sh) and what the bin/magento
wrapper does about it: a module removed since the last setup:di:compile
otherwise makes every bin/magento command die with 'Class … not found'."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

import helpers

LIB = os.path.join(helpers.REPO, "bin", "lib")

RECORD = """#!/bin/bash
echo "%s $*" >> "$CALLS"
exit 0
"""


class Shop:
    """An instance folder whose magento/ was compiled an hour ago."""

    def __init__(self, tmp, mode="developer"):
        self.inst = os.path.join(tmp, "shop")
        self.md = os.path.join(self.inst, "magento")
        helpers.write(os.path.join(self.inst, ".env"), "APP_NAME=shop\nAPP_DOMAIN=shop.test\nMAGENTO_MODE=%s\n" % mode)
        for rel in ("app/etc/config.php", "composer.lock", "app/code/Acme/Forms/registration.php",
                    "app/code/Acme/Forms/etc/di.xml", "app/code/Acme/Forms/etc/frontend/di.xml",
                    "generated/code/Acme/Forms/Model/Thing/Interceptor.php", "generated/metadata/global.php"):
            helpers.write(os.path.join(self.md, rel), "x\n")
        self.age(3600)
        self.touch("generated/metadata/global.php", 1800)

    def path(self, rel):
        return os.path.join(self.md, rel)

    def age(self, seconds):
        t = time.time() - seconds
        for root, dirs, files in os.walk(self.md):
            for n in dirs + files:
                os.utime(os.path.join(root, n), (t, t))
        os.utime(self.md, (t, t))

    def touch(self, rel, ago=0):
        t = time.time() - ago
        os.utime(self.path(rel), (t, t))


def stale(md):
    p = subprocess.run(["bash", "-c", 'source "%s/generated.sh"; generated_stale "$1"' % LIB, "stale", md],
                       capture_output=True, text=True)
    return p.returncode == 0, p.stdout.strip()


class GeneratedStale(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gen-stale-"))
        self.shop = Shop(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fresh_compile_is_not_stale(self):
        self.assertEqual(stale(self.shop.md), (False, ""))

    def test_no_compiled_di_is_not_stale(self):
        os.remove(self.shop.path("generated/metadata/global.php"))
        self.shop.touch("app/etc/config.php")
        self.assertFalse(stale(self.shop.md)[0])

    def test_config_php_or_composer_lock_changed(self):
        self.shop.touch("app/etc/config.php")
        self.assertEqual(stale(self.shop.md), (True, "app/etc/config.php changed since the last compile"))
        self.shop.touch("app/etc/config.php", 3600)
        self.shop.touch("composer.lock")
        self.assertEqual(stale(self.shop.md), (True, "composer.lock changed since the last compile"))

    def test_module_folder_removed(self):
        # A module folder deleted; its interceptors stay behind.
        shutil.rmtree(self.shop.path("app/code/Acme/Forms"))
        ok, reason = stale(self.shop.md)
        self.assertTrue(ok)
        self.assertIn("app/code/Acme", reason)

    def test_vendor_folder_removed(self):
        shutil.rmtree(self.shop.path("app/code/Acme"))
        self.assertEqual(stale(self.shop.md), (True, "a module was added or removed in app/code since the last compile"))

    def test_di_xml_or_registration_changed(self):
        for rel in ("app/code/Acme/Forms/etc/frontend/di.xml", "app/code/Acme/Forms/etc/di.xml",
                    "app/code/Acme/Forms/registration.php"):
            self.shop.touch(rel)
            self.assertEqual(stale(self.shop.md), (True, rel + " changed since the last compile"))
            self.shop.touch(rel, 3600)

    @unittest.skipIf(os.getuid() == 0, "root reads any folder")
    def test_a_failing_check_is_not_up_to_date(self):
        locked = self.shop.path("app/code/Acme")
        os.chmod(locked, 0)
        try:
            self.assertEqual(subprocess.run(["bash", "-c", 'source "%s/generated.sh"; generated_stale "$1"' % LIB,
                                             "stale", self.shop.md], capture_output=True).returncode, 2)
        finally:
            os.chmod(locked, 0o755)

    def test_ordinary_code_edits_are_not_stale(self):
        helpers.write(self.shop.path("app/code/Acme/Forms/Model/Thing.php"), "<?php\n")
        self.shop.touch("app/code/Acme/Forms")  # its new Model/ folder
        self.assertFalse(stale(self.shop.md)[0])


class Wrapper(unittest.TestCase):
    """The real bin/magento with fake bin/platform and docker."""

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="gen-wrap-"))
        self.repo = os.path.join(self.tmp, "repo")
        helpers.copy_scripts(self.repo, "bin/magento")
        helpers.write(os.path.join(self.repo, "bin", "platform"), RECORD % "platform", 0o755)
        self.bin = os.path.join(self.tmp, "fakebin")
        helpers.write(os.path.join(self.bin, "docker"), RECORD % "docker", 0o755)
        self.calls = os.path.join(self.tmp, "calls.log")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def magento(self, shop, *args, **env):
        helpers.write(os.path.join(shop.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % self.repo)
        e = {k: v for k, v in os.environ.items() if k != "MDK_SKIP_GENERATED_CHECK"}
        e.update(PLATFORM_INSTANCE_DIR=shop.inst, CALLS=self.calls, PATH=self.bin + os.pathsep + os.environ["PATH"], **env)
        p = subprocess.run([os.path.join(self.repo, "bin", "magento")] + list(args), env=e,
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
        try:
            with open(self.calls) as f:
                calls = [l.strip() for l in f]
            os.remove(self.calls)
        except OSError:
            calls = []
        return p, calls

    def test_fresh_compile_runs_the_command_untouched(self):
        p, calls = self.magento(Shop(self.tmp), "cache:flush")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("docker compose") and calls[0].endswith("php bin/magento --no-interaction cache:flush"))
        self.assertEqual(p.stderr, "")

    def test_developer_and_default_mode_clear_then_run(self):
        user = "%d:%d" % (os.getuid(), os.getgid())
        for mode in ("developer", "default"):
            shop = Shop(os.path.join(self.tmp, mode), mode)
            shutil.rmtree(shop.path("app/code/Acme/Forms"))
            p, calls = self.magento(shop, "cache:flush")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(calls[:2], ["platform exec -T --user %s php rm -rf generated/code generated/metadata" % user,
                                         "platform exec -T --user %s php mkdir -p generated/code generated/metadata" % user])
            self.assertTrue(calls[2].endswith("bin/magento --no-interaction cache:flush"), calls)
            self.assertIn("Compiled code was out of date (a module was added or removed in app/code/Acme", p.stderr)

    def test_production_warns_and_leaves_compiled_code(self):
        shop = Shop(self.tmp, "production")
        shop.touch("app/etc/config.php")
        p, calls = self.magento(shop, "cache:flush")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("docker compose"))
        self.assertIn("Compiled code looks out of date (app/etc/config.php changed", p.stderr)
        self.assertIn("mdk -i shop build", p.stderr)

    @unittest.skipIf(os.getuid() == 0, "root reads any folder")
    def test_a_failing_check_clears_nothing(self):
        shop = Shop(self.tmp)
        locked = shop.path("app/code/Acme")
        os.chmod(locked, 0)
        try:
            p, calls = self.magento(shop, "cache:flush")
        finally:
            os.chmod(locked, 0o755)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].startswith("docker compose"))
        self.assertIn("Couldn't check whether the compiled code is up to date", p.stderr)

    def test_skip_variable(self):
        shop = Shop(self.tmp)
        shop.touch("app/etc/config.php")
        p, calls = self.magento(shop, "cache:flush", MDK_SKIP_GENERATED_CHECK="1")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(len(calls), 1)
        self.assertEqual(p.stderr, "")


if __name__ == "__main__":
    unittest.main()
