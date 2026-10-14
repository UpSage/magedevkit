"""The pipeline wizard (bin/local/pipeline + bin/lib/pipeline-engine.sh) run for
real against a throwaway instance whose magento/ is a small git repo, answers
given through PRESET_PIPELINE_*; plus the dashboard's readiness probe
(bin/lib/web-wizard/pipeline.py) on the result. No AWS: the AWS CLI finds no
config, so every lookup falls back to the preset answers. Terraform isn't run
The CloudFormation template is checked offline by bin/lib/cfn-check.py."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import fakes
import helpers
import envs
import pipeline

CFN_CHECK = os.path.join(helpers.REPO, "bin", "lib", "cfn-check.py")
PINS = ("PHP_VERSION=8.4\nMARIADB_VERSION=12.3\nOPENSEARCH_VERSION=3\nVALKEY_VERSION=9\nRABBITMQ_VERSION=4.3\n"
        "VARNISH_VERSION=8\nNGINX_VERSION=1.30\nCOMPOSER_VERSION=2.10\nDEBIAN_RELEASE=trixie\nMAGENTO_VERSION=2.4.9\n")

PRESETS = {
    "PRESET_PIPELINE_ENV": "dev", "PRESET_PIPELINE_APP": "shop", "PRESET_PIPELINE_ACCOUNT": "123456789012",
    "PRESET_PIPELINE_REGION": "us-east-2", "PRESET_PIPELINE_ZONE": "shop.test",
    "PRESET_PIPELINE_DOMAIN": "dev.shop.test", "PRESET_PIPELINE_INSTANCE_TYPE": "t3.large",
    "PRESET_PIPELINE_OFF_HOURS": "no", "PRESET_PIPELINE_BRANCH": "development",
    "PRESET_PIPELINE_GITHUB_REPO": "Acme/shop",
    "PRESET_PIPELINE_GITHUB_CONNECTION": "arn:aws:codestar-connections:us-east-2:123456789012:connection/abc-123",
    "PRESET_PIPELINE_MODE": "production", "PRESET_PIPELINE_ACME_EMAIL": "ops@shop.test",
    "PRESET_PIPELINE_STATIC_LOCALES": "en_US", "PRESET_PIPELINE_ZONE_ID": "Z0123456789ABCDEFGHIJ",
    "PRESET_PIPELINE_IMAGE_ID": "ami-0123456789abcdef0", "PRESET_PIPELINE_PRODUCTION": "no",
    "PRESET_PIPELINE_DUMP_BUCKET": "shop-dumps",
    "PRESET_PIPELINE_COMMIT": "yes", "PRESET_PIPELINE_BUILD_TEST": "no", "PRESET_PIPELINE_REMOVE_OLD": "yes",
}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=True).stdout.strip()


class PipelineWizard(unittest.TestCase):
    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="pipeline-"))
        self.inst = os.path.join(self.tmp, "shop")
        helpers.write(os.path.join(self.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % helpers.REPO)
        helpers.write(os.path.join(self.inst, ".env"),
                      "APP_NAME=shop\nAPP_DOMAIN=shop.test\nAPP_TIMEZONE=UTC\nMAGENTO_MODE=developer\n"
                      "MAGENTO_ADMIN_URI=backend\nFILE_OWNERSHIP=single\n" + PINS)
        self.mdir = os.path.join(self.inst, "magento")
        helpers.write(os.path.join(self.mdir, "composer.json"), '{"name": "acme/shop"}\n')
        helpers.write(os.path.join(self.mdir, "app", "etc", "config.php"),
                      "<?php return ['modules' => [], 'scopes' => ['websites' => []]];\n")
        helpers.write(os.path.join(self.mdir, "buildspec.yml"), "version: 0.2\n")
        helpers.write(os.path.join(self.mdir, "scripts", "before_install.sh"), "#!/bin/sh\n", 0o755)
        subprocess.run(["git", "init", "-q", "-b", "development", self.mdir], check=True)
        # The repo's own identity — like every store repo, never a global one.
        git(self.mdir, "config", "user.name", "t")
        git(self.mdir, "config", "user.email", "t@t")
        git(self.mdir, "remote", "add", "origin", "https://github.com/Acme/shop.git")
        git(self.mdir, "add", "-A")
        git(self.mdir, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "store")
        self.env = dict(os.environ, PLATFORM_INSTANCE_DIR=self.inst,
                        XDG_CONFIG_HOME=os.path.join(self.tmp, "config"),  # no MageDevKit AWS connections
                        AWS_CONFIG_FILE=os.path.join(self.tmp, "none"),
                        AWS_SHARED_CREDENTIALS_FILE=os.path.join(self.tmp, "none"),
                        GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t",
                        **PRESETS)
        for k in ("MAGENTO_DIR", "ENV_FILE", "SETUP_UI", "UI_FLOW", "WIZARD_LOCK_HELD"):
            self.env.pop(k, None)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_wizard(self, **extra):
        env = dict(self.env, **extra)
        return subprocess.run([os.path.join(helpers.REPO, "bin", "local", "pipeline")], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=300)

    def info(self):
        pipeline.drop_cache()
        return pipeline.info(envs.Environment(self.inst))

    def test_generates_commits_and_detects_edits(self):
        self.assertEqual(self.info()["status"], "missing")

        p = self.run_wizard()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        d = self.mdir
        for rel in ("deploy/pipeline.json", "deploy/buildspec.yml", "deploy/appspec.yml", "deploy/compose/app.yml",
                    "deploy/docker/php/Dockerfile", "deploy/config/nginx/magento-backend.conf",
                    "deploy/infra/stack.json", "deploy/infra/dev.params.json", "deploy/targets/dev.env",
                    "deploy/targets/dev.config-overrides.php", ".platform/stack.env"):
            self.assertTrue(os.path.isfile(os.path.join(d, rel)), rel)
        self.assertTrue(os.access(os.path.join(d, "deploy/scripts/box"), os.X_OK))
        target = helpers.read_json(os.path.join(d, "deploy/pipeline.json"))["targets"]["dev"]
        self.assertEqual(target["account"], "123456789012")  # the account, never a local profile name
        self.assertNotIn("profile", target)
        with open(os.path.join(d, "deploy/targets/dev.env")) as f:
            target = f.read()
        self.assertIn("APP_DOMAIN=dev.shop.test", target)
        self.assertIn("ADMIN_URI=backend", target)
        self.assertIn("PRODUCTION=no", target)
        self.assertIn("DUMP_BUCKET=shop-dumps", target)
        self.assertEqual(subprocess.run(["find", os.path.join(d, "deploy"), "-name", "*.tf"], capture_output=True,
                                        text=True).stdout, "")  # no Terraform left
        check = subprocess.run(["python3", CFN_CHECK, os.path.join(d, "deploy/infra/stack.json"),
                                os.path.join(d, "deploy/infra/dev.params.json")], capture_output=True, text=True)
        self.assertEqual(check.returncode, 0, check.stdout)
        with open(os.path.join(d, "deploy/appspec.yml")) as f:
            self.assertIn("destination: /opt/shop/incoming", f.read())
        with open(os.path.join(d, "deploy/config/nginx/magento-backend.conf")) as f:
            nginx = f.read()
        self.assertIn("listen 8080;", nginx)
        self.assertNotIn("listen 443", nginx)
        out = subprocess.run(["grep", "-rlE", "@@[A-Z_]+@@", os.path.join(d, "deploy"), os.path.join(d, ".platform")],
                             capture_output=True, text=True)
        self.assertEqual(out.stdout, "", "unfilled placeholders")

        # Committed on a new local branch, old pipeline removed, nothing pushed.
        branch = git(d, "rev-parse", "--abbrev-ref", "HEAD")
        self.assertTrue(branch.startswith("pipeline/dev-"), branch)
        self.assertEqual(git(d, "status", "--porcelain"), "")
        tracked = git(d, "ls-files").splitlines()
        self.assertNotIn("buildspec.yml", tracked)
        self.assertNotIn("scripts/before_install.sh", tracked)
        self.assertEqual(git(d, "branch", "-r"), "")

        i = self.info()
        self.assertEqual(i["status"], "current")
        self.assertEqual([t["env"] for t in i["targets"]], ["dev"])
        self.assertTrue(i["pins"]["inSync"])
        self.assertEqual(i["oldFiles"], [])

        # A rerun with nothing changed commits nothing.
        head = git(d, "rev-parse", "HEAD")
        p = self.run_wizard()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("Nothing changed", p.stdout)
        self.assertEqual(git(d, "rev-parse", "HEAD"), head)

        # A hand edit is reported and kept…
        edge = os.path.join(d, "deploy/compose/edge.yml")
        with open(edge, "a") as f:
            f.write("# my tweak\n")
        git(d, "commit", "-qam", "tweak")
        self.assertEqual(self.info()["status"], "edited")
        p = self.run_wizard()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("edited by hand: deploy/compose/edge.yml", p.stdout)
        with open(edge) as f:
            self.assertIn("# my tweak", f.read())

        # …unless replacing is chosen.
        p = self.run_wizard(PRESET_PIPELINE_REPLACE_EDITED="yes")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(edge) as f:
            self.assertNotIn("# my tweak", f.read())
        self.assertEqual(self.info()["status"], "current")

        # A second target keeps the first one's files.
        p = self.run_wizard(PRESET_PIPELINE_ENV="staging", PRESET_PIPELINE_DOMAIN="staging.shop.test",
                            PRESET_PIPELINE_BRANCH="staging")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(os.path.isfile(os.path.join(d, "deploy/targets/dev.env")))
        self.assertTrue(os.path.isfile(os.path.join(d, "deploy/infra/dev.params.json")))
        self.assertTrue(os.path.isfile(os.path.join(d, "deploy/infra/staging.params.json")))
        i = self.info()
        self.assertEqual([t["env"] for t in i["targets"]], ["dev", "staging"])
        self.assertEqual(i["status"], "current")

    def test_upgrade_retires_old_files_and_keeps_overrides(self):
        self.assertEqual(self.run_wizard().returncode, 0)
        d = self.mdir
        # A kit-v2 leftover: one Terraform file as generated, one edited by hand.
        manifest_path = os.path.join(d, "deploy/pipeline.json")
        m = helpers.read_json(manifest_path)
        for rel, text, recorded in (("deploy/infra/envs/dev/main.tf", "terraform {}\n", "terraform {}\n"),
                                    ("deploy/infra/modules/ecr/main.tf", "# mine\n", "original\n")):
            helpers.write(os.path.join(d, rel), text)
            m["files"][rel] = hashlib.sha256(recorded.encode()).hexdigest()
        with open(manifest_path, "w") as f:
            json.dump(m, f, indent=2)
        overrides = os.path.join(d, "deploy/targets/dev.config-overrides.php")
        with open(overrides, "a") as f:
            f.write("// my ERP is off\n")
        git(d, "add", "-A")
        git(d, "commit", "-qm", "v2 leftovers")
        p = self.run_wizard()
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        tracked = git(d, "ls-files").splitlines()
        self.assertNotIn("deploy/infra/envs/dev/main.tf", tracked)   # as generated: removed
        self.assertIn("deploy/infra/modules/ecr/main.tf", tracked)    # edited: kept and reported
        self.assertIn("edited by hand (kept", p.stdout)
        with open(overrides) as f:
            self.assertIn("// my ERP is off", f.read())               # seed-once: never rewritten
        self.assertNotIn("deploy/targets/dev.config-overrides.php", helpers.read_json(manifest_path)["files"])
        self.assertEqual(self.info()["status"], "current")

    def test_production_flag(self):
        p = self.run_wizard(PRESET_PIPELINE_ENV="prod", PRESET_PIPELINE_DOMAIN="www.shop.test",
                            PRESET_PIPELINE_PRODUCTION="yes")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        with open(os.path.join(self.mdir, "deploy/targets/prod.env")) as f:
            self.assertIn("PRODUCTION=yes", f.read())
        self.assertEqual(helpers.read_json(os.path.join(self.mdir, "deploy/pipeline.json"))["targets"]["prod"]["production"], "yes")

    def test_refuses_secrets_in_git(self):
        helpers.write(os.path.join(self.mdir, "app", "etc", "env.php"), "<?php return [];\n")
        git(self.mdir, "add", "-f", "app/etc/env.php")
        git(self.mdir, "commit", "-qm", "oops")
        p = self.run_wizard()
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("app/etc/env.php is committed", p.stdout + p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.mdir, "deploy")))

    def test_pin_drift_is_reported(self):
        self.assertEqual(self.run_wizard().returncode, 0)
        with open(os.path.join(self.inst, ".env"), "a") as f:
            f.write("PHP_VERSION=8.5\n")
        i = self.info()
        self.assertFalse(i["pins"]["inSync"])
        self.assertEqual([x["key"] for x in i["pins"]["diff"]], ["PHP_VERSION"])


if __name__ == "__main__":
    unittest.main()


class SecretScan(unittest.TestCase):
    """bin/lib/secret-scan.py — the check before the pipeline wizard commits."""

    SCAN = os.path.join(helpers.REPO, "bin", "lib", "secret-scan.py")

    def scan(self, *paths):
        return subprocess.run(["python3", self.SCAN] + list(paths), capture_output=True, text=True)

    def test_flags_credentials_without_printing_them(self):
        d = tempfile.mkdtemp(prefix="scan-")
        try:
            bad = {
                "a.env": "AWS_ACCESS_KEY_ID=%s\n" % fakes.AWS_KEY,
                "b.ini": "aws_secret_access_key = %s\n" % fakes.AWS_SECRET,
                "c.pem": fakes.PRIVATE_KEY_HEADER + "\nMIIE\n",
                "d.php": "'crypt' => ['key' => '%s'],\n" % fakes.CRYPT_KEY,
                "e.json": '{"http-basic": {"repo.magento.com": {"username": "%s"}}}\n' % fakes.MAGENTO_REPO_KEY,
                "f.sh": "TOKEN=%s\n" % fakes.GITHUB_TOKEN,
            }
            for name, text in bad.items():
                helpers.write(os.path.join(d, name), text)
                p = self.scan(os.path.join(d, name))
                self.assertEqual(p.returncode, 1, name)
                self.assertIn(name + ":", p.stdout)
                for secret in (fakes.AWS_KEY, fakes.AWS_SECRET, fakes.CRYPT_KEY, fakes.MAGENTO_REPO_KEY, fakes.GITHUB_TOKEN):
                    self.assertNotIn(secret, p.stdout)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_the_repo_holds_no_credential_shaped_literals(self):
        # Fake values are built at run time (tests/fakes.py), so GitHub's
        # secret scanning has nothing to flag; only the detector's own regex
        # lines may look like one.
        files = subprocess.run(["git", "-C", helpers.REPO, "ls-files"], capture_output=True, text=True,
                               check=True).stdout.split("\n")
        files = [os.path.join(helpers.REPO, f) for f in files if f and os.path.isfile(os.path.join(helpers.REPO, f))]
        p = self.scan(*files)
        self.assertEqual(p.returncode, 0, p.stdout)

    def test_kit_templates_are_clean(self):
        files = subprocess.run(["git", "-C", helpers.REPO, "ls-files", "pipeline/templates"], capture_output=True,
                               text=True, check=True).stdout.split()
        p = self.scan(*[os.path.join(helpers.REPO, f) for f in files])
        self.assertEqual(p.returncode, 0, p.stdout)
