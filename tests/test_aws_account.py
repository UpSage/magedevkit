"""MageDevKit's own AWS connections (bin/lib/aws-credentials.py,
bin/lib/aws-profiles.py, bin/lib/aws.sh, bin/local/aws-account) against a
fake `aws` — no network, and never the real ~/.aws."""

import json
import os
import shutil
import stat
import subprocess
import tempfile
import unittest

import fakes
import helpers

LIB = os.path.join(helpers.REPO, "bin", "lib")
CREDS = os.path.join(LIB, "aws-credentials.py")
PROFILES = os.path.join(LIB, "aws-profiles.py")
ACCOUNT = os.path.join(helpers.REPO, "bin", "local", "aws-account")
KEY = fakes.AWS_KEY
SECRET = fakes.AWS_SECRET

# Records the environment each call got (the login it could have used), then
# answers like the AWS CLI for the few commands MageDevKit runs.
FAKE_AWS = r"""#!/bin/bash
{
  printf 'argv=%s\n' "$*"
  printf 'AWS_PROFILE=%s AWS_CONFIG_FILE=%s AWS_SHARED_CREDENTIALS_FILE=%s\n' "${AWS_PROFILE-unset}" "${AWS_CONFIG_FILE-unset}" "${AWS_SHARED_CREDENTIALS_FILE-unset}"
  printf 'AWS_ACCESS_KEY_ID=%s AWS_SECRET_ACCESS_KEY=%s\n' "${AWS_ACCESS_KEY_ID-unset}" "${AWS_SECRET_ACCESS_KEY-unset}"
} >> "$FAKE_AWS_LOG"
case "$AWS_PROFILE:$*" in
  expired:sts\ get-caller-identity*) echo "Error when retrieving token from sso: Token has expired and refresh failed" >&2; exit 255 ;;
  badkeys:sts\ get-caller-identity*) echo "An error occurred (InvalidClientTokenId) when calling the GetCallerIdentity operation" >&2; exit 254 ;;
  *:sts\ get-caller-identity*--query\ Account*) echo 485849269510 ;;
  *:sts\ get-caller-identity*) echo '{"Account": "485849269510", "Arn": "arn:aws:iam::485849269510:user/me", "UserId": "AIDA"}' ;;
  *:iam\ list-account-aliases*) echo '{"AccountAliases": ["acme-dev"]}' ;;
  *) echo "unexpected: $*" >&2; exit 2 ;;
esac
"""


