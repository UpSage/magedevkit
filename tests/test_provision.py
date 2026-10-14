"""The provision wizard (bin/local/provision + bin/lib/provision-engine.sh)
run for real against a fake `aws` that keeps a little state (the stack, the
SSM parameters, the change set) — no AWS. Covers what keeps an account
safe: the connection must reach the kit's account, secrets never reach a
command line or the log, existing secrets are kept, a change that replaces
the box needs the typed name, an unapplied change set leaves nothing behind,
and removing an environment needs its typed name (twice for production)."""

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
PROVISION = os.path.join(helpers.REPO, "bin", "local", "provision")
PIPELINE = os.path.join(helpers.REPO, "bin", "local", "pipeline")
ACCOUNT = "123456789012"

FAKE_AWS = r"""#!/bin/bash
# Fake AWS CLI: answers by argv, keeps state in $FAKE_STATE.
[ "$1" = --region ] && shift 2
printf '%s\n' "$*" >> "$FAKE_STATE/calls.log"
st="$FAKE_STATE"
case "$*" in
  "sts get-caller-identity"*) echo "${FAKE_ACCOUNT:-123456789012}" ;;
  "cloudformation describe-stacks"*StackStatus*) [ -f "$st/stack" ] && cat "$st/stack" || exit 254 ;;
  "cloudformation describe-stacks"*magedevkit:app*) [ -f "$st/stack" ] && printf 'shop\tdev\n' || exit 254 ;;
  "cloudformation describe-stacks"*EnableTerminationProtection*) cat "$st/protected" 2>/dev/null || echo False ;;
  "cloudformation describe-stacks"*PipelineName*) echo shop-dev ;;
  "cloudformation describe-stacks"*InstanceId*) echo i-0abc ;;
  "cloudformation describe-stacks"*OperatorPolicyArn*) echo arn:aws:iam::123456789012:policy/shop-dev-operator ;;
  "cloudformation describe-stacks"*ArtifactBucket*) echo shop-dev-artifacts ;;
  "cloudformation create-change-set"*)
    case "$*" in *"--change-set-type CREATE"*) echo REVIEW_IN_PROGRESS > "$st/stack" ;; esac
    echo arn:aws:cloudformation:cs/1 ;;
  "cloudformation wait change-set-create-complete"*) [ -n "$FAKE_NO_CHANGES" ] && exit 255; exit 0 ;;
  "cloudformation describe-change-set"*StatusReason*) echo "The submitted information didn't contain changes." ;;
  "cloudformation describe-change-set"*)
    if [ -n "$FAKE_DANGER" ]; then
      echo '{"Changes": [{"ResourceChange": {"Action": "Modify", "LogicalResourceId": "Box", "ResourceType": "AWS::EC2::Instance", "Replacement": "True"}}]}'
    else
      echo '{"Changes": [{"ResourceChange": {"Action": "Add", "LogicalResourceId": "Vpc", "ResourceType": "AWS::EC2::VPC"}}, {"ResourceChange": {"Action": "Add", "LogicalResourceId": "Box", "ResourceType": "AWS::EC2::Instance"}}]}'
    fi ;;
  "cloudformation execute-change-set"*) echo CREATE_COMPLETE > "$st/stack" ;;
  "cloudformation delete-change-set"*) : ;;
  "cloudformation describe-stack-events"*)
    echo '{"StackEvents": [{"EventId": "e1", "ResourceStatus": "CREATE_COMPLETE", "LogicalResourceId": "mdk-shop-dev", "ResourceType": "AWS::CloudFormation::Stack"}]}' ;;
  "cloudformation update-termination-protection"*) : ;;
  "cloudformation delete-stack"*) rm -f "$st/stack" ;;
  "route53 get-hosted-zone"*) echo shop.test. ;;
  "codestar-connections get-connection"*) echo AVAILABLE ;;
  "ec2 describe-instance-type-offerings"*) echo t3.large ;;
  "ec2 describe-images"*) echo ami-0123456789abcdef0 ;;
  "service-quotas get-service-quota"*) echo 5.0 ;;
  "ec2 describe-addresses"*) echo 0 ;;
  "ec2 describe-vpcs"*) echo 1 ;;
  "s3api head-bucket"*) : ;;
  "ssm describe-parameters"*) cat "$st/params" 2>/dev/null | tr '\n' '\t' ;;
  "ssm put-parameter --cli-input-json file://"*)
    a="$*"; f="${a#*file://}"; f="${f%% *}"
    printf 'mode=%s\n' "$(stat -c %a "$f")" >> "$st/calls.log"
    python3 -c 'import json, sys; d = json.load(open(sys.argv[1])); print(d["Name"]); open(sys.argv[2], "a").write(d["Value"] + "\n")' \
      "$f" "$st/values" >> "$st/params" ;;
  "ssm get-parameter --name /shop/dev/status"*) echo '{"state": "awaiting-database"}' ;;
  "budgets"*) exit 255 ;;
  "codepipeline get-pipeline-state"*) printf 'Source\tSucceeded\nBuild\tSucceeded\nDeploy\tSucceeded\n' ;;
  "s3api list-objects-v2"*) echo "dumps/latest.sql.gz" ;;
  "ssm send-command"*) echo cmd-1 ;;
  "ssm get-command-invocation"*) echo Success ;;
  "s3 rm"*|"ssm delete-parameters"*) : ;;
  *) echo "fake aws: unexpected $*" >&2; exit 2 ;;
esac
"""

