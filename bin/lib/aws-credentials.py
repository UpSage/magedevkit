#!/usr/bin/env python3
"""MageDevKit's own AWS connections: the config and credentials files under
~/.config/magedevkit/aws (MDK_AWS_DIR), never ~/.aws.

  aws-credentials.py list                     connections as JSON (no secrets)
  aws-credentials.py set NAME < answers.json  add or replace one connection
  aws-credentials.py remove NAME              delete one connection
  aws-credentials.py sources [CONFIG CREDS]   profiles in another AWS config
                                              (default ~/.aws) as JSON, no secrets
  aws-credentials.py import NAME CONFIG CREDS copy that one profile (and the
                                              login it needs) in, unchanged
  aws-credentials.py sso-accounts NAME        after `aws sso login`: the accounts
                                              and roles the SSO login may use
  aws-credentials.py remember ACCOUNT NAME    on this computer, account ACCOUNT is
                                              reached through connection NAME
  aws-credentials.py which ACCOUNT            that connection (blank if none)

A deploy kit records the AWS account, never a connection name: which of your
connections reaches it is this computer's own choice, kept in
~/.config/magedevkit/accounts.json (0600) — not in a store's .env or repo.

`set` reads its answers as one JSON object on stdin, so a secret never
reaches a command line:
  {"type": "keys", "region": …, "access_key_id": …, "secret_access_key": …}
  {"type": "sso", "region": …, "sso_start_url": …, "sso_region": …,
   "sso_account_id": … (optional until picked), "sso_role_name": …}
  {"type": "role", "region": …, "role_arn": …, "source_profile": …,
   "mfa_serial": … (optional)}

These files belong to MageDevKit alone (bin/lib/aws.sh points the AWS CLI at
them per call), so they're rewritten whole: 0600 in a 0700 folder, the
previous copy kept as <file>.bak-<time> (the newest 5). Errors are one line
on stderr and exit 1. Standard library only.
"""

import configparser
import datetime
import glob
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request

NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
REGION_RE = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]*)?-[a-z]+-[0-9]$")
KEY_ID_RE = re.compile(r"^AKIA[A-Z0-9]{16}$")  # long-term keys only (ASIA needs a session token)
SECRET_RE = re.compile(r"^[A-Za-z0-9/+=]{40}$")
ACCOUNT_RE = re.compile(r"^[0-9]{12}$")
ROLE_NAME_RE = re.compile(r"^[\w+=,.@-]{1,64}$")
ROLE_ARN_RE = re.compile(r"^arn:aws[a-z-]*:iam::[0-9]{12}:role/[\w+=,.@/-]{1,512}$")
MFA_RE = re.compile(r"^arn:aws[a-z-]*:iam::[0-9]{12}:mfa/[\w+=,.@/-]{1,256}$")
START_URL_RE = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/#-]*)?$")
KEEP_BACKUPS = 5


class Refused(Exception):
    pass


def aws_dir():
    d = os.environ.get("MDK_AWS_DIR") or os.path.join(
        os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"), "magedevkit", "aws")
    return os.path.abspath(d)


def accounts_path():
    return os.path.join(os.path.dirname(aws_dir()), "accounts.json")


def read_accounts():
    try:
        with open(accounts_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if ACCOUNT_RE.match(k) and isinstance(v, str) and NAME_RE.match(v)} \
        if isinstance(data, dict) else {}


def cmd_remember(account, name):
    data = read_accounts()
    data[account] = name
    path = accounts_path()
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(data.items())), f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def paths():
    d = aws_dir()
    return d, os.path.join(d, "config"), os.path.join(d, "credentials")


def parser():
    p = configparser.RawConfigParser(default_section="\x00none")  # no [DEFAULT] magic
    p.optionxform = str
    return p


def read(path):
    p = parser()
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            p.read_file(f)
    return p


def section(name):
    """The config file's section for profile NAME ("default" has no prefix)."""
    return "default" if name == "default" else "profile " + name


def profile_names(cfg, creds):
    names = set(creds.sections())
    for s in cfg.sections():
        if s == "default":
            names.add("default")
        elif s.startswith("profile "):
            names.add(s[len("profile "):])
    return sorted(names)


