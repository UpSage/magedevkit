"""Small bash helpers that guard user input on its way into files, sed and
the filesystem: bin/lib/env.sh and bin/lib/db-backup.sh."""

import os
import shutil
import subprocess
import tempfile
import time
import unittest

import helpers

LIB = os.path.join(helpers.REPO, "bin", "lib")


def bash(script, cwd):
    return subprocess.run(["bash", "-c", 'for f in ui env util db-backup; do source "%s/$f.sh"; done; %s' % (LIB, script)],
                          cwd=cwd, capture_output=True, text=True)


class BashHelpers(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="bash-helpers-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_set_env_var_is_literal_and_keeps_mode(self):
        env = os.path.join(self.d, "t.env")
        helpers.write(env, "A=1\nB=old\n", 0o600)
        evil = "x|w %s/pwned & \\back/slash $(id)" % self.d
        p = bash("set_env_var t.env B '%s'; set_env_var t.env C new" % evil, self.d)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(helpers.read(env), "A=1\nB=%s\nC=new\n" % evil)
        self.assertFalse(os.path.exists(os.path.join(self.d, "pwned")))
        self.assertEqual(os.stat(env).st_mode & 0o777, 0o600)

    def test_set_env_var_refuses_multiline_and_bad_keys(self):
        env = os.path.join(self.d, "t.env")
        helpers.write(env, "A=1\n")
        self.assertNotEqual(bash('set_env_var t.env A "$(printf "a\\nB=b")"', self.d).returncode, 0)
        self.assertNotEqual(bash('set_env_var t.env "A.*" 2', self.d).returncode, 0)
        self.assertEqual(helpers.read(env), "A=1\n")

    def test_env_get_reads_like_source(self):
        helpers.write(os.path.join(self.d, "t.env"),
                      "A=first\nA=last\nQ=\"quoted value\"\nS='single'\nE=\nU=a=b=c\n# A=comment\n")
        get = lambda *a: bash("env_get t.env " + " ".join(a), self.d).stdout
        self.assertEqual(get("A"), "last")
        self.assertEqual(get("Q"), "quoted value")
        self.assertEqual(get("S"), "single")
        self.assertEqual(get("U"), "a=b=c")
        self.assertEqual(get("E", "dflt"), "dflt")
        self.assertEqual(get("MISSING", "dflt"), "dflt")
        self.assertEqual(bash("env_get nope.env A x", self.d).stdout, "x")

    def test_sed_literal_rewrite(self):
        p = bash('printf "%s\\n" "a https://w.x.com/ b https://wXx.com/" '
                 '| sed -e "s|$(sed_re_literal "https://w.x.com/")|$(sed_repl_literal "https://n.test/&|")|g"', self.d)
        self.assertEqual(p.stdout, "a https://n.test/&| b https://wXx.com/\n")

    def test_valid_hostname(self):
        for ok in ("shop.test", "my-store.local", "a.b.c"):
            self.assertEqual(bash('valid_hostname "%s"' % ok, self.d).returncode, 0, ok)
        for bad in ("localhost", "x;rm.test", "a..b", "-a.b", "a.b|c", "a b.test", ""):
            self.assertNotEqual(bash("valid_hostname '%s'" % bad, self.d).returncode, 0, bad)

    def test_version_compare(self):
        for a, b, lt in (("2.4.7", "2.4.8", True), ("2.4.8", "2.4.7", False), ("2.4.8", "2.4.8", False),
                         ("2.4.8", "2.4.8-p1", True), ("10.6", "11.4", True)):
            self.assertEqual(bash("version_lt %s %s" % (a, b), self.d).returncode == 0, lt, (a, b))
        self.assertEqual(bash("version_ge 1.16.5 1.6.0", self.d).returncode, 0)

    def test_composer_check(self):
        for ok in ("", "install", "update", "update vendor/pkg magento/*"):
            self.assertEqual(bash("composer_check %s" % ok, self.d).returncode, 0, ok)
        for bad in ("remove", "install vendor/pkg", "update Vendor/Pkg", "update 'a/b;rm'", "update ../x"):
            self.assertNotEqual(bash("composer_check %s" % bad, self.d).returncode, 0, bad)

    def test_prune_backups_keeps_newest_and_handles_spaces(self):
        names = ["app-1.sql.gz", "app 2.sql.gz", "app-3.sql.gz", "app-4.sql.gz", "other-1.sql.gz"]
        for i, n in enumerate(names):
            path = os.path.join(self.d, n)
            helpers.write(path, "x")
            os.utime(path, (time.time() - 100 + i, time.time() - 100 + i))
        p = bash('prune_backups "$PWD" app 2', self.d)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(sorted(os.listdir(self.d)), ["app-3.sql.gz", "app-4.sql.gz", "other-1.sql.gz"])


class S3Storage(unittest.TestCase):
    """bin/lib/aws-storage.sh against a fake `aws` (no network)."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="s3-storage-")
        self.bin = os.path.join(self.d, "bin")
        os.makedirs(self.bin)
        helpers.write(os.path.join(self.bin, "aws"), """#!/bin/sh
case "$*" in
  "sts get-caller-identity"*) exit 0 ;;
  "configure get region"*) echo eu-west-1 ;;
  "configure get aws_access_key_id"*) echo AKIAFAKEFAKEFAKE0000 ;;
  "configure get aws_secret_access_key"*) echo fake-secret ;;
  "s3api head-bucket"*) exit 0 ;;
esac
""", 0o755)
        helpers.write(os.path.join(self.d, ".env"), "REMOTE_STORAGE_DRIVER=CHANGE_ME\nREMOTE_STORAGE_BUCKET=old\n")
        os.makedirs(os.path.join(self.d, "home", ".aws"))
        helpers.write(os.path.join(self.d, "home", ".aws", "credentials"), "[shop]\n")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_choose_and_save(self):
        script = """
            source "%(lib)s/ui.sh"; source "%(lib)s/env.sh"; source "%(lib)s/aws-storage.sh"
            ENV_FILE=.env ENV_NAME=.env
            prompt_aws_profile() { echo shop; }
            prompt_s3_bucket() { echo "picked-$2"; }
            s3_bucket_region() { echo eu-central-1; }
            aws_configure_profile() { echo "configure called" >&2; }
            s3_storage_choose "$PWD/.envrc" old
            s3_storage_save aws-s3 "$RS_BUCKET" "$RS_REGION" "$AWS_KEY_ID" "$AWS_SECRET"
            echo "profile=$AWS_PROFILE bucket=$RS_BUCKET region=$RS_REGION"
        """ % {"lib": LIB}
        p = subprocess.run(["bash", "-c", script], cwd=self.d, capture_output=True, text=True,
                           env=dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"],
                                    HOME=os.path.join(self.d, "home")))
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("profile=shop bucket=picked-old region=eu-central-1", p.stdout)
        self.assertNotIn("configure called", p.stderr)  # profile already set up
        env = helpers.read(os.path.join(self.d, ".env"))
        self.assertIn("REMOTE_STORAGE_DRIVER=aws-s3\n", env)
        self.assertIn("REMOTE_STORAGE_BUCKET=picked-old\n", env)
        self.assertIn("AWS_PROFILE_DEFAULT=shop\n", env)
        envrc = helpers.read(os.path.join(self.d, ".envrc"))
        self.assertEqual(envrc, "export AWS_PROFILE=shop\nexport AWS_S3_PROFILE=shop\n")  # never keys


if __name__ == "__main__":
    unittest.main()
