"""Small bash helpers that guard user input on its way into files, sed and
the filesystem: bin/lib/env.sh and bin/lib/db-backup.sh."""

import json
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

    def test_live_site_url_and_cookie_domain(self):
        self.assertEqual(bash("normalize_base_url https://example.com", self.d).stdout, "https://example.com/")
        self.assertEqual(bash("normalize_base_url https://cdn.example.com/media/", self.d).stdout,
                         "https://cdn.example.com/media/")
        for bad in ("example.com", "ftp://x.com/", "'https://a b.com/'", "https://", "'https://x.com/|w'"):
            self.assertNotEqual(bash("normalize_base_url %s" % bad, self.d).returncode, 0, bad)
        for ok in (".example.com", "www.example.com"):
            self.assertEqual(bash("valid_cookie_domain %s" % ok, self.d).returncode, 0, ok)
        for bad in ("''", "localhost", "'.ex ample.com'"):
            self.assertNotEqual(bash("valid_cookie_domain %s" % bad, self.d).returncode, 0, bad)

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


class ConfirmReplace(unittest.TestCase):
    """sync-db-config-engine.sh confirm_replace: never replaces a database on
    a default when nobody can answer."""

    def run_confirm(self, preset=None):
        engine = os.path.join(LIB, "sync-db-config-engine.sh")
        script = ('source "%s/ui.sh"; cmd_hint() { echo "mdk refresh-wizard"; }; '
                  'eval "$(sed -n \'/^confirm_replace() {/,/^}/p\' "%s")"; '
                  'MARIADB_DB=magento; confirm_replace && echo CONFIRMED') % (LIB, engine)
        env = {k: v for k, v in os.environ.items() if k not in ("SETUP_UI", "PRESET_REPLACE_CHOICE")}
        if preset is not None:
            env["PRESET_REPLACE_CHOICE"] = preset
        return subprocess.run(["bash", "-c", script], stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env)

    def test_no_terminal_stops(self):
        p = self.run_confirm()
        self.assertEqual(p.returncode, 1)
        self.assertNotIn("CONFIRMED", p.stdout)
        self.assertIn("Not replacing the database without a confirmation", p.stderr)

    def test_preset_yes_and_no(self):
        self.assertIn("CONFIRMED", self.run_confirm("Y").stdout)
        p = self.run_confirm("N")
        self.assertEqual(p.returncode, 130)
        self.assertNotIn("CONFIRMED", p.stdout)


class PerRepoGit(unittest.TestCase):
    """bin/lib/git-repo.sh: logins and identities stay in one repo."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="repo-git-")
        self.home = os.path.join(self.d, "home")
        os.makedirs(self.home)
        for r in ("company", "personal"):
            subprocess.run(["git", "init", "-q", os.path.join(self.d, r)], check=True)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def sh(self, script):
        env = dict(os.environ, HOME=self.home, GIT_TERMINAL_PROMPT="0", GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["bash", "-c", 'source "%s/ui.sh"; source "%s/git-repo.sh"; %s' % (LIB, LIB, script)],
                              cwd=self.d, capture_output=True, text=True, env=env)

    def fill(self, repo):
        env = dict(os.environ, HOME=self.home, GIT_TERMINAL_PROMPT="0", GIT_ASKPASS="/bin/false", GIT_CONFIG_NOSYSTEM="1")
        return subprocess.run(["git", "-C", os.path.join(self.d, repo), "credential", "fill"],
                              input="protocol=https\nhost=github.com\n\n", capture_output=True, text=True, env=env)

    def test_login_saved_for_one_repo_only(self):
        p = self.sh("repo_remember_login \"$PWD/company\" github.com work-user ghp_company0123456789")
        self.assertEqual(p.returncode, 0, p.stderr)
        creds = os.path.join(self.d, "company", ".git", "credentials")
        self.assertEqual(os.stat(creds).st_mode & 0o777, 0o600)
        self.assertIn("password=ghp_company0123456789", self.fill("company").stdout)
        self.assertNotEqual(self.fill("personal").returncode, 0)  # the other repo never sees it
        self.assertFalse(os.path.exists(os.path.join(self.home, ".gitconfig")))
        self.assertFalse(os.path.exists(os.path.join(self.home, ".git-credentials")))

    def github(self, emails_ok, name="Up Sage"):
        """github_identity against a fake curl (no network) that records its
        argv, so the test can check the token never reaches a command line."""
        fake = os.path.join(self.d, "fakebin")
        os.makedirs(fake, exist_ok=True)
        user = json.dumps({"login": "UpSage", "id": 4242, "name": name, "email": None})
        emails = json.dumps([{"email": "old@x.com", "primary": False, "verified": True},
                             {"email": "me@personal.com", "primary": True, "verified": True}])
        helpers.write(os.path.join(fake, "curl"), """#!/bin/bash
