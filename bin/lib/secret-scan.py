#!/usr/bin/env python3
"""Credential-shaped values in files about to be committed — the pipeline
wizard's last check before it commits the kit (a Magento repo holds no
credentials, ever).

  secret-scan.py FILE …   → one line per finding, "FILE:LINE: what", exit 1;
                            nothing and exit 0 when the files are clean

Only values that look like real credentials are reported (an AWS access key,
a 40-character AWS secret after its key name, a private key block, a GitHub
token, a 32-character Magento crypt key or repo.magento.com key), so the
kit's scripts — full of `DB_PASSWORD=$(…)` and `${…}` — never trip it. The
value itself is never printed. Standard library only.
"""

import re
import sys

CHECKS = [
    ("an AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("an AWS secret access key", re.compile(r"(?i)aws_?secret_?access_?key[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}\b")),
    ("a private key", re.compile(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b")),
    ("a Magento crypt key", re.compile(r"['\"]key['\"]\s*=>\s*['\"][A-Za-z0-9]{32,}['\"]")),
    ("a repo.magento.com key", re.compile(r"\"(?:username|password)\"\s*:\s*\"[0-9a-f]{32}\"")),
]


def scan(path):
    found = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, 1):
                for what, rx in CHECKS:
                    if rx.search(line):
                        found.append("%s:%d: %s" % (path, n, what))
    except OSError:
        pass
    return found


def main(paths):
    found = [x for p in paths for x in scan(p)]
    for x in found:
        print(x)
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