class AwsConnections(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="aws-account-")
        self.home = os.path.join(self.d, "home")
        self.mdk = os.path.join(self.home, ".config", "magedevkit", "aws")
        self.bin = os.path.join(self.d, "bin")
        self.log = os.path.join(self.d, "aws.log")
        helpers.write(os.path.join(self.bin, "aws"), FAKE_AWS, 0o755)
        # The person's own ~/.aws: MageDevKit must never change it.
        self.user_cfg = os.path.join(self.home, ".aws", "config")
        self.user_creds = os.path.join(self.home, ".aws", "credentials")
        helpers.write(self.user_cfg, "[default]\nregion = us-east-2\n\n[profile shop-s3]\nregion = eu-west-1\n\n"
                                     "[profile deployer]\nrole_arn = arn:aws:iam::111122223333:role/Deploy\n"
                                     "source_profile = default\nregion = us-east-2\n\n"
                                     "[profile helper]\ncredential_process = /usr/bin/get-keys\n", 0o600)
        helpers.write(self.user_creds, "[default]\naws_access_key_id = %s\naws_secret_access_key = %s\n\n"
                                       "[shop-s3]\naws_access_key_id = %s\n"
                                       "aws_secret_access_key = %s\n" % (KEY, SECRET, fakes.AWS_KEY_SHOP, "s" * 40), 0o600)
        self.user_before = (helpers.read(self.user_cfg), helpers.read(self.user_creds))

    def tearDown(self):
        # Whatever a test did, the person's own AWS files are untouched.
        self.assertEqual((helpers.read(self.user_cfg), helpers.read(self.user_creds)), self.user_before)
        shutil.rmtree(self.d, ignore_errors=True)

    def env(self, **extra):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_") and k != "MDK_AWS_DIR"}
        env.update(HOME=self.home, XDG_CONFIG_HOME=os.path.join(self.home, ".config"), FAKE_AWS_LOG=self.log,
                   PATH=self.bin + os.pathsep + os.environ["PATH"],
                   UI_LOG_FILE=os.path.join(self.d, "run.log"))  # the run log stays here, not in the repo's logs/
        env.update(extra)
        return env

    def creds(self, *args, stdin="", **extra):
        return subprocess.run(["python3", CREDS] + list(args), input=stdin, capture_output=True, text=True,
                              env=self.env(**extra))

    def add_keys(self, name="acme", region="us-east-2"):
        p = self.creds("set", name, stdin=json.dumps({"type": "keys", "region": region, "access_key_id": KEY,
                                                      "secret_access_key": SECRET}))
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    # --- aws-credentials.py -------------------------------------------------

    def test_keys_go_only_into_mageDevKits_own_files(self):
        out = self.add_keys()
        self.assertEqual(out["type"], "keys")
        self.assertNotIn(SECRET, json.dumps(out))
        self.assertNotIn(KEY, json.dumps(out))  # only a masked hint of the key id
        cfg, cred = os.path.join(self.mdk, "config"), os.path.join(self.mdk, "credentials")
        self.assertIn("[profile acme]", helpers.read(cfg))
        self.assertIn(SECRET, helpers.read(cred))
        self.assertNotIn(SECRET, helpers.read(cfg))
        for f in (cfg, cred):
            self.assertEqual(stat.S_IMODE(os.stat(f).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.mdk).st_mode), 0o700)
        listed = self.creds("list")
        self.assertEqual([c["name"] for c in json.loads(listed.stdout)], ["acme"])
        self.assertNotIn(SECRET, listed.stdout)

    def test_bad_answers_are_refused_and_nothing_is_written(self):
        for answers in ({"type": "keys", "region": "us-east-2", "access_key_id": "nope", "secret_access_key": SECRET},
                        {"type": "keys", "region": "mars-1", "access_key_id": KEY, "secret_access_key": SECRET},
                        {"type": "keys", "region": "us-east-2", "access_key_id": KEY, "secret_access_key": "short"},
                        {"type": "role", "region": "us-east-2", "role_arn": "arn:aws:iam::1:role/x", "source_profile": "a"},
                        {"type": "sso", "region": "us-east-2", "sso_start_url": "http://insecure", "sso_region": "us-east-1"},
                        {"type": "magic", "region": "us-east-2"}):
            p = self.creds("set", "acme", stdin=json.dumps(answers))
            self.assertEqual(p.returncode, 1, answers)
            self.assertNotIn(SECRET, p.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.mdk, "credentials")))
        self.assertEqual(self.creds("set", "../evil", stdin="{}").returncode, 1)

    def test_role_needs_an_existing_connection_and_blocks_its_removal(self):
        p = self.creds("set", "deploy", stdin=json.dumps({"type": "role", "region": "us-east-2",
                                                          "role_arn": "arn:aws:iam::111122223333:role/Deploy",
                                                          "source_profile": "acme"}))
        self.assertEqual(p.returncode, 1)
        self.assertIn("no MageDevKit connection called 'acme'", p.stderr)
        self.add_keys()
        p = self.creds("set", "deploy", stdin=json.dumps({"type": "role", "region": "us-east-2",
                                                          "role_arn": "arn:aws:iam::111122223333:role/Deploy",
                                                          "source_profile": "acme"}))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(p.stdout)["roleArn"], "arn:aws:iam::111122223333:role/Deploy")
        p = self.creds("remove", "acme")
        self.assertEqual(p.returncode, 1)
        self.assertIn("used to sign in by deploy", p.stderr)
        self.assertEqual(self.creds("remove", "deploy").returncode, 0)
        self.assertEqual(self.creds("remove", "acme").returncode, 0)
        self.assertNotIn(SECRET, helpers.read(os.path.join(self.mdk, "credentials")))

    def test_sso_writes_a_session_and_no_keys(self):
        p = self.creds("set", "corp", stdin=json.dumps({"type": "sso", "region": "eu-west-1",
                                                        "sso_start_url": "https://corp.awsapps.com/start",
                                                        "sso_region": "us-east-1", "sso_account_id": "111122223333",
                                                        "sso_role_name": "AdministratorAccess"}))
        self.assertEqual(p.returncode, 0, p.stderr)
        cfg = helpers.read(os.path.join(self.mdk, "config"))
        self.assertIn("[sso-session corp]", cfg)
        self.assertIn("sso_session = corp", cfg)
        self.assertNotIn("[corp]", helpers.read(os.path.join(self.mdk, "credentials")))
        self.assertEqual(self.creds("remove", "corp").returncode, 0)
        self.assertNotIn("sso-session corp", helpers.read(os.path.join(self.mdk, "config")))

    def test_backups_are_private_and_capped(self):
        for i in range(8):
            self.add_keys(region=["us-east-1", "us-east-2"][i % 2])
        backups = [f for f in os.listdir(self.mdk) if f.startswith("credentials.bak-")]
        self.assertEqual(len(backups), 5)
        for f in backups:
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.mdk, f)).st_mode), 0o600)

    def test_import_copies_one_profile_and_its_sign_in_only(self):
        p = self.creds("sources")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn(SECRET, p.stdout)
        self.assertEqual({c["name"] for c in json.loads(p.stdout)}, {"default", "shop-s3", "deployer", "helper"})
        p = self.creds("import", "deployer", self.user_cfg, self.user_creds)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual([c["name"] for c in json.loads(p.stdout)], ["deployer", "default"])
        mine = helpers.read(os.path.join(self.mdk, "credentials"))
        self.assertIn(SECRET, mine)
        self.assertNotIn(fakes.AWS_KEY_SHOP, mine)  # the store's S3 profile stays where it is
        again = self.creds("import", "deployer", self.user_cfg, self.user_creds)
        self.assertEqual(again.returncode, 1)
        self.assertIn("already has a connection", again.stderr)
        refused = self.creds("import", "helper", self.user_cfg, self.user_creds)
        self.assertEqual(refused.returncode, 1)
        self.assertIn("credential_process", refused.stderr)

    # --- aws-profiles.py and bin/lib/aws.sh -----------------------------------

    def test_status_uses_only_mageDevKits_files_never_exported_keys(self):
        self.add_keys()
        self.creds("set", "expired", stdin=json.dumps({"type": "sso", "region": "us-east-1",
                                                       "sso_start_url": "https://corp.awsapps.com/start",
                                                       "sso_region": "us-east-1"}))
        p = subprocess.run(["python3", PROFILES], capture_output=True, text=True,
                           env=self.env(AWS_ACCESS_KEY_ID=fakes.AWS_KEY_STRAY, AWS_SECRET_ACCESS_KEY="stray",
                                        AWS_PROFILE="default"))
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = {r["name"]: r for r in json.loads(p.stdout)}
        self.assertEqual((rows["acme"]["status"], rows["acme"]["account"], rows["acme"]["alias"]),
                         ("ok", "485849269510", "acme-dev"))
        self.assertEqual(rows["expired"]["status"], "sso-expired")
        log = helpers.read(self.log)
        self.assertNotIn(fakes.AWS_KEY_STRAY, log)
        self.assertNotIn("AWS_PROFILE=default", log)
        self.assertNotIn(os.path.join(self.home, ".aws"), log)
        self.assertIn("AWS_CONFIG_FILE=%s" % os.path.join(self.mdk, "config"), log)

    def test_mdk_aws_requires_the_recorded_account(self):
        self.add_keys()
        script = 'source "%s/ui.sh"; source "%s/util.sh"; source "%s/aws.sh"; mdk_aws_require_account acme "$1"' % (LIB, LIB, LIB)
        ok = subprocess.run(["bash", "-c", script, "x", "485849269510"], capture_output=True, text=True,
                            env=self.env(AWS_ACCESS_KEY_ID=fakes.AWS_KEY_STRAY))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        wrong = subprocess.run(["bash", "-c", script, "x", "111122223333"], capture_output=True, text=True,
                               env=self.env())
        self.assertEqual(wrong.returncode, 1)
        self.assertIn("reaches account 485849269510, not 111122223333", wrong.stderr)
        self.assertNotIn(fakes.AWS_KEY_STRAY, helpers.read(self.log))

    # --- bin/local/aws-account ------------------------------------------------

    def account(self, *args, **extra):
        return subprocess.run([ACCOUNT] + list(args), capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env=self.env(**extra))

    def test_list_test_and_remove(self):
        p = self.account("list")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("No MageDevKit AWS connections yet", p.stdout)
        self.add_keys()
        p = self.account("list")
        self.assertIn("acme", p.stdout)
        self.assertIn("485849269510", p.stdout)
        self.assertNotIn(SECRET, p.stdout + p.stderr)
        p = self.account("test", "acme")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("account 485849269510 (acme-dev)", p.stdout)
        p = self.account("remove", "acme")  # no terminal, no --yes: refused
        self.assertEqual(p.returncode, 1)
        self.assertIn("needs a yes", p.stderr)
        p = self.account("remove", "acme", "--yes")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(json.loads(self.creds("list").stdout), [])

    def test_bad_names_and_actions_are_refused(self):
        for args in (["test", "../x"], ["login"], ["frobnicate"], ["remove", "a b"]):
            p = self.account(*args)
            self.assertNotEqual(p.returncode, 0, args)

    def test_import_without_a_person_imports_nothing(self):
        p = self.account("import")
        self.assertNotEqual(p.returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(self.mdk, "credentials")))