def describe(name, cfg, creds):
    """One connection without its secrets."""
    c = dict(cfg.items(section(name))) if cfg.has_section(section(name)) else {}
    has_keys = creds.has_section(name) and creds.has_option(name, "aws_access_key_id")
    if c.get("sso_session") or c.get("sso_start_url"):
        kind = "sso"
    elif c.get("role_arn"):
        kind = "role"
    elif has_keys:
        kind = "keys"
    else:
        kind = "unknown"
    out = {"name": name, "type": kind, "region": c.get("region", "")}
    if kind == "sso":
        sess = "sso-session " + c.get("sso_session", "")
        s = dict(cfg.items(sess)) if cfg.has_section(sess) else {}
        out.update(ssoStartUrl=s.get("sso_start_url", c.get("sso_start_url", "")),
                   ssoAccountId=c.get("sso_account_id", ""), ssoRoleName=c.get("sso_role_name", ""))
    elif kind == "role":
        out.update(roleArn=c.get("role_arn", ""), sourceProfile=c.get("source_profile", ""),
                   mfa=bool(c.get("mfa_serial")))
    elif kind == "keys":
        key = creds.get(name, "aws_access_key_id")
        out["keyId"] = key[:4] + "…" + key[-4:] if len(key) > 8 else "…"
    return out


def write(path, p):
    """Rewrites PATH with P: 0600, the old file kept as a dated backup."""
    d = os.path.dirname(path)
    os.makedirs(d, mode=0o700, exist_ok=True)
    os.chmod(d, 0o700)
    if os.path.isfile(path):
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = "%s.bak-%s" % (path, stamp)
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out, open(path, encoding="utf-8") as src:
            out.write(src.read())
        for old in sorted(glob.glob(glob.escape(path) + ".bak-*"))[:-KEEP_BACKUPS]:
            os.remove(old)
    buf = io.StringIO()
    p.write(buf)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(buf.getvalue())
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def need(answers, key, rx, what):
    v = str(answers.get(key) or "").strip()
    if not rx.match(v):
        raise Refused("%s isn't valid." % what)
    return v


def drop(name, cfg, creds):
    """Removes NAME (and an sso-session nothing else uses) from both files."""
    sec = section(name)
    sess = cfg.get(sec, "sso_session", fallback="") if cfg.has_section(sec) else ""
    cfg.remove_section(sec)
    creds.remove_section(name)
    if sess and not any(cfg.get(s, "sso_session", fallback="") == sess for s in cfg.sections()):
        cfg.remove_section("sso-session " + sess)


def cmd_set(name, answers):
    _, cfg_path, creds_path = paths()
    cfg, creds = read(cfg_path), read(creds_path)
    kind = answers.get("type")
    region = need(answers, "region", REGION_RE, "The region")
    new = {"region": region, "output": "json"}
    new_creds = None
    if kind == "keys":
        new_creds = {"aws_access_key_id": need(answers, "access_key_id", KEY_ID_RE, "The access key ID (AKIA…, 20 characters)"),
                     "aws_secret_access_key": need(answers, "secret_access_key", SECRET_RE, "The secret access key (40 characters)")}
    elif kind == "sso":
        start = need(answers, "sso_start_url", START_URL_RE, "The SSO start URL (https://…)")
        sso_region = need(answers, "sso_region", REGION_RE, "The SSO region")
        new["sso_session"] = name
        if answers.get("sso_account_id") or answers.get("sso_role_name"):
            new["sso_account_id"] = need(answers, "sso_account_id", ACCOUNT_RE, "The account number (12 digits)")
            new["sso_role_name"] = need(answers, "sso_role_name", ROLE_NAME_RE, "The role name")
    elif kind == "role":
        new["role_arn"] = need(answers, "role_arn", ROLE_ARN_RE, "The role ARN (arn:aws:iam::<account>:role/<name>)")
        src = need(answers, "source_profile", NAME_RE, "The connection to sign in with")
        if src == name:
            raise Refused("A role can't sign in with itself.")
        if src not in profile_names(cfg, creds):
            raise Refused("There's no MageDevKit connection called '%s' to sign in with." % src)
        new["source_profile"] = src
        if answers.get("mfa_serial"):
            new["mfa_serial"] = need(answers, "mfa_serial", MFA_RE, "The MFA device ARN")
    else:
        raise Refused("Choose access keys, SSO or a role.")
    drop(name, cfg, creds)
    cfg.add_section(section(name))
    for k, v in new.items():
        cfg.set(section(name), k, v)
    if kind == "sso":
        cfg.add_section("sso-session " + name)
        cfg.set("sso-session " + name, "sso_start_url", start)
        cfg.set("sso-session " + name, "sso_region", sso_region)
        cfg.set("sso-session " + name, "sso_registration_scopes", "sso:account:access")
    if new_creds:
        creds.add_section(name)
        for k, v in new_creds.items():
            creds.set(name, k, v)
    write(cfg_path, cfg)
    write(creds_path, creds)
    return describe(name, cfg, creds)


