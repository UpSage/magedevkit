#!/usr/bin/env python3
"""Helpers for the provision wizard (bin/lib/provision-engine.sh) — the parts
that are JSON, or must keep a secret out of bash and off command lines.

  provision.py secret KIND NAME OUT [SOURCE] [TAGS_JSON]
      Writes `aws ssm put-parameter --cli-input-json file://OUT` input for the
      SecureString NAME into OUT (0600). The value is made or read here and
      never printed:
        password   32 letters and digits (safe in .env and in env.php quotes)
        cryptkey   32 hex characters, a new Magento crypt key
        envphp     the crypt key from SOURCE, a Magento app/etc/env.php
        authjson   the contents of SOURCE, an auth.json with a
                   repo.magento.com key (COMPOSER_AUTH format)
  provision.py changes      < describe-change-set JSON
      "ACTION<TAB>LOGICAL<TAB>TYPE<TAB>REPLACEMENT" per change, then
      "danger<TAB>LOGICAL…" naming resources whose removal or replacement
      loses the box's data (empty when there are none).
  provision.py events SEEN  < describe-stack-events JSON
      New events, oldest first, as one line each; their ids are added to the
      file SEEN so the next poll prints only what is new.
  provision.py cost INSTANCE_TYPE VOLUME_GB OFF_HOURS(yes|no)
      A rough monthly estimate in USD (on-demand prices, us-east), e.g. "71".

Standard library only.
"""

import json
import os
import re
import secrets
import string
import sys

# Losing or replacing these loses the box — and the database on its disk.
DATA_RESOURCES = {"Box", "BoxEip", "Vpc", "PublicSubnet", "BoxLaunchTemplate"}
# On-demand Linux, USD per hour (us-east-1/2) — an estimate, not a quote.
HOURLY = {"t3.medium": 0.0416, "t3.large": 0.0832, "t3.xlarge": 0.1664, "t3.2xlarge": 0.3328,
          "m6i.large": 0.096, "m6i.xlarge": 0.192, "m7i.large": 0.1008, "c6i.large": 0.085,
          "r6i.large": 0.126}
ALNUM = string.ascii_letters + string.digits


class Refused(Exception):
    pass


def make_secret(kind, source):
    if kind == "password":
        return "".join(secrets.choice(ALNUM) for _ in range(32))
    if kind == "cryptkey":
        return secrets.token_hex(16)
    if kind == "envphp":
        try:
            with open(source, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            raise Refused("Couldn't read %s." % source)
        m = re.search(r"""['"]crypt['"]\s*=>\s*\[\s*['"]key['"]\s*=>\s*['"]([^'"]+)['"]""", text)
        if not m:
            raise Refused("No crypt key found in %s." % source)
        return m.group(1)
    if kind == "authjson":
        try:
            with open(source, encoding="utf-8") as f:
                auth = json.load(f)
        except (OSError, ValueError):
            raise Refused("%s isn't readable JSON." % source)
        repo = ((auth.get("http-basic") or {}).get("repo.magento.com") or {}) if isinstance(auth, dict) else {}
        if not repo.get("username") or not repo.get("password"):
            raise Refused("%s has no repo.magento.com key (http-basic)." % source)
        return json.dumps(auth, separators=(",", ":"))
    raise Refused("Unknown secret kind %s." % kind)


def cmd_secret(kind, name, out, source="", tags="[]"):
    if not re.match(r"^/[a-z][a-z0-9-]{1,23}/[a-z][a-z0-9-]{1,15}/[a-z_]{1,40}$", name):
        raise Refused("%s isn't a parameter name the kit uses." % name)
    value = make_secret(kind, source)
    doc = {"Name": name, "Type": "SecureString", "Value": value,
           "Description": "MageDevKit (provision wizard). Read by the box and the build; never printed."}
    tag_list = json.loads(tags)
    if tag_list:
        doc["Tags"] = tag_list
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)


def cmd_changes(data):
    danger = []
    for c in data.get("Changes", []):
        rc = c.get("ResourceChange", {})
        action, logical = rc.get("Action", ""), rc.get("LogicalResourceId", "")
        replacement = rc.get("Replacement", "") or ""
        print("%s\t%s\t%s\t%s" % (action, logical, rc.get("ResourceType", ""), replacement))
        if logical in DATA_RESOURCES and (action == "Remove" or replacement in ("True", "Conditional")):
            danger.append(logical)
    print("danger\t%s" % " ".join(danger))


def cmd_events(data, seen_path):
    try:
        with open(seen_path, encoding="utf-8") as f:
            seen = set(f.read().split())
    except OSError:
        seen = set()
    new = [e for e in data.get("StackEvents", []) if e.get("EventId") not in seen]
    with open(seen_path, "a", encoding="utf-8") as f:
        for e in new:
            f.write(e.get("EventId", "") + "\n")
    for e in reversed(new):
        reason = e.get("ResourceStatusReason") or ""
        print("%-28s %-40s %s%s" % (e.get("ResourceStatus", ""), e.get("LogicalResourceId", ""),
                                    e.get("ResourceType", ""), (" — " + reason) if reason else ""))


def cmd_cost(instance_type, volume_gb, off_hours):
    hours = 730.0 * ((13 * 5) / 168.0 if off_hours == "yes" else 1.0)
    box = HOURLY.get(instance_type)
    if box is None:
        print("")
        return
    total = box * hours + 0.08 * float(volume_gb) + 3.65 + 5  # disk, Elastic IP, logs/S3/builds
    print("%d" % round(total))


def main(argv):
    try:
        if argv[:1] == ["secret"] and 4 <= len(argv) <= 6:
            cmd_secret(*argv[1:])
        elif argv == ["changes"]:
            cmd_changes(json.load(sys.stdin))
        elif argv[:1] == ["events"] and len(argv) == 2:
            cmd_events(json.load(sys.stdin), argv[1])
        elif argv[:1] == ["cost"] and len(argv) == 4:
            cmd_cost(*argv[1:])
        else:
            raise Refused("Usage: provision.py secret KIND NAME OUT [SOURCE] [TAGS] | changes | events SEEN | cost TYPE GB OFF")
    except Refused as e:
        sys.stderr.write("%s\n" % e)
        return 1
    except ValueError:
        sys.stderr.write("Unexpected answer from AWS (not JSON).\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
