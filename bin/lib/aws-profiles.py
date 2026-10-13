#!/usr/bin/env python3
"""Which AWS account each MageDevKit connection reaches, and whether it works.

  aws-profiles.py [NAME …]   → JSON list, one entry per connection (all when
                               no NAME is given):
      {"name", "type", "region", "status", "account", "arn", "alias", "error"}
  status: ok | sso-expired | bad-keys | denied | unreachable | error

Runs `aws sts get-caller-identity` (and `iam list-account-aliases`, which may
be refused — the alias is then blank) for every connection at once, each with
a 10 s limit, through MageDevKit's own AWS files only (MDK_AWS_DIR, see
bin/lib/aws.sh): any AWS_* credentials in this process's environment are
dropped first, so a stray exported key can never answer for a connection.
Nothing secret is printed. Standard library only.
"""

import concurrent.futures
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
creds = __import__("aws-credentials")  # the hyphenated sibling: list and paths

TIMEOUT = 10
DROP = re.compile(r"^AWS_(ACCESS_KEY_ID|SECRET_ACCESS_KEY|SESSION_TOKEN|SECURITY_TOKEN|PROFILE|DEFAULT_PROFILE|"
                  r"CONFIG_FILE|SHARED_CREDENTIALS_FILE|CREDENTIAL_EXPIRATION|ROLE_ARN|WEB_IDENTITY_TOKEN_FILE)$")


def aws_env(name, region):
    _, cfg, cred = creds.paths()
    env = {k: v for k, v in os.environ.items() if not DROP.match(k)}
    env.update(AWS_CONFIG_FILE=cfg, AWS_SHARED_CREDENTIALS_FILE=cred, AWS_PROFILE=name, AWS_PAGER="",
               AWS_RETRY_MODE="standard", AWS_MAX_ATTEMPTS="2")
    if region:
        env["AWS_REGION"] = region
    return env


def classify(err):
    e = err.lower()
    if "sso" in e and ("expired" in e or "token" in e or "login" in e):
        return "sso-expired"
    if "invalidclienttokenid" in e or "signaturedoesnotmatch" in e or "unable to locate credentials" in e:
        return "bad-keys"
    if "accessdenied" in e or "not authorized" in e:
        return "denied"
    if "could not connect" in e or "timed out" in e or "name resolution" in e or "connect timeout" in e:
        return "unreachable"
    return "error"


def short(err):
    """The CLI's last meaningful line, without anything key-shaped."""
    lines = [l.strip() for l in err.splitlines() if l.strip()]
    line = lines[-1] if lines else ""
    line = re.sub(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b", "[hidden]", line)
    return line[:300]


def run(args, env):
    try:
        p = subprocess.run(["aws"] + args + ["--output", "json"], env=env, capture_output=True, text=True,
                           timeout=TIMEOUT, stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return None, "Connect timeout: no answer from AWS in %d s." % TIMEOUT
    except OSError:
        return None, "The AWS CLI isn't installed."
    if p.returncode != 0:
        return None, p.stderr
    try:
        return json.loads(p.stdout or "{}"), ""
    except ValueError:
        return None, "Unexpected answer from the AWS CLI."


def check(conn):
    out = dict(conn, status="error", account="", arn="", alias="", error="")
    env = aws_env(conn["name"], conn.get("region", ""))
    who, err = run(["sts", "get-caller-identity"], env)
    if who is None:
        out.update(status=classify(err), error=short(err))
        return out
    account = str(who.get("Account", ""))
    out.update(status="ok", account=account if creds.ACCOUNT_RE.match(account) else "", arn=str(who.get("Arn", "")))
    aliases, _ = run(["iam", "list-account-aliases"], env)
    if aliases and aliases.get("AccountAliases"):
        out["alias"] = str(aliases["AccountAliases"][0])[:63]
    return out


def main(argv):
    _, cfg_path, cred_path = creds.paths()
    cfg, cr = creds.read(cfg_path), creds.read(cred_path)
    conns = [creds.describe(n, cfg, cr) for n in creds.profile_names(cfg, cr)]
    if argv:
        wanted = set(argv)
        conns = [c for c in conns if c["name"] in wanted]
        missing = wanted - {c["name"] for c in conns}
        if missing:
            sys.stderr.write("No MageDevKit connection called %s.\n" % ", ".join(sorted(missing)))
            return 1
    if not conns:
        print("[]")
        return 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, len(conns))) as pool:
        result = list(pool.map(check, conns))
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