PINS = ("PHP_VERSION=8.4\nMARIADB_VERSION=12.3\nOPENSEARCH_VERSION=3\nVALKEY_VERSION=9\nRABBITMQ_VERSION=4.3\n"
        "VARNISH_VERSION=8\nNGINX_VERSION=1.30\nCOMPOSER_VERSION=2.10\nDEBIAN_RELEASE=trixie\nMAGENTO_VERSION=2.4.9\n")
KIT_PRESETS = {
    "PRESET_PIPELINE_ENV": "dev", "PRESET_PIPELINE_APP": "shop", "PRESET_PIPELINE_ACCOUNT": ACCOUNT,
    "PRESET_PIPELINE_REGION": "us-east-2", "PRESET_PIPELINE_ZONE": "shop.test", "PRESET_PIPELINE_ZONE_ID": "Z0123456789ABCDEFGHIJ",
    "PRESET_PIPELINE_DOMAIN": "dev.shop.test", "PRESET_PIPELINE_INSTANCE_TYPE": "t3.large", "PRESET_PIPELINE_OFF_HOURS": "no",
    "PRESET_PIPELINE_BRANCH": "development", "PRESET_PIPELINE_GITHUB_REPO": "Acme/shop",
    "PRESET_PIPELINE_GITHUB_CONNECTION": "arn:aws:codestar-connections:us-east-2:123456789012:connection/abc-123",
    "PRESET_PIPELINE_MODE": "production", "PRESET_PIPELINE_ACME_EMAIL": "ops@shop.test", "PRESET_PIPELINE_STATIC_LOCALES": "en_US",
    "PRESET_PIPELINE_IMAGE_ID": "ami-0123456789abcdef0", "PRESET_PIPELINE_PRODUCTION": "no",
    "PRESET_PIPELINE_DUMP_BUCKET": "shop-dumps", "PRESET_PIPELINE_COMMIT": "yes", "PRESET_PIPELINE_BUILD_TEST": "no",
    "PRESET_PIPELINE_CONNECTION": "nonexistent",
}
RUN_PRESETS = {
    "PRESET_PROVISION_CONNECTION": "acme", "PRESET_PROVISION_CRYPT_KEY": "new", "PRESET_PROVISION_FOLLOW": "yes",
    "PRESET_PROVISION_BUDGET": "no", "PRESET_PROVISION_DUMP": "s3://shop-dumps/dumps/latest.sql.gz",
    "PRESET_PROVISION_CONFIRM": "dev", "PROVISION_POLL": "0",
}


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=True).stdout.strip()


