"""bin/local/install-mdk: the mdk link, removing retired command links and
re-pointing instances after the repo moved — run against a throwaway copy of
the scripts with a throwaway HOME."""

import os
import shutil
import subprocess
import tempfile
import unittest

import helpers


class InstallMdk(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="install-"))
        self.root = os.path.join(self.tmp, "sites")
        self.repo = os.path.join(self.root, "magedevkit")
        helpers.copy_scripts(self.repo, "bin/local/install-mdk", "bin/local/new-instance", "bin/mdk",
                             "env/.env.local.example")
        self.home = os.path.join(self.tmp, "home")
        self.bin = os.path.join(self.home, ".local", "bin")
        os.makedirs(self.bin)
        # A docker that is "not running", so new-instance skips its Docker check.
        fakebin = os.path.join(self.tmp, "fakebin")
        helpers.write(os.path.join(fakebin, "docker"), "#!/bin/sh\nexit 1\n", 0o755)
        self.env = dict(os.environ, HOME=self.home,
                        PATH=os.pathsep.join([self.bin, fakebin, os.environ["PATH"]]))
        for k in ("PLATFORM_INSTANCES_ROOT", "PLATFORM_INSTANCE_DIR"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self):
        p = subprocess.run([os.path.join(self.repo, "bin", "local", "install-mdk")], env=self.env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stdout)
        return p.stdout

    def instance(self, name, repo):
        d = os.path.join(self.root, name)
        helpers.write(os.path.join(d, ".platform-instance"), "PLATFORM_REPO=%s\n" % repo)
        helpers.write(os.path.join(d, ".env"), "APP_NAME=%s\nCUSTOM=1\n" % name)
        return d

    def test_links_mdk_and_removes_retired_mrn(self):
        os.symlink("/old/sites/magerunner/bin/mrn", os.path.join(self.bin, "mrn"))
        out = self.install()
        self.assertEqual(os.readlink(os.path.join(self.bin, "mdk")), os.path.join(self.repo, "bin", "mdk"))
        self.assertFalse(os.path.lexists(os.path.join(self.bin, "mrn")))
        self.assertIn("Removed the retired command", out)

    def test_keeps_an_unrelated_mrn(self):
        os.symlink("/opt/tools/mrn-cli", os.path.join(self.bin, "mrn"))
        self.install()
        self.assertEqual(os.readlink(os.path.join(self.bin, "mrn")), "/opt/tools/mrn-cli")

    def test_repoints_instances_whose_repo_is_gone(self):
        moved = self.instance("moved", os.path.join(self.root, "magerunner"))   # renamed away
        other_repo = os.path.join(self.tmp, "other-checkout")
        os.makedirs(other_repo)
        other = self.instance("other", other_repo)
        current = self.instance("current", self.repo)
        out = self.install()

        marker = helpers.read(os.path.join(moved, ".platform-instance"))
        self.assertIn("PLATFORM_REPO=%s\n" % self.repo, marker)
        self.assertIn("Commands: mdk (bin/mdk)", marker)
        self.assertIn("CUSTOM=1", helpers.read(os.path.join(moved, ".env")))   # settings untouched
        self.assertIn("Re-pointed instance moved", out)
        self.assertIn("mdk -i moved platform up -d", out)
        # Another existing checkout's instance and this repo's own are left alone.
        self.assertEqual(helpers.read(os.path.join(other, ".platform-instance")), "PLATFORM_REPO=%s\n" % other_repo)
        self.assertEqual(helpers.read(os.path.join(current, ".platform-instance")), "PLATFORM_REPO=%s\n" % self.repo)
        self.assertNotIn("instance other", out)
        self.assertNotIn("instance current", out)

        # Re-running changes nothing.
        out = self.install()
        self.assertNotIn("Re-pointed", out)
        self.assertEqual(helpers.read(os.path.join(moved, ".platform-instance")), marker)


if __name__ == "__main__":
    unittest.main()
