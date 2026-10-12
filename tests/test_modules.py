"""bin/lib/web-wizard/modules.py — the Modules tab's data: on/off from
app/etc/config.php, module.xml from vendor/ and app/code/, packages and
versions from composer.lock, and the checks on a module action's names."""

import json
import os
import shutil
import tempfile
import unittest

import helpers
import modules
import webcore

CONFIG = """<?php
return [
    'modules' => [
        'Magento_Store' => 1,
        'Magento_Catalog' => 1,
        'PayPal_Braintree' => 1,
        'Amasty_Base' => 1,
        'Amasty_Shipping' => 0,
        'Acme_Local' => 1,
        'dotdigitalgroup_Email' => 0,
    ],
    'scopes' => [
        'websites' => [],
    ],
];
"""
MODULE_XML = """<?xml version="1.0"?>
<config xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:noNamespaceSchemaLocation="urn:magento:framework:Module/etc/module.xsd">
    <module name="%s"%s>
        <sequence>%s</sequence>
    </module>
</config>
"""
LOCK = {"packages": [
    {"name": "magento/product-community-edition", "version": "2.4.8-p2",
     "require": {"magento/module-catalog": "104.0.8", "paypal/module-braintree": "4.7.0"}},
    {"name": "magento/module-catalog", "version": "104.0.8"},
    {"name": "paypal/module-braintree", "version": "4.7.0"},
    {"name": "amasty/base", "version": "1.18.0"},
    {"name": "amasty/shipping", "version": "2.3.1"},
]}