def cmd_remove(name):
    _, cfg_path, creds_path = paths()
    cfg, creds = read(cfg_path), read(creds_path)
    if name not in profile_names(cfg, creds):
        raise Refused("There's no MageDevKit connection called '%s'." % name)
    users = [n for n in profile_names(cfg, creds)
             if cfg.get(section(n), "source_profile", fallback="") == name]
    if users:
        raise Refused("'%s' is used to sign in by %s — remove or change those first." % (name, ", ".join(users)))
    drop(name, cfg, creds)
    write(cfg_path, cfg)
    write(creds_path, creds)


def default_sources():
    home = os.path.expanduser("~")
    return (os.environ.get("MDK_IMPORT_CONFIG") or os.path.join(home, ".aws", "config"),
            os.environ.get("MDK_IMPORT_CREDENTIALS") or os.path.join(home, ".aws", "credentials"))


def chain(name, cfg, creds, seen=None):
    """NAME plus every profile it signs in with (source_profile), in order."""
    seen = seen or []
    if name in seen:
        raise Refused("'%s' signs in through a loop of profiles." % name)
    seen.append(name)
    src = cfg.get(section(name), "source_profile", fallback="") if cfg.has_section(section(name)) else ""
    if src:
        chain(src, cfg, creds, seen)
    return seen


def cmd_import(name, src_cfg_path, src_creds_path):
    src_cfg, src_creds = read(src_cfg_path), read(src_creds_path)
    if name not in profile_names(src_cfg, src_creds):
        raise Refused("There's no profile '%s' in %s." % (name, os.path.dirname(src_cfg_path)))
    _, cfg_path, creds_path = paths()
    cfg, creds = read(cfg_path), read(creds_path)
    names = chain(name, src_cfg, src_creds)
    for n in names:
        if not NAME_RE.match(n):
            raise Refused("'%s' isn't a usable connection name." % n)
        if n in profile_names(cfg, creds):
            raise Refused("MageDevKit already has a connection called '%s' — remove it first." % n)
    for n in names:
        sec = section(n)
        if src_cfg.has_section(sec):
            cfg.add_section(sec)
            for k, v in src_cfg.items(sec):
                if k in ("credential_process", "credential_source"):
                    raise Refused("'%s' gets its keys from %s, which MageDevKit doesn't import." % (n, k))
                cfg.set(sec, k, v)
            sess = src_cfg.get(sec, "sso_session", fallback="")
            if sess and not cfg.has_section("sso-session " + sess):
                cfg.add_section("sso-session " + sess)
                for k, v in src_cfg.items("sso-session " + sess):
                    cfg.set("sso-session " + sess, k, v)
        if src_creds.has_section(n):
            creds.add_section(n)
            for k, v in src_creds.items(n):
                creds.set(n, k, v)
    write(cfg_path, cfg)
    write(creds_path, creds)
    return [describe(n, cfg, creds) for n in names]


def sso_token(name):
    """The SSO login's access token from the AWS CLI's cache (written by
    `aws sso login`). Kept in memory only."""
    cfg = read(paths()[1])
    sess = cfg.get(section(name), "sso_session", fallback="")
    if not sess:
        raise Refused("'%s' isn't an SSO connection." % name)
    cache = os.path.join(os.path.expanduser("~"), ".aws", "sso", "cache",
                         hashlib.sha1(sess.encode("utf-8")).hexdigest() + ".json")
    try:
        with open(cache, encoding="utf-8") as f:
            tok = json.load(f)
    except (OSError, ValueError):
        raise Refused("Not signed in — sign in to SSO first.")
    expires = str(tok.get("expiresAt", ""))
    try:
        when = datetime.datetime.fromisoformat(expires.replace("Z", "+00:00"))
        if when <= datetime.datetime.now(datetime.timezone.utc):
            raise Refused("The SSO sign-in has expired — sign in again.")
    except ValueError:
        pass
    region = cfg.get("sso-session " + sess, "sso_region", fallback="")
    if not REGION_RE.match(region) or not tok.get("accessToken"):
        raise Refused("Not signed in — sign in to SSO first.")
    return tok["accessToken"], region


