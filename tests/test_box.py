"""The pipeline kit's scripts for the EC2 box (pipeline/templates/deploy/
scripts/) run here against a fake box: /etc/m2deploy.env replaced through
M2DEPLOY_ENV, fake `docker` and `aws`. Covers what MageDevKit relies on:
box refuses arguments it doesn't allow, publishes a status without secrets,
and render-env locks the non-production safety settings into env.php."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers

KIT = os.path.join(helpers.REPO, "pipeline", "templates", "deploy")

FAKE_AWS = r"""#!/bin/bash
printf '%s\n' "$*" >> "$FAKE_LOG"
case "$*" in
  "ssm get-parameters-by-path"*)
    printf '/shop/dev/db_password\tdbpw-1234567\n/shop/dev/db_root_password\trootpw-1234567\n'
    printf '/shop/dev/rabbitmq_password\trbpw-1234567\n/shop/dev/crypt_key\tcryptkey-1234567\n' ;;
esac
exit 0
"""
FAKE_DOCKER = """#!/bin/bash
printf 'docker %s\\n' "$*" >> "$FAKE_LOG"
exit 0
"""


class Box(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="box-")
        self.home = os.path.join(self.d, "opt", "shop")
        self.log = os.path.join(self.d, "calls.log")
        bin_ = os.path.join(self.d, "bin")
        helpers.write(os.path.join(bin_, "aws"), FAKE_AWS, 0o755)
        helpers.write(os.path.join(bin_, "docker"), FAKE_DOCKER, 0o755)
        helpers.write(os.path.join(self.d, "m2deploy.env"),
                      "APP=shop\nENV_NAME=dev\nAWS_REGION=us-east-2\nAPP_HOME=%s\n" % self.home)
        # The deployed deploy/ folder: the kit's scripts plus a rendered target.
        shutil.copytree(os.path.join(KIT, "scripts"), os.path.join(self.home, "deploy", "scripts"))
        helpers.write(os.path.join(self.home, "deploy", "pipeline.json"), '{"templateVersion": 3}\n')
        helpers.write(os.path.join(self.home, "shared", ".env"), "APP_NAME=shop\nDB_PASSWORD=dbpw-1234567\n")
        helpers.write(os.path.join(self.home, "shared", "release.env"), "COMMIT=abc123\nIMAGE_TAG=s-1\n")
        self.target(production="no")
        self.env = dict(os.environ, PATH=bin_ + os.pathsep + os.environ["PATH"], FAKE_LOG=self.log,
                        M2DEPLOY_ENV=os.path.join(self.d, "m2deploy.env"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def target(self, production):
        text = helpers.read(os.path.join(KIT, "targets", "target.env"))
        values = {"ENV_NAME": "dev", "APP": "shop", "AWS_REGION": "us-east-2", "APP_DOMAIN": "dev.shop.test",
                  "ACME_EMAIL": "ops@shop.test", "MAGENTO_MODE": "production", "ADMIN_URI": "backend",
                  "APP_TIMEZONE": "UTC", "STATIC_LOCALES": "en_US", "STORAGE_DRIVER": "file", "MEDIA_BUCKET": "",
                  "MEDIA_REGION": "", "PRODUCTION": production, "DUMP_BUCKET": "shop-dumps", "HEADER": "test"}
        for k, v in values.items():
            text = text.replace("@@%s@@" % k, v)
        helpers.write(os.path.join(self.home, "deploy", "targets", "dev.env"), text)

    def box(self, *args):
        return subprocess.run([os.path.join(self.home, "deploy", "scripts", "box")] + list(args), env=self.env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL)

    def test_refuses_what_it_doesnt_allow(self):
        for args in (["nope"], ["restart", "php;id"], ["restart", "sshd"], ["reindex", "a b"],
                     ["refresh", "https://example.com/dump.sql.gz"], ["refresh", "s3://bucket/../etc/passwd"],
                     ["refresh", ""], ["logs", "../../etc/shadow"], ["maintenance", "maybe"]):
            p = self.box(*args)
            self.assertNotEqual(p.returncode, 0, args)
        self.assertNotIn("docker compose", helpers.read(self.log) if os.path.exists(self.log) else "")

    def test_status_json(self):
        helpers.write(os.path.join(self.home, "shared", "awaiting-database"), "")
        p = self.box("status", "--json")
        self.assertEqual(p.returncode, 0, p.stderr)
        st = json.loads(p.stdout)
        self.assertEqual((st["awaitingDatabase"], st["mode"], st["production"], st["containers"]),
                         (True, "production", "no", []))
        self.assertNotIn("dbpw", p.stdout)

    def test_publish_status_without_secrets(self):
        script = 'source "%s/scripts/lib/common.sh"; publish_status ok' % os.path.join(self.home, "deploy")
        p = subprocess.run(["bash", "-c", script], env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        call = [l for l in helpers.read(self.log).splitlines() if l.startswith("ssm put-parameter")]
        self.assertEqual(len(call), 1)
        self.assertIn("--name /shop/dev/status --type String --overwrite", call[0])
        value = json.loads(call[0].split("--value ", 1)[1])
        self.assertEqual((value["state"], value["commit"], value["kit"], value["mode"], value["production"]),
                         ("ok", "abc123", "3", "production", "no"))
        self.assertNotIn("dbpw", call[0])

    def render(self):
        p = subprocess.run([os.path.join(self.home, "deploy", "scripts", "render-env")], env=self.env,
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertNotIn("dbpw", p.stdout + p.stderr)  # secrets never printed
        self.assertEqual(oct(os.stat(os.path.join(self.home, "shared", "env.php")).st_mode & 0o777), "0o600")
        return helpers.read(os.path.join(self.home, "shared", "env.php"))

    def test_non_production_safety_block(self):
        php = self.render()
        self.assertIn("'system' => ['smtp' => ['disable' => '1'], 'full_page_cache'", php)  # one 'system' key
        self.assertIn("'default_robots' => 'NOINDEX,NOFOLLOW'", php)
        self.assertIn("'sandbox_flag' => '1'", php)
        self.assertIn("require __DIR__ . '/env.overrides.php'", php)
        self.target(production="yes")
        php = self.render()
        self.assertNotIn("smtp", php)
        self.assertNotIn("NOINDEX", php)
        self.assertIn("'full_page_cache'", php)


if __name__ == "__main__":
    unittest.main()