if __name__ == "__main__":
    unittest.main()


class AwsAccountsPage(unittest.TestCase):
    """The dashboard's /api/aws/accounts against the same fake `aws`."""

    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="aws-page-")
        cls.bin = os.path.join(cls.d, "bin")
        cls.log = os.path.join(cls.d, "aws.log")
        helpers.write(os.path.join(cls.bin, "aws"), FAKE_AWS, 0o755)
        helpers.write(os.path.join(cls.bin, "docker"), "#!/bin/sh\nexit 1\n", 0o755)
        # A key exported in the shell that starts the dashboard: never used.
        cls.dash = helpers.Dashboard(cls.d, cls.d, cls.bin, {"FAKE_AWS_LOG": cls.log, "AWS_ACCESS_KEY_ID": fakes.AWS_KEY_STRAY,
                                                            "AWS_SECRET_ACCESS_KEY": "stray-secret", "AWS_PROFILE": "default"})
        cls.mdk = os.path.join(cls.dash.tmp, "config", "magedevkit", "aws")
        for name in ("acme", "spare"):
            p = subprocess.run(["python3", CREDS, "set", name], capture_output=True, text=True,
                               input=json.dumps({"type": "keys", "region": "us-east-2", "access_key_id": KEY,
                                                 "secret_access_key": SECRET}),
                               env=dict(os.environ, MDK_AWS_DIR=cls.mdk))
            assert p.returncode == 0, p.stderr

    @classmethod
    def tearDownClass(cls):
        cls.dash.stop()
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_lists_and_checks_without_secrets(self):
        def checked():
            code, body, _ = self.dash.request("GET", "/api/aws/accounts")
            return body if code == 200 and body["check"]["status"] == "done" else None
        body = helpers.wait_for(checked)
        self.assertIsNotNone(body, "the check didn't finish")
        rows = {c["name"]: c for c in body["connections"]}
        self.assertEqual(set(rows), {"acme", "spare"})
        self.assertEqual((rows["acme"]["status"], rows["acme"]["account"], rows["acme"]["alias"]), ("ok", "485849269510", "acme-dev"))
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn(KEY, json.dumps(body))
        log = helpers.read(self.log)
        self.assertNotIn(fakes.AWS_KEY_STRAY, log)
        self.assertNotIn("AWS_PROFILE=default", log)

    def test_bad_requests(self):
        self.assertEqual(self.dash.request("POST", "/api/aws/accounts/wizard", {"action": "rm"})[0], 400)
        self.assertEqual(self.dash.request("POST", "/api/aws/accounts/wizard", {"action": "login", "name": "../x"})[0], 400)
        self.assertEqual(self.dash.request("POST", "/api/aws/accounts/remove", {"name": "a;b"})[0], 400)
        self.assertEqual(self.dash.request("POST", "/api/aws/accounts/remove", {"name": "nope"})[0], 404)
        self.assertEqual(self.dash.request("POST", "/api/aws/accounts/remove", {"name": "acme"}, headers={"X-Wizard": None})[0], 403)
        self.assertEqual(self.dash.request("GET", "/api/aws/accounts", cookie=False)[0], 403)

    def test_remove(self):
        code, body, _ = self.dash.request("POST", "/api/aws/accounts/remove", {"name": "spare"})
        self.assertEqual(code, 200, body)
        code, body, _ = self.dash.request("GET", "/api/aws/accounts")
        self.assertNotIn("spare", {c["name"] for c in body["connections"]})
