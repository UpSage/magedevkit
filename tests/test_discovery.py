import os
import shutil
import tempfile
import unittest

import helpers
import envs


class StubDocker:
    def __init__(self, containers, error=None):
        self.c, self.err = containers, error

    def containers(self):
        return self.c, self.err

    def invalidate(self):
        pass


class Discovery(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="disc-")
        self.a = helpers.make_checkout(self.root, "ProjA", "stack-a", "a.test")
        self.b = helpers.make_checkout(self.root, "ProjB", "stack-b", "b.test")
        # B is only known to Docker: it lives outside the scanned root.
        self.elsewhere = tempfile.mkdtemp(prefix="disc-else-")
        self.c = helpers.make_checkout(self.elsewhere, "ProjC", "stack-c", "c.test")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.elsewhere, ignore_errors=True)

    def test_scan_and_docker_are_merged(self):
        conts = [
            helpers.container("1", "stack-a-platform", "nginx", os.path.join(self.a, "compose")),
            helpers.container("2", "stack-c-platform", "php", os.path.join(self.c, "compose"), health=None),
            helpers.container("3", "platform-edge", "traefik", "/x/compose/edge"),
        ]
        found, warnings, projects, err = envs.Discovery([self.root], StubDocker(conts)).environments()
        self.assertEqual(sorted(found), ["stack-a", "stack-b", "stack-c"])
        self.assertEqual(found["stack-c"].platform_dir, os.path.realpath(self.c))
        self.assertIn("platform-edge", projects)
        self.assertEqual(warnings, [])

    def test_stacks_dont_mix(self):
        conts = [
            helpers.container("1", "stack-a-platform", "nginx", os.path.join(self.a, "compose"), state="running"),
            helpers.container("2", "stack-b-platform", "nginx", os.path.join(self.b, "compose"), state="exited"),
        ]
        found, _, projects, _ = envs.Discovery([self.root], StubDocker(conts)).environments()
        sa = envs.summarize(found["stack-a"], projects.get("stack-a-platform", []))
        sb = envs.summarize(found["stack-b"], projects.get("stack-b-platform", []))
        self.assertEqual(sa["docker"]["running"], 1)
        self.assertEqual(sb["docker"]["running"], 0)
        self.assertEqual(sb["services"]["nginx"]["text"], "Stopped")

    def test_instances_found_by_label_and_scan(self):
        repo = helpers.make_repo(self.root)
        scanned = helpers.make_instance(self.root, "inst-a", repo)
        labelled = helpers.make_instance(self.elsewhere, "inst-b", repo)
        conts = [helpers.container("9", "inst-b-platform", "php", os.path.join(repo, "compose"), instance_dir=labelled)]
        found, warnings, _, _ = envs.Discovery([self.root], StubDocker(conts)).environments()
        self.assertTrue(found["inst-a"].is_instance)
        self.assertEqual(found["inst-b"].platform_dir, os.path.realpath(labelled))
        self.assertEqual(found["inst-b"].code_dir, os.path.realpath(repo))
        self.assertEqual(found["inst-b"].env_name, ".env")
        self.assertIsNone(found["inst-b"].readonly)
        self.assertEqual(warnings, [])

    def test_instance_name_must_match_folder(self):
        repo = helpers.make_repo(self.root)
        d = helpers.make_instance(self.root, "folder-x", repo)
        with open(os.path.join(d, ".env"), "w") as f:
            f.write("APP_NAME=other\n")
        found, _, _, _ = envs.Discovery([self.root], StubDocker([])).environments()
        self.assertTrue(found["other"].readonly)

    def test_dev_target_is_read_only_and_bad_names_skipped(self):
        helpers.write(os.path.join(self.b, "env", ".env.dev"), "APP_NAME=stack-b\n")
        d = helpers.make_checkout(self.root, "ProjD", '"quoted"', "d.test")
        found, warnings, _, _ = envs.Discovery([self.root], StubDocker([])).environments()
        self.assertTrue(found["stack-b"].readonly)
        self.assertNotIn('"quoted"', found)
        self.assertTrue(any(d in w for w in warnings))

    def test_duplicate_app_name_is_skipped(self):
        helpers.make_checkout(self.root, "ProjE", "stack-a", "e.test")
        found, warnings, _, _ = envs.Discovery([self.root], StubDocker([])).environments()
        self.assertEqual(len([e for e in found.values() if e.id == "stack-a"]), 1)
        self.assertTrue(any("also used by" in w for w in warnings))

    def test_stack_env_overrides_versions(self):
        helpers.write(os.path.join(self.a, "env", "stack.env"), "PHP_VERSION=8.5\n")
        e = envs.Environment(self.a)
        self.assertEqual(e.value("PHP_VERSION"), "8.5")
        with open(e.env_file, "a") as f:
            f.write("STACK_LOCAL_OVERRIDE=1\n")
        self.assertEqual(envs.Environment(self.a).value("PHP_VERSION"), "8.4")

    def test_docker_unavailable_means_unknown(self):
        found, _, _, err = envs.Discovery([self.root], StubDocker([], "down")).environments()
        s = envs.summarize(found["stack-a"], [], docker_ok=False)
        self.assertEqual(s["docker"]["state"], "unknown")
        self.assertEqual(s["services"]["php"]["level"], "unknown")

    def test_service_status(self):
        v = lambda **kw: dict({"state": "running", "health": None, "healthDetail": None, "exitCode": 0}, **kw)
        self.assertEqual(envs.service_status(v(health="healthy"))["level"], "ok")
        self.assertEqual(envs.service_status(v(health="unhealthy"))["level"], "bad")
        self.assertEqual(envs.service_status(v())["text"], "Running (no healthcheck)")
        self.assertEqual(envs.service_status(v(state="exited", exitCode=1))["level"], "bad")
        self.assertEqual(envs.service_status(None)["text"], "Not created")

    def test_magento_facts(self):
        e = envs.Environment(self.a)
        self.assertEqual(envs.actual_mode(e.magento_dir), ("developer", None))
        helpers.write(os.path.join(e.magento_dir, "composer.lock"),
                      '{"packages": [{"name": "magento/magento2-base", "version": "2.4.9"},'
                      ' {"name": "magento/product-community-edition", "version": "2.4.9-p1"}]}')
        self.assertEqual(envs.magento_version_from_lock(e.magento_dir), "2.4.9-p1")


if __name__ == "__main__":
    unittest.main()