echo "$*" >> "%(d)s/curl-args"
cat > "%(d)s/curl-stdin"
case "$*" in
  *user/emails*) %(emails)s ;;
  *api.github.com/user*) echo '%(user)s' ;;
esac
""" % {"d": self.d, "user": user, "emails": ("echo '%s'" % emails) if emails_ok else "exit 22"}, 0o755)
        env = dict(os.environ, HOME=self.home, PATH=fake + os.pathsep + os.environ["PATH"])
        p = subprocess.run(["bash", "-c", 'source "%s/ui.sh"; source "%s/git-repo.sh"; github_identity ghp_SECRET123' % (LIB, LIB)],
                           capture_output=True, text=True, env=env)
        self.assertNotIn("ghp_SECRET123", helpers.read(os.path.join(self.d, "curl-args")))
        self.assertIn("ghp_SECRET123", helpers.read(os.path.join(self.d, "curl-stdin")))  # header via stdin
        return p.stdout

    def test_identity_from_the_github_account(self):
        self.assertEqual(self.github(emails_ok=True), "Up Sage\tme@personal.com\n")
        # The token may not read emails: the account's noreply address still credits it.
        self.assertEqual(self.github(emails_ok=False, name=None), "UpSage\t4242+UpSage@users.noreply.github.com\n")

    def test_given_identity_is_used_without_asking(self):
        p = self.sh('repo_ensure_identity "$PWD/personal" "Up Sage" me@personal.com </dev/null')
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Up Sage <me@personal.com>", p.stdout)
        git_email = subprocess.run(["git", "-C", os.path.join(self.d, "personal"), "config", "--local", "user.email"],
                                   capture_output=True, text=True).stdout.strip()
        self.assertEqual(git_email, "me@personal.com")

    def test_identity_validation(self):
        for ok in ("dev@company.com", "a.b+c@x.co"):
            self.assertEqual(self.sh("valid_git_email '%s'" % ok).returncode, 0, ok)
        for bad in ("nope", "a b@c.co", "@x.co", "a@b"):
            self.assertNotEqual(self.sh("valid_git_email '%s'" % bad).returncode, 0, bad)
        self.assertEqual(self.sh("valid_git_name 'UpSage'").returncode, 0)
        for bad in ("", "A <b>"):
            self.assertNotEqual(self.sh("valid_git_name '%s'" % bad).returncode, 0, bad)

    def test_existing_identity_is_kept_and_none_is_reported(self):
        subprocess.run(["git", "-C", os.path.join(self.d, "company"), "config", "user.email", "dev@company.com"], check=True)
        subprocess.run(["git", "-C", os.path.join(self.d, "company"), "config", "user.name", "Dev"], check=True)
        p = self.sh('repo_ensure_identity "$PWD/company" </dev/null')
        self.assertIn("Dev <dev@company.com>", p.stdout)
        p = self.sh('repo_ensure_identity "$PWD/personal" </dev/null')  # no terminal: tells how, carries on
        self.assertEqual(p.returncode, 0)
        self.assertIn("no commit identity of its own", p.stdout)


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