def portal(region, path, token, params):
    url = "https://portal.sso.%s.amazonaws.com%s?%s" % (region, path, urllib.parse.urlencode(params))
    req = urllib.request.Request(url, headers={"x-amz-sso_bearer_token": token})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def cmd_sso_accounts(name):
    """The accounts and roles this SSO login may use (the same portal API the
    AWS CLI calls; the token never reaches a command line)."""
    token, region = sso_token(name)
    out = []
    try:
        params = {"max_result": "100"}
        while True:
            page = portal(region, "/assignment/accounts", token, params)
            for a in page.get("accountList", []):
                roles, rp = [], {"account_id": a.get("accountId", ""), "max_result": "100"}
                while True:
                    r = portal(region, "/assignment/roles", token, rp)
                    roles += [x.get("roleName", "") for x in r.get("roleList", [])]
                    if not r.get("nextToken"):
                        break
                    rp["next_token"] = r["nextToken"]
                out.append({"accountId": a.get("accountId", ""), "name": a.get("accountName", ""),
                            "roles": sorted(x for x in roles if ROLE_NAME_RE.match(x))})
            if not page.get("nextToken"):
                break
            params["next_token"] = page["nextToken"]
    except (OSError, ValueError) as e:
        raise Refused("Couldn't list the SSO accounts (%s)." % getattr(e, "reason", e.__class__.__name__))
    return [a for a in out if ACCOUNT_RE.match(a["accountId"])]


def main(argv):
    try:
        if len(argv) == 1 and argv[0] == "list":
            _, cfg_path, creds_path = paths()
            cfg, creds = read(cfg_path), read(creds_path)
            print(json.dumps([describe(n, cfg, creds) for n in profile_names(cfg, creds)]))
        elif len(argv) == 2 and argv[0] == "set" and NAME_RE.match(argv[1]):
            try:
                answers = json.loads(sys.stdin.read() or "{}")
            except ValueError:
                raise Refused("The answers aren't valid JSON.")
            if not isinstance(answers, dict):
                raise Refused("The answers aren't valid JSON.")
            print(json.dumps(cmd_set(argv[1], answers)))
        elif len(argv) == 2 and argv[0] == "remove" and NAME_RE.match(argv[1]):
            cmd_remove(argv[1])
        elif argv[:1] == ["sources"] and len(argv) in (1, 3):
            cfg_path, creds_path = argv[1:3] if len(argv) == 3 else default_sources()
            cfg, creds = read(cfg_path), read(creds_path)
            print(json.dumps([describe(n, cfg, creds) for n in profile_names(cfg, creds) if NAME_RE.match(n)]))
        elif len(argv) == 4 and argv[0] == "import" and NAME_RE.match(argv[1]):
            print(json.dumps(cmd_import(argv[1], argv[2], argv[3])))
        elif len(argv) == 3 and argv[0] == "remember" and ACCOUNT_RE.match(argv[1]) and NAME_RE.match(argv[2]):
            cmd_remember(argv[1], argv[2])
        elif len(argv) == 2 and argv[0] == "which" and ACCOUNT_RE.match(argv[1]):
            name = read_accounts().get(argv[1], "")
            _, cfg_path, creds_path = paths()
            if name and name in profile_names(read(cfg_path), read(creds_path)):
                print(name)
        elif len(argv) == 2 and argv[0] == "sso-accounts" and NAME_RE.match(argv[1]):
            print(json.dumps(cmd_sso_accounts(argv[1])))
        else:
            raise Refused("Usage: aws-credentials.py list | set NAME | remove NAME | sources [CONFIG CREDS] | "
                          "import NAME CONFIG CREDS | sso-accounts NAME | remember ACCOUNT NAME | which ACCOUNT")
    except Refused as e:
        sys.stderr.write("%s\n" % e)
        return 1
    except (OSError, configparser.Error) as e:
        sys.stderr.write("Couldn't read or write the AWS files (%s).\n" % e.__class__.__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
