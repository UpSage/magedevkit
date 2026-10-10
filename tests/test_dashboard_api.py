import fcntl
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

import helpers


def calls(pd):
    try:
        with open(os.path.join(pd, "calls.log")) as f:
            return f.read()
    except OSError:
        return ""


class DashboardApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(prefix="dash-root-")
        cls.a = helpers.make_checkout(cls.root, "ProjA", "stack-a", "a.test")
        cls.b = helpers.make_checkout(cls.root, "ProjB", "stack-b", "b.test")
        # An instance folder on a shared repo (bin/local/new-instance layout).
        cls.repo = helpers.make_repo(cls.root)
        cls.c = helpers.make_instance(cls.root, "stack-c", cls.repo)
        cls.fakebin = helpers.fake_docker_dir(cls.root, [
            helpers.container("1", "stack-a-platform", "php", os.path.join(cls.a, "compose")),
            helpers.container("2", "stack-b-platform", "php", os.path.join(cls.b, "compose"), state="exited"),
            helpers.container("3", "stack-c-platform", "php", os.path.join(cls.repo, "compose"), instance_dir=cls.c),
        ])
        # Planted variables that must never reach a checkout's scripts.
        cls.dash = helpers.Dashboard(cls.a, cls.root, cls.fakebin, {
            "APP_NAME": "evil", "COMPOSE_PROJECT_NAME": "evil-platform", "WIZARD_LOCK_HELD": "1"})

    @classmethod
    def tearDownClass(cls):
        cls.dash.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def wait_done(self, pd, env_id, op):
        def done():
            code, rec, _ = self.dash.request("GET", "/api/environments/%s/operations/%s" % (env_id, op))
            return rec if code == 200 and rec["status"] not in ("starting", "running") else None
        rec = helpers.wait_for(done)
        self.assertIsNotNone(rec, "operation didn't finish")
        return rec

    # -- auth / transport -------------------------------------------------------

    def test_requires_cookie(self):
        code, body, _ = self.dash.request("GET", "/api/environments", cookie=False)
        self.assertEqual(code, 403)
        code, _, _ = self.dash.request("GET", "/api/environments", headers={"Cookie": "dash_%d=wrong" % self.dash.port})
        self.assertEqual(code, 403)

    def test_wrong_host(self):
        code, body, _ = self.dash.request("GET", "/api/environments", host="evil.example:%d" % self.dash.port)
        self.assertEqual(code, 403)
        self.assertEqual(body["error"], "Forbidden host.")

    def test_post_needs_x_wizard(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-a/actions/cache_flush", headers={"X-Wizard": None})
        self.assertEqual(code, 403)
        self.assertEqual(calls(self.a).count("cache:flush"), 0)

    def test_token_login_sets_port_cookie(self):
        code, _, headers = self.dash.request("GET", "/?token=" + self.dash.token, cookie=False)
        self.assertEqual(code, 303)
        self.assertIn("dash_%d=" % self.dash.port, headers.get("Set-Cookie", ""))
        code, _, _ = self.dash.request("GET", "/?token=nope", cookie=False)
        self.assertEqual(code, 403)

    def test_security_headers(self):
        _, _, headers = self.dash.request("GET", "/api/environments")
        self.assertEqual(headers.get("X-Frame-Options"), "DENY")
        self.assertNotIn("https:", headers.get("Content-Security-Policy", ""))

    # -- discovery / input validation ------------------------------------------------

    def test_lists_both_stacks(self):
        code, body, _ = self.dash.request("GET", "/api/environments")
        self.assertEqual(code, 200)
        by_id = {e["id"]: e for e in body["environments"]}
        self.assertEqual(set(by_id), {"stack-a", "stack-b", "stack-c"})
        self.assertEqual(by_id["stack-c"]["layout"], "instance")
        self.assertEqual(by_id["stack-c"]["platformDir"], self.c)
        self.assertEqual(by_id["stack-c"]["codeDir"], self.repo)
        self.assertEqual(by_id["stack-c"]["docker"]["running"], 1)
        self.assertEqual(by_id["stack-c"]["mode"]["actual"], "production")
        self.assertEqual(body["warnings"], [])
        self.assertEqual(by_id["stack-a"]["docker"]["running"], 1)
        self.assertEqual(by_id["stack-b"]["docker"]["running"], 0)

    def test_bad_ids_and_actions(self):
        for path in ("/api/environments/..%2Fx", "/api/environments/a;rm%20-rf", "/api/environments/STACK-A",
                     "/api/environments/nope", "/api/environments/stack-a/operations/..%2F..%2Fx"):
            code, _, _ = self.dash.request("GET", path)
            self.assertEqual(code, 404, path)
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-a/actions/rm")[0], 404)
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-a/actions/restart", {"service": "php; rm -rf /"})[0], 400)
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-a/actions/mode_set", {"mode": "../x"})[0], 400)
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-a/workflows/evil")[0], 404)

    def test_log_sources_cant_escape(self):
        for src in ("file:../env/.env.local", "file:..%2F..%2Fenv%2F.env.local", "magento:../../app/etc/env",
                    "service:php;id", "op:../../x", "nope:x"):
            code, _, _ = self.dash.request("GET", "/api/environments/stack-a/logs?source=" + src)
            self.assertIn(code, (400, 404), src)

    def test_log_lines_must_be_a_number(self):
        code, _, _ = self.dash.request("GET", "/api/environments/stack-a/logs?source=magento:system&lines=abc")
        self.assertEqual(code, 400)

    def test_logs_are_redacted(self):
        code, body, _ = self.dash.request("GET", "/api/environments/stack-a/logs?source=magento:system")
        self.assertEqual(code, 200)
        self.assertNotIn("cryptkey0123456789abcdef", body["text"])   # env.php crypt key
        self.assertNotIn("hunter2hunter2", body["text"])             # URL credentials
        self.assertIn("plain line", body["text"])

    # -- actions ----------------------------------------------------------------------

    def test_action_runs_only_in_its_checkout_with_clean_env(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/restart", {"service": "php"})
        self.assertEqual(code, 202)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["status"], "done")
        log = calls(self.b)
        self.assertIn("bin/platform restart php", log)
        self.assertIn("cwd=%s" % self.b, log)
        # the planted values never arrive; WIZARD_LOCK_HELD=1 is set on purpose for a
        # locked action (the runner holds the lock for its child)
        self.assertIn("APP_NAME=unset COMPOSE_PROJECT_NAME=unset WIZARD_LOCK_HELD=1", log)
        self.assertNotIn("restart php", calls(self.a))
        # the operation log is redacted too
        code, out, _ = self.dash.request("GET", "/api/environments/stack-b/operations/%s/log" % body["operation"])
        self.assertNotIn("supersecretvalue123", out["text"])

    def test_instance_actions_run_repo_scripts_for_that_instance(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-c/actions/cache_flush")
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.c, "stack-c", body["operation"])
        self.assertEqual(rec["status"], "done")
        log = calls(self.repo)
        self.assertIn("%s/bin/magento cache:flush" % self.repo, log)
        self.assertIn("cwd=%s" % self.c, log)
        self.assertIn("PLATFORM_INSTANCE_DIR=%s MAGENTO_DIR=unset" % self.c, log)
        self.assertTrue(os.path.exists(os.path.join(self.c, "logs", "operations", body["operation"] + ".json")))
        # the legacy checkouts' own scripts weren't involved
        self.assertNotIn("cache:flush", calls(self.a) + calls(self.b))

    def test_instance_workflow_uses_repo_launcher(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-c/workflows/version")
        self.assertEqual(code, 202, body)
        self.assertEqual(body["url"], "http://127.0.0.1:4242/?token=fakeTOKEN_123")
        self.wait_done(self.c, "stack-c", body["operation"])

    def test_new_instance_validation(self):
        for name in ("Bad", "a.b", "", "x" * 41, None):
            code, _, _ = self.dash.request("POST", "/api/instances", {"name": name})
            self.assertEqual(code, 400, name)
        code, _, _ = self.dash.request("POST", "/api/instances", {"name": "stack-c"})
        self.assertEqual(code, 409)

    def test_read_only_action_gets_no_lock_flag(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-a/actions/mode_show")
        self.assertEqual(code, 202, body)
        self.wait_done(self.a, "stack-a", body["operation"])
        line = [l for l in calls(self.a).splitlines() if "WIZARD_LOCK_HELD" in l][-1]
        self.assertIn("WIZARD_LOCK_HELD=unset", line)

    def test_composer_update(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/composer_update", {})
        self.assertEqual(code, 202, body)
        self.wait_done(self.b, "stack-b", body["operation"])
        self.assertIn("bin/composer update --no-interaction\n", calls(self.b))
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/composer_update",
                                          {"packages": ["amasty/shipping-rules", "magento/module-*"]})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "composer update amasty/shipping-rules magento/module-*")
        self.assertIn("bin/composer update --no-interaction amasty/shipping-rules magento/module-*", calls(self.b))
        for bad in (["a;b"], ["../x"], ["vendor/pkg; rm -rf /"], ["Vendor/Pkg"], ["v/p%d" % i for i in range(21)], "x/y z"):
            code, _, _ = self.dash.request("POST", "/api/environments/stack-b/actions/composer_update", {"packages": bad})
            self.assertEqual(code, 400, bad)

    def test_magento_build(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/magento_build", {})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "Build Magento")
        self.assertIn("bin/local/magento-build \n", calls(self.b))
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/magento_build",
                                          {"composer": "update", "packages": ["amasty/shipping-rules"]})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "composer update amasty/shipping-rules + build")
        self.assertIn("bin/local/magento-build --composer update amasty/shipping-rules", calls(self.b))
        for bad in ({"composer": "remove"}, {"composer": "install", "packages": ["a/b"]}, {"composer": "update", "packages": ["a;b"]}):
            self.assertEqual(self.dash.request("POST", "/api/environments/stack-b/actions/magento_build", bad)[0], 400, bad)

    def test_modules(self):
        md = os.path.join(os.path.dirname(self.b), "magento")
        helpers.write(os.path.join(md, "app", "etc", "config.php"),
                      "<?php\nreturn [\n    'modules' => [\n        'Magento_Store' => 1,\n        'Amasty_Base' => 0,\n    ],\n];\n")
        code, body, _ = self.dash.request("GET", "/api/environments/stack-b/modules")
        self.assertEqual(code, 200, body)
        self.assertIsNone(body["error"])
        self.assertEqual({m["name"]: m["enabled"] for m in body["modules"]}, {"Amasty_Base": False, "Magento_Store": True})
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_enable", {"modules": ["Amasty_Base"]})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "Enable Amasty_Base + build")
        self.assertIn("bin/local/magento-module enable --build Amasty_Base\n", calls(self.b))
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_disable",
                                          {"modules": ["Amasty_Base", "Magento_Store"], "build": False})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "Disable 2 modules")
        self.assertIn("bin/local/magento-module disable Amasty_Base Magento_Store\n", calls(self.b))
        for bad in ({}, {"modules": ["Not_There"]}, {"modules": ["Amasty_Base; rm -rf /"]}, {"modules": "Amasty_Base"},
                    {"modules": ["Amasty_Base"], "build": "no"}, {"modules": ["X_Y%d" % i for i in range(101)]}):
            code, _, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_enable", bad)
            self.assertEqual(code, 400, bad)

    def test_module_updates(self):
        md = os.path.join(os.path.dirname(self.b), "magento")
        helpers.write(os.path.join(md, "app", "etc", "config.php"),
                      "<?php\nreturn [\n    'modules' => [\n        'Amasty_Base' => 1,\n    ],\n];\n")
        helpers.write(os.path.join(md, "vendor", "amasty", "base", "etc", "module.xml"),
                      '<config><module name="Amasty_Base"/></config>')
        helpers.write(os.path.join(md, "vendor", "amasty", "base", "composer.json"), '{"name": "amasty/base"}')
        helpers.write(os.path.join(md, "composer.json"), '{"require": {"amasty/base": "^1.17"}}')
        helpers.write(os.path.join(md, "composer.lock"), '{"packages": [{"name": "amasty/base", "version": "1.18.0"}]}')
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_check_updates", {})
        self.assertEqual(code, 202, body)
        self.wait_done(self.b, "stack-b", body["operation"])
        self.assertIn("bin/local/magento-module check-updates\n", calls(self.b))
        # Before a check, nothing can be updated.
        code, _, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_update", {"packages": ["amasty/base"]})
        self.assertEqual(code, 400)
        helpers.write(os.path.join(self.b, "logs", "module-updates.json"), json.dumps({
            "checkedAt": "2026-10-10T10:00:00Z", "exitCode": 0, "error": None,
            "packages": {"amasty/base": {"version": "1.18.0", "latest": "2.0.0", "latestStatus": "update-possible"}}}))
        code, body, _ = self.dash.request("GET", "/api/environments/stack-b/modules")
        self.assertEqual(code, 200, body)
        self.assertEqual(body["modules"][0]["update"]["status"], "update-available")
        self.assertTrue(body["updates"]["ok"])
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_update",
                                          {"packages": ["amasty/base"], "dryRun": True})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "Dry run: update amasty/base")
        self.assertIn("bin/local/magento-module update --dry-run amasty/base:^2.0.0\n", calls(self.b))
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_update",
                                          {"packages": ["amasty/base"], "build": False})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["label"], "Update amasty/base")
        self.assertIn("bin/local/magento-module update --no-build amasty/base:^2.0.0\n", calls(self.b))
        for bad in ({"packages": ["magento/module-catalog"]}, {"packages": ["amasty/base"], "dryRun": "yes"},
                    {"packages": ["amasty/base; rm"]}, {"packages": ["v/p%d" % i for i in range(101)]}):
            code, _, _ = self.dash.request("POST", "/api/environments/stack-b/actions/module_update", bad)
            self.assertEqual(code, 400, bad)

    def test_magento_releases(self):
        # stack-c: Magento 2.4.8-p3, composer.json pinning PHP 8.3; its repo has the real m2/ catalog.
        shutil.copytree(os.path.join(helpers.REPO, "m2"), os.path.join(self.repo, "m2"), dirs_exist_ok=True)
        md = os.path.join(self.c, "magento")
        helpers.write(os.path.join(md, "composer.lock"),
                      '{"packages": [{"name": "magento/product-community-edition", "version": "2.4.8-p3"}]}')
        helpers.write(os.path.join(md, "composer.json"), '{"config": {"platform": {"php": "8.3.20"}}}')
        php = {"PHP": "8.4, 8.3", "MariaDB": "11.4, 11.8", "OpenSearch": "3", "Valkey": "8.1", "RabbitMQ": "4.3",
               "Varnish": "8", "nginx": "1.30", "Composer": "2.10"}
        cache = os.path.join(self.dash.tmp, "cache", "platform-releases")
        helpers.write(os.path.join(cache, "releases.json"), json.dumps({
            "fetchedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "lastError": None,
            "releases": {"2.4.8-p5": php, "2.4.8-p6": php, "2.4.9": dict(php, PHP="8.5")}}))
        code, body, _ = self.dash.request("GET", "/api/environments/stack-c")
        self.assertEqual(code, 200, body)
        r = body["releases"]
        self.assertEqual(r["installed"], "2.4.8-p3")
        self.assertEqual(r["latestPublished"], "2.4.9")
        self.assertEqual(r["latestCompatible"], "2.4.8-p6")
        self.assertEqual(r["eligible"], ["2.4.8-p6", "2.4.8-p5", "2.4.8-p4"])
        nine = [x for x in r["releases"] if x["version"] == "2.4.9"][0]
        self.assertIn("config.platform.php", nine["reason"])  # PHP 8.3 pinned, 2.4.9 needs 8.5
        self.assertFalse(r["check"]["stale"])
        self.assertEqual(body["magentoEdition"], "community")
        code, body, _ = self.dash.request("GET", "/api/environments")
        self.assertEqual({e["id"]: e["releaseUpdate"] for e in body["environments"]}["stack-c"], "2.4.8-p6")
        # Check now with no network: 200, the error recorded, the old copy kept.
        code, body, _ = self.dash.request("POST", "/api/releases/refresh", {})
        self.assertEqual(code, 200, body)
        self.assertTrue(body["lastError"])
        self.assertTrue(body["fetchedAt"])
        code, body, _ = self.dash.request("GET", "/api/releases")
        self.assertEqual(code, 200, body)
        self.assertEqual(self.dash.request("POST", "/api/releases/refresh", {}, headers={"X-Wizard": None})[0], 403)
        code, body, _ = self.dash.request("GET", "/api/environments/stack-c")
        self.assertEqual(body["releases"]["latestCompatible"], "2.4.8-p6")

    def test_ownership_workflow(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/workflows/ownership", {"model": "two-owner"})
        self.assertEqual(code, 202, body)
        self.assertEqual(body["url"], "http://127.0.0.1:4242/?token=fakeTOKEN_123")
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual((rec["kind"], rec["label"]), ("ownership", "Switch file ownership to two-owner"))
        self.assertIn("launch.sh ownership two-owner --no-browser", calls(self.b))
        for bad in ({}, {"model": "root"}, {"model": "two-owner; id"}):
            self.assertEqual(self.dash.request("POST", "/api/environments/stack-b/workflows/ownership", bad)[0], 400, bad)
        # already the current model
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-b/workflows/ownership", {"model": "single"})[0], 409)

    def test_pipeline_workflow_needs_a_git_checkout(self):
        code, d, _ = self.dash.request("GET", "/api/environments/stack-c")
        self.assertEqual(code, 200)
        self.assertFalse(d["workflows"]["pipeline"]["available"])
        self.assertEqual(d["pipeline"]["status"], "missing")
        self.assertFalse(d["pipeline"]["isRepo"])
        self.assertEqual(self.dash.request("POST", "/api/environments/stack-c/workflows/pipeline")[0], 409)

        mdir = os.path.join(self.c, "magento")
        subprocess.run(["git", "init", "-q", mdir], check=True)
        try:
            code, d, _ = self.dash.request("GET", "/api/environments/stack-c")
            self.assertTrue(d["workflows"]["pipeline"]["available"])
            code, body, _ = self.dash.request("POST", "/api/environments/stack-c/workflows/pipeline")
            self.assertEqual(code, 202, body)
            rec = self.wait_done(self.c, "stack-c", body["operation"])
            self.assertEqual((rec["kind"], rec["label"]), ("pipeline", "Make pipeline-ready"))
            self.assertIn("launch.sh pipeline --no-browser", calls(self.repo))
        finally:
            shutil.rmtree(os.path.join(mdir, ".git"), ignore_errors=True)

    def test_rebuild_workflow_args(self):
        for bad in ({"composer": "remove"}, {"composer": "update", "packages": ["a;b"]},
                    {"composer": "install", "packages": ["a/b"]}, {"packages": ["a/b"]}):
            self.assertEqual(self.dash.request("POST", "/api/environments/stack-c/workflows/rebuild", bad)[0], 400, bad)
        code, body, _ = self.dash.request("POST", "/api/environments/stack-c/workflows/rebuild",
                                          {"composer": "update", "packages": ["amasty/shipping-rules"]})
        self.assertEqual(code, 202, body)
        rec = self.wait_done(self.c, "stack-c", body["operation"])
        self.assertEqual((rec["kind"], rec["label"]), ("rebuild", "composer update amasty/shipping-rules + rebuild"))
        self.assertIn("launch.sh rebuild update amasty/shipping-rules --no-browser", calls(self.repo))

    def test_lock_held_refuses_and_keeps_text(self):
        lock = os.path.join(self.a, ".wizard.lock")
        fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.ftruncate(fd, 0)
            os.write(fd, b"PID 1, started 2026-01-01 00:00:00\n")
            code, body, _ = self.dash.request("POST", "/api/environments/stack-a/actions/cache_flush")
            self.assertEqual(code, 409)
            self.assertIn("PID 1", body.get("detail", ""))
            self.assertEqual(self.dash.request("POST", "/api/environments/stack-a/workflows/refresh")[0], 409)
            with open(lock) as f:
                self.assertEqual(f.read(), "PID 1, started 2026-01-01 00:00:00\n")
            # read-only actions and other checkouts aren't blocked
            code_a, body_a, _ = self.dash.request("POST", "/api/environments/stack-a/actions/indexer_status")
            code_b, body_b, _ = self.dash.request("POST", "/api/environments/stack-b/actions/cache_clean")
            self.assertEqual((code_a, code_b), (202, 202))
        finally:
            os.close(fd)
        self.wait_done(self.a, "stack-a", body_a["operation"])
        self.wait_done(self.b, "stack-b", body_b["operation"])

    def test_in_container_actions_cant_be_cancelled(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-a/actions/cache_clean")
        self.assertEqual(code, 202)
        code, _, _ = self.dash.request("POST", "/api/environments/stack-a/operations/%s/cancel" % body["operation"])
        self.assertIn(code, (400, 409))
        self.wait_done(self.a, "stack-a", body["operation"])

    def test_workflow_returns_wizard_link_and_record(self):
        code, body, _ = self.dash.request("POST", "/api/environments/stack-b/workflows/refresh")
        self.assertEqual(code, 202, body)
        self.assertEqual(body["url"], "http://127.0.0.1:4242/?token=fakeTOKEN_123")
        code, ops_, _ = self.dash.request("GET", "/api/environments/stack-b/operations")
        self.assertTrue(any(o["id"] == body["operation"] and o["kind"] == "refresh" for o in ops_["operations"]))
        rec = self.wait_done(self.b, "stack-b", body["operation"])
        self.assertEqual(rec["status"], "done")


if __name__ == "__main__":
    unittest.main()