class Provision(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # The store and its kit are made once (the pipeline wizard is slow);
        # each test gets a fresh copy.
        cls.base = os.path.realpath(tempfile.mkdtemp(prefix="provision-base-"))
        inst = os.path.join(cls.base, "shop")
        helpers.write(os.path.join(inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % helpers.REPO)
        helpers.write(os.path.join(inst, ".env"),
                      "APP_NAME=shop\nAPP_DOMAIN=shop.test\nAPP_TIMEZONE=UTC\nMAGENTO_MODE=developer\n"
                      "MAGENTO_ADMIN_URI=backend\nFILE_OWNERSHIP=single\n" + PINS)
        mdir = os.path.join(inst, "magento")
        helpers.write(os.path.join(mdir, "composer.json"), '{"name": "acme/shop"}\n')
        helpers.write(os.path.join(mdir, "app", "etc", "config.php"),
                      "<?php return ['modules' => [], 'scopes' => ['websites' => []]];\n")
        helpers.write(os.path.join(mdir, ".gitignore"), "/auth.json\n")
        subprocess.run(["git", "init", "-q", "-b", "development", mdir], check=True)
        git(mdir, "config", "user.name", "t")
        git(mdir, "config", "user.email", "t@t")
        git(mdir, "remote", "add", "origin", "https://github.com/Acme/shop.git")
        git(mdir, "add", "-A")
        git(mdir, "commit", "-qm", "store")
        # The repo.magento.com key, as for a local composer install (never committed).
        helpers.write(os.path.join(mdir, "auth.json"), json.dumps(
            {"http-basic": {"repo.magento.com": {"username": fakes.MAGENTO_REPO_KEY, "password": "f" * 32}}}))
        env = dict(cls.clean_env(), PLATFORM_INSTANCE_DIR=inst, XDG_CONFIG_HOME=os.path.join(cls.base, "config"), **KIT_PRESETS)
        p = subprocess.run([PIPELINE], env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
        assert p.returncode == 0, p.stdout + p.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    @staticmethod
    def clean_env():
        env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        for k in ("MAGENTO_DIR", "ENV_FILE", "SETUP_UI", "UI_FLOW", "WIZARD_LOCK_HELD") + tuple(
                k for k in os.environ if k.startswith("AWS_")):
            env.pop(k, None)
        return env

    def setUp(self):
        self.tmp = os.path.realpath(tempfile.mkdtemp(prefix="provision-"))
        self.state = os.path.join(self.tmp, "aws-state")
        os.makedirs(self.state)
        bin_ = os.path.join(self.tmp, "bin")
        helpers.write(os.path.join(bin_, "aws"), FAKE_AWS, 0o755)
        self.inst = os.path.join(self.tmp, "shop")
        shutil.copytree(os.path.join(self.base, "shop"), self.inst, symlinks=True)
        self.mdir = os.path.join(self.inst, "magento")
        self.env = dict(self.clean_env(), PLATFORM_INSTANCE_DIR=self.inst, PATH=bin_ + os.pathsep + os.environ["PATH"],
                        XDG_CONFIG_HOME=os.path.join(self.tmp, "config"), FAKE_STATE=self.state)
        # MageDevKit's connection "acme" to the kit's account (fake keys; the fake aws accepts them).
        p = subprocess.run(["python3", os.path.join(LIB, "aws-credentials.py"), "set", "acme"], env=self.env, text=True,
                           capture_output=True, input=json.dumps({"type": "keys", "region": "us-east-2",
                                                                   "access_key_id": fakes.AWS_KEY,
                                                                   "secret_access_key": fakes.AWS_SECRET}))
        assert p.returncode == 0, p.stderr

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_provision(self, *args, **extra):
        env = dict(self.env, **RUN_PRESETS)
        env.update(extra)
        env = {k: v for k, v in env.items() if v is not None}
        return subprocess.run([PROVISION] + list(args), env=env, stdin=subprocess.DEVNULL, capture_output=True,
                              text=True, timeout=300)

    def calls(self):
        return helpers.read(os.path.join(self.state, "calls.log")) if os.path.exists(os.path.join(self.state, "calls.log")) else ""

    def test_creates_the_environment(self):
        p = self.run_provision("dev")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.calls()
        self.assertIn("--change-set-type CREATE", calls)
        self.assertIn("--capabilities CAPABILITY_NAMED_IAM", calls)
        self.assertIn("Key=magedevkit:app,Value=shop Key=magedevkit:env,Value=dev", calls)
        self.assertIn("cloudformation execute-change-set", calls)
        names = helpers.read(os.path.join(self.state, "params")).split()
        self.assertEqual(sorted(names), ["/shop/dev/composer_auth", "/shop/dev/crypt_key", "/shop/dev/db_password",
                                         "/shop/dev/db_root_password", "/shop/dev/rabbitmq_password"])
        # Secrets: made by provision.py, handed over in a private file, never on a command line or in the log.
        self.assertEqual(calls.count("mode=600"), 5)
        for value in helpers.read(os.path.join(self.state, "values")).split():
            self.assertNotIn(value, calls)
            self.assertNotIn(value, p.stdout + p.stderr)
        self.assertNotIn(fakes.MAGENTO_REPO_KEY, calls + p.stdout + p.stderr)
        self.assertIn('"command": ["refresh"], "arg": ["s3://shop-dumps/dumps/latest.sql.gz"]', calls)
        self.assertIn("--document-name shop-dev-box", calls)
        self.assertIn("Database loaded", p.stdout)
        self.assertNotIn("update-termination-protection", calls)  # not production

        # Again: secrets kept, nothing to change, nothing executed.
        os.remove(os.path.join(self.state, "calls.log"))
        p = self.run_provision("dev", FAKE_NO_CHANGES="1")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.calls()
        self.assertNotIn("put-parameter", calls)
        self.assertNotIn("execute-change-set", calls)
        self.assertIn("already matches the kit", p.stdout)

    def test_wrong_account_changes_nothing(self):
        p = self.run_provision("dev", FAKE_ACCOUNT="999999999999")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("reaches account 999999999999, not 123456789012", p.stderr)
        calls = self.calls()
        for change in ("put-parameter", "create-change-set", "execute-change-set"):
            self.assertNotIn(change, calls)

    def test_replacing_the_box_needs_the_typed_name(self):
        p = self.run_provision("dev", FAKE_DANGER="1", PRESET_PROVISION_CONFIRM=None)
        self.assertNotEqual(p.returncode, 0)
        calls = self.calls()
        self.assertNotIn("execute-change-set", calls)
        # The unapplied change set and the empty stack it created are cleaned up.
        self.assertIn("cloudformation delete-change-set", calls)
        self.assertIn("cloudformation delete-stack", calls)
        p = self.run_provision("dev", FAKE_DANGER="1", PRESET_PROVISION_CONFIRM="staging")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("execute-change-set", self.calls())

    def test_an_edited_kit_is_refused(self):
        with open(os.path.join(self.mdir, "deploy", "compose", "edge.yml"), "a") as f:
            f.write("# mine\n")
        git(self.mdir, "commit", "-qam", "tweak")
        p = self.run_provision("dev")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("changed by hand", p.stderr)
        self.assertNotIn("create-change-set", self.calls())

    def test_destroy_needs_the_typed_name(self):
        helpers.write(os.path.join(self.state, "stack"), "CREATE_COMPLETE\n")
        p = self.run_provision("--destroy", "dev", PRESET_PROVISION_CONFIRM=None)
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("delete-stack", self.calls())
        p = self.run_provision("--destroy", "dev", PRESET_PROVISION_DELETE_SECRETS="no")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        calls = self.calls()
        self.assertIn("s3 rm s3://shop-dev-artifacts --recursive", calls)
        self.assertIn("cloudformation delete-stack --stack-name mdk-shop-dev", calls)
        self.assertNotIn("delete-parameters", calls)

    def test_destroying_production_needs_a_second_confirmation(self):
        helpers.write(os.path.join(self.state, "stack"), "CREATE_COMPLETE\n")
        helpers.write(os.path.join(self.state, "protected"), "True\n")
        p = self.run_provision("--destroy", "dev")
        self.assertNotEqual(p.returncode, 0)
        self.assertNotIn("delete-stack", self.calls())
        p = self.run_provision("--destroy", "dev", PRESET_PROVISION_CONFIRM_PRODUCTION="production-dev",
                               PRESET_PROVISION_DELETE_SECRETS="no")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertIn("--no-enable-termination-protection", self.calls())


class Secrets(unittest.TestCase):
    """bin/lib/provision.py secret — values made or read here, written to a 0600 file."""

    def secret(self, kind, source=""):
        d = tempfile.mkdtemp(prefix="secret-")
        out = os.path.join(d, "s.json")
        p = subprocess.run(["python3", os.path.join(LIB, "provision.py"), "secret", kind, "/shop/dev/x", out, source],
                           capture_output=True, text=True)
        try:
            if p.returncode:
                return p, None
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
            self.assertEqual(p.stdout, "")  # never printed
            with open(out) as f:
                return p, json.load(f)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_kinds(self):
        _, doc = self.secret("password")
        self.assertRegex(doc["Value"], r"^[A-Za-z0-9]{32}$")
        self.assertEqual(doc["Type"], "SecureString")
        _, doc = self.secret("cryptkey")
        self.assertRegex(doc["Value"], r"^[0-9a-f]{32}$")
        d = tempfile.mkdtemp(prefix="src-")
        try:
            env_php = os.path.join(d, "env.php")
            helpers.write(env_php, "<?php return ['crypt' => ['key' => '%s'], 'db' => []];\n" % fakes.CRYPT_KEY)
            _, doc = self.secret("envphp", env_php)
            self.assertEqual(doc["Value"], fakes.CRYPT_KEY)
            bad = os.path.join(d, "auth.json")
            helpers.write(bad, '{"http-basic": {}}')
            p, doc = self.secret("authjson", bad)
            self.assertEqual(p.returncode, 1)
            self.assertIn("no repo.magento.com key", p.stderr)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        p, _ = self.secret("password", "")
        p = subprocess.run(["python3", os.path.join(LIB, "provision.py"), "secret", "password", "/etc/passwd", "/tmp/x"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)


if __name__ == "__main__":
    unittest.main()