class Modules(unittest.TestCase):
    def setUp(self):
        self.md = tempfile.mkdtemp(prefix="modules-")
        modules.envs.CACHE.data.clear()
        self.write("app/etc/config.php", CONFIG)
        self.module("vendor/magento/module-catalog", "Magento_Catalog", "magento/module-catalog", ["Magento_Store"])
        self.module("vendor/magento/module-store", "Magento_Store", "magento/module-store")
        self.module("vendor/paypal/module-braintree", "PayPal_Braintree", "paypal/module-braintree", src=True)
        self.module("vendor/amasty/base", "Amasty_Base", "amasty/base")
        self.module("vendor/amasty/shipping", "Amasty_Shipping", "amasty/shipping", ["Amasty_Base"])
        self.module("app/code/Acme/Local", "Acme_Local", None, ["Amasty_Base", "Magento_Catalog"], version="1.0.2")
        self.write("composer.lock", json.dumps(LOCK))

    def tearDown(self):
        shutil.rmtree(self.md, ignore_errors=True)

    def write(self, rel, text):
        helpers.write(os.path.join(self.md, rel), text)

    def module(self, rel, name, package, sequence=(), src=False, version=None):
        seq = "".join('<module name="%s"/>' % s for s in sequence)
        attr = ' setup_version="%s"' % version if version else ""
        self.write(os.path.join(rel, "src" if src else "", "etc", "module.xml"), MODULE_XML % (name, attr, seq))
        if package:
            self.write(os.path.join(rel, "composer.json"), json.dumps({"name": package}))

    def env(self):
        e = type("E", (), {"secret_values": lambda self: []})()
        e.magento_dir = self.md
        e.platform_dir = os.path.join(self.md, "instance")
        return e

    def check(self, packages, rc=0, error=None):
        """A saved composer outdated result (bin/local/magento-module check-updates)."""
        helpers.write(os.path.join(self.md, "instance", "logs", "module-updates.json"), json.dumps(
            {"checkedAt": "2026-10-10T10:00:00Z", "exitCode": rc, "packages": packages, "error": error}))
        modules.envs.CACHE.data.clear()

    def by_name(self):
        return {m["name"]: m for m in modules.listing(self.env())["modules"]}

    def test_states(self):
        states, error = modules.module_states(self.md)
        self.assertIsNone(error)
        self.assertEqual(states["Amasty_Base"], True)
        self.assertEqual(states["Amasty_Shipping"], False)
        self.assertIn("dotdigitalgroup_Email", states)  # a lowercase vendor is a real Magento module
        self.assertNotIn("websites", states)

    def test_unreadable_config_is_reported_not_guessed(self):
        self.write("app/etc/config.php", "<?php return ['modules' => [\n'Amasty_Base' => 1,\n'X_Y' => true,\n]];")
        states, error = modules.module_states(self.md)
        self.assertEqual(states, {})
        self.assertIn("can't read", error)
        os.remove(os.path.join(self.md, "app", "etc", "config.php"))
        self.assertIn("isn't installed yet", modules.module_states(self.md)[1])

    def test_listing(self):
        m = self.by_name()
        self.assertEqual(m["Amasty_Base"]["package"], "amasty/base")
        self.assertEqual(m["Amasty_Base"]["version"], "1.18.0")
        self.assertEqual(m["Amasty_Base"]["dependents"], ["Acme_Local", "Amasty_Shipping"])
        self.assertFalse(m["Amasty_Base"]["core"])
        self.assertEqual(m["Amasty_Shipping"]["enabled"], False)
        self.assertEqual(m["Amasty_Shipping"]["sequence"], ["Amasty_Base"])
        # Core: required by the product metapackage, or Magento's own.
        self.assertTrue(m["PayPal_Braintree"]["core"])
        self.assertEqual(m["PayPal_Braintree"]["package"], "paypal/module-braintree")  # module.xml under src/
        self.assertTrue(m["Magento_Store"]["core"])
        self.assertEqual(m["Acme_Local"]["source"], "app/code")
        self.assertIsNone(m["Acme_Local"]["package"])
        self.assertEqual(m["Acme_Local"]["setupVersion"], "1.0.2")
        # In config.php but no module.xml found.
        self.assertEqual(m["dotdigitalgroup_Email"]["source"], "unknown")

    def test_package_with_several_modules(self):
        for sub in ("src/Search", "src/SearchMysql"):
            name = "Mirasvit_" + sub.split("/")[1]
            self.write("vendor/mirasvit/module-search-ultimate/%s/etc/module.xml" % sub, MODULE_XML % (name, "", ""))
        self.write("vendor/avalara/avatax-magento/BaseProvider/etc/module.xml", MODULE_XML % ("Avalara_BaseProvider", "", ""))
        for rel, pkg in (("vendor/mirasvit/module-search-ultimate", "mirasvit/module-search-ultimate"),
                         ("vendor/avalara/avatax-magento", "avalara/avatax-magento")):
            self.write(rel + "/composer.json", json.dumps({"name": pkg}))
        m = self.by_name()
        self.assertEqual(m["Mirasvit_Search"]["package"], "mirasvit/module-search-ultimate")
        self.assertEqual(m["Mirasvit_SearchMysql"]["package"], "mirasvit/module-search-ultimate")
        self.assertEqual(m["Avalara_BaseProvider"]["package"], "avalara/avatax-magento")

    def test_new_module_not_registered_yet(self):
        self.module("vendor/acme/new", "Acme_New", "acme/new")
        self.assertIsNone(self.by_name()["Acme_New"]["enabled"])

    def test_installed_json_fallback(self):
        os.remove(os.path.join(self.md, "composer.lock"))
        self.write("vendor/composer/installed.json", json.dumps([{"name": "amasty/base", "version": "1.17.0"}]))
        self.assertEqual(self.by_name()["Amasty_Base"]["version"], "1.17.0")

    def test_update_statuses(self):
        self.write("composer.json", json.dumps({"require": {"amasty/base": "^1.17", "amasty/shipping": "^2.0",
                                                            "magento/product-community-edition": "2.4.8-p2"}}))
        m = self.by_name()
        self.assertEqual(m["Amasty_Base"]["update"]["status"], "unable-to-check")
        self.assertIn("No update check", m["Amasty_Base"]["update"]["reason"])
        self.check({"amasty/base": {"version": "1.18.0", "latest": "1.19.2", "latestStatus": "semver-safe-update"},
                    "amasty/shipping": {"version": "2.3.1", "latest": "2.3.1", "latestStatus": "up-to-date"}})
        m = self.by_name()
        self.assertEqual(m["Amasty_Base"]["update"], {"status": "update-available", "latest": "1.19.2", "major": False, "note": None})
        self.assertEqual(m["Amasty_Shipping"]["update"]["status"], "up-to-date")
        self.assertEqual(m["Acme_Local"]["update"]["status"], "not-updatable")
        self.assertEqual(m["Magento_Catalog"]["update"]["status"], "managed-by-version-wizard")
        self.assertEqual(m["PayPal_Braintree"]["update"]["status"], "managed-by-version-wizard")  # required by the metapackage
        # Installed since the check: not trusted any more.
        self.check({"amasty/base": {"version": "1.17.0", "latest": "1.19.2", "latestStatus": "semver-safe-update"}})
        m = self.by_name()
        self.assertEqual(m["Amasty_Base"]["update"]["status"], "unable-to-check")
        self.assertEqual(m["Amasty_Shipping"]["update"]["status"], "unable-to-check")  # not in that check
        # A failed check says nothing; its error is shown (redacted).
        self.check(None, rc=1, error="Could not authenticate against repo.example.com")
        data = modules.listing(self.env())
        self.assertFalse(data["updates"]["ok"])
        self.assertIn("Could not authenticate", data["updates"]["error"])
        self.assertEqual({x["update"]["status"] for x in data["modules"] if x["name"].startswith("Amasty")}, {"unable-to-check"})

    def test_update_specs(self):
        self.write("composer.json", json.dumps({"require": {"amasty/base": "^1.17", "amasty/shipping": "~2.0",
                                                            "magento/module-catalog": "*"}}))
        self.check({"amasty/base": {"version": "1.18.0", "latest": "1.19.2", "latestStatus": "semver-safe-update"},
                    "amasty/shipping": {"version": "2.3.1", "latest": "v3.0.1", "latestStatus": "update-possible"},
                    "magento/module-catalog": {"version": "104.0.8", "latest": "104.1.0", "latestStatus": "semver-safe-update"}})
        e = self.env()
        # Minor/patch keeps the constraint; a major gets ^<latest>.
        self.assertEqual(modules.update_specs(e, {"packages": ["amasty/base", "amasty/shipping", "amasty/base"]}),
                         ["amasty/base:^1.17", "amasty/shipping:^3.0.1"])
        for bad in ({}, {"packages": []}, {"packages": "amasty/base"}, {"packages": ["Amasty/Base"]},
                    {"packages": ["amasty/base:^9"]}, {"packages": ["magento/module-catalog"]}, {"packages": ["acme/none"]},
                    {"packages": ["v/p%d" % i for i in range(101)]}):
            with self.assertRaises(webcore.OpError, msg=bad) as cm:
                modules.update_specs(e, bad)
            self.assertEqual(cm.exception.code, 400, bad)
        # Not a direct requirement any more: refused.
        self.write("composer.json", json.dumps({"require": {}}))
        modules.envs.CACHE.data.clear()
        with self.assertRaises(webcore.OpError):
            modules.update_specs(e, {"packages": ["amasty/base"]})

    def test_auth_json_lists_are_redacted(self):
        self.write("auth.json", json.dumps({"http-basic": {"repo.example.com": {"username": "pubkey0123456789",
                                                                                "password": "privkey0123456789"}},
                                            "bearer": {"x.example.com": ["listtoken0123456789"]}}))
        r = modules.Redactor(self.env())
        out = r("pubkey0123456789 privkey0123456789 listtoken0123456789")
        for secret in ("pubkey0123456789", "privkey0123456789", "listtoken0123456789"):
            self.assertNotIn(secret, out)

    def test_parse_modules(self):
        e = self.env()
        self.assertEqual(modules.parse_modules(e, {"modules": ["Amasty_Base", "Amasty_Base", "Acme_Local"]}),
                         ["Amasty_Base", "Acme_Local"])
        for bad in ({}, {"modules": []}, {"modules": "Amasty_Base"}, {"modules": ["amasty/base"]},
                    {"modules": ["A_B; rm"]}, {"modules": ["Not_There"]}, {"modules": ["X_Y%d" % i for i in range(101)]}):
            with self.assertRaises(webcore.OpError, msg=bad) as cm:
                modules.parse_modules(e, bad)
            self.assertEqual(cm.exception.code, 400, bad)


if __name__ == "__main__":
    unittest.main()
