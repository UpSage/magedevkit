#!/usr/bin/env python3
"""Magento releases from Adobe, for the setup and version wizards' menus.

Adobe's system requirements data file (the one bin/m2/check-requirements
compares the catalog with) lists every Magento Open Source / Commerce
release, patches included, with the PHP, MariaDB, OpenSearch … versions it
supports. This keeps a copy of it and, for each release 2.4.8 or later that
has no folder in the m2/ catalog yet:

  * on a line the catalog has (m2/2.4.8/ for 2.4.8-p6): takes the stack
    versions of the newest catalogued release on that line, checks every one
    against Adobe's list for the new release (the same rule as
    check-requirements' satisfies), and — only when all pass — writes
    m2/<base>/pN/version.env under the cache ("derived"), which
    bin/lib/m2-catalog.sh offers next to the repo's own folders;
  * otherwise: "published" only, with the reason.

A folder in the repo's m2/ always wins over a derived one.

  releases.py refresh [--if-stale] [--timeout N] --catalog DIR
  releases.py list --tsv --catalog DIR

Cache: ${XDG_CACHE_HOME:-~/.cache}/platform-releases/ — releases.json (0600)
and the derived m2/ tree, written whole and swapped in under a lock. Offline,
refresh keeps the old data, records the error, prints a note and exits 0, so
the wizards look exactly like they do without this.

Standard library only, and no imports from this repo: bash runs it with
python3 -I (which leaves the script's folder off the import path); the
dashboard imports it as a module.
"""

import argparse
import datetime
import fcntl
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.request

ADOBE_URL = "https://raw.githubusercontent.com/AdobeDocs/commerce-operations.en/main/_jekyll/_data/system-requirements.yml"
MAX_BYTES = 2 * 1024 * 1024
STALE_AFTER = 24 * 3600
RETRY_AFTER = 3600         # after a failed download, don't slow every wizard run down
MIN_VERSION = (2, 4, 8)
RELEASE_RE = re.compile(r"^(\d+\.\d+\.\d+(?:-p\d+)?)(?: \(.*\))?:\s*$")
REQ_RE = re.compile(r"^  ([^:]+):\s*(.*?)\s*$")
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:-p(\d+))?$")
# Adobe service name → version.env key (bin/m2/check-requirements SERVICES).
SERVICES = (("PHP", "PHP_VERSION"), ("MariaDB", "MARIADB_VERSION"), ("OpenSearch", "OPENSEARCH_VERSION"),
            ("Valkey", "VALKEY_VERSION"), ("RabbitMQ", "RABBITMQ_VERSION"), ("Varnish", "VARNISH_VERSION"),
            ("nginx", "NGINX_VERSION"), ("Composer", "COMPOSER_VERSION"))
# Every stack key a version.env can pin (bin/lib/m2-catalog.sh M2_STACK_KEYS).
STACK_KEYS = tuple(k for _, k in SERVICES) + ("DEBIAN_RELEASE",)


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "platform-releases")


def version_key(v):
    """'2.4.8-p5' → (2, 4, 8, 5); the base release sorts before its patches."""
    m = VERSION_RE.match(v or "")
    return tuple(int(x or 0) for x in m.groups()) if m else (0, 0, 0, 0)


def base_of(v):
    return v.split("-", 1)[0]


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def age(iso):
    try:
        t = datetime.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc).timestamp()
    except (TypeError, ValueError):
        return float("inf")
    return time.time() - t


# ---------------------------------------------------------------------------
# Adobe's data
# ---------------------------------------------------------------------------

def fetch_text(timeout=8):
    """Adobe's file as text. Raises on any network/HTTP error or a body over
    MAX_BYTES."""
    req = urllib.request.Request(ADOBE_URL, headers={"User-Agent": "MageDevKit release check"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("the file is over %d MB" % (MAX_BYTES // 1024 // 1024))
    return body.decode("utf-8")


def parse(text):
    """{version: {service: value}} for 2.4.8 and later. Release lines are
    '2.4.8-p5 (latest):'; requirement lines "  PHP: '8.4, 8.3'"."""
    out, cur = {}, None
    for line in text.splitlines():
        m = RELEASE_RE.match(line)
        if m:
            cur = m.group(1) if version_key(m.group(1))[:3] >= MIN_VERSION else None
            if cur:
                out[cur] = {}
            continue
        if not line.startswith(" "):
            cur = None
            continue
        r = REQ_RE.match(line)
        if cur and r:
            out[cur][r.group(1).strip()] = r.group(2).strip().strip("'\"")
    return out


def satisfies(pin, adobe):
    """A pin meets Adobe's list ("8.4, 8.3", "2.9.3+", "2.19") when it equals
    an entry or one is a dotted prefix of the other (2.19.6 ~ 2.19,
    2.9 ~ 2.9.3+) — bin/m2/check-requirements satisfies()."""
    for item in (adobe or "").split(","):
        item = item.strip().rstrip("+")
        if item and (pin == item or pin.startswith(item + ".") or item.startswith(pin + ".")):
            return True
    return False


# ---------------------------------------------------------------------------
# The repo's m2/ catalog and derived entries
# ---------------------------------------------------------------------------

def read_env(path):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    except OSError:
        pass
    return vals


def catalog(catalog_dir):
    """{version: pins} for every m2/ folder, a patch's values over its base's
    (the way m2_get reads them)."""
    out = {}
    try:
        bases = [b for b in os.listdir(catalog_dir) if VERSION_RE.match(b) and "-" not in b]
    except OSError:
        return out
    for base in bases:
        bdir = os.path.join(catalog_dir, base)
        if not os.path.isfile(os.path.join(bdir, "version.env")):
            continue
        base_vals = read_env(os.path.join(bdir, "version.env"))
        out[base] = base_vals
        for p in os.listdir(bdir):
            if re.match(r"^p\d+$", p) and os.path.isfile(os.path.join(bdir, p, "version.env")):
                out["%s-%s" % (base, p)] = dict(base_vals, **read_env(os.path.join(bdir, p, "version.env")))
    return out


def derive(releases, catalog_dir):
    """Every release Adobe or the catalog knows, newest first:
    [{version, state: catalog|derived|published, from, note, pins}].
    `pins` is set for derived ones: what their version.env gets."""
    cat = catalog(catalog_dir)
    out = []
    for ver in sorted(set(releases) | set(cat), key=version_key, reverse=True):
        if ver in cat:
            out.append({"version": ver, "state": "catalog", "from": None, "note": None, "pins": None})
            continue
        base = base_of(ver)
        older = [v for v in cat if base_of(v) == base and version_key(v) < version_key(ver)]
        if not older:
            out.append({"version": ver, "state": "published", "from": None, "pins": None,
                        "note": "No m2/ catalog line for %s — add a folder (m2/README.md)." % base})
            continue
        src = max(older, key=version_key)
        pins = {k: cat[src][k] for k in STACK_KEYS if cat[src].get(k)}
        failed = []
        for svc, key in SERVICES:
            want = releases[ver].get(svc)
            if not want:
                continue
            if not pins.get(key):
                failed.append("%s isn't pinned in %s" % (svc, src))
            elif not satisfies(pins[key], want):
                failed.append("%s %s isn't in Adobe's list (%s)" % (svc, pins[key], want))
        if failed:
            out.append({"version": ver, "state": "published", "from": src, "pins": None,
                        "note": "Versions from %s don't fit: %s." % (src, "; ".join(failed))})
        else:
            out.append({"version": ver, "state": "derived", "from": src, "pins": pins,
                        "note": "new: versions from %s, checked against Adobe's requirements" % src})
    return out


def write_derived(root, entries, fetched_at):
    """Rebuilds <root>/m2/ from the derived entries: written in a temp folder
    and swapped in, so a release that's now catalogued (or no longer passes)
    disappears."""
    tmp = tempfile.mkdtemp(prefix="m2.", dir=root)
    for e in entries:
        if e["state"] != "derived":
            continue
        base, patch = e["version"].split("-", 1)
        d = os.path.join(tmp, base, patch)
        os.makedirs(d)
        lines = ["# Derived from %s + Adobe's system requirements (fetched %s) by" % (e["from"], fetched_at or "?"),
                 "# bin/lib/web-wizard/releases.py. A folder for this release in m2/ replaces it.",
                 "MAGENTO_VERSION=%s" % e["version"]]
        lines += ["%s=%s" % (k, e["pins"][k]) for k in STACK_KEYS if k in e["pins"]]
        with open(os.path.join(d, "version.env"), "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    final = os.path.join(root, "m2")
    old = None
    if os.path.exists(final):
        old = final + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.rename(final, old)
    os.rename(tmp, final)
    if old:
        shutil.rmtree(old, ignore_errors=True)


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

def load(root=None):
    try:
        with open(os.path.join(root or cache_root(), "releases.json"), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(root, data):
    fd, tmp = tempfile.mkstemp(prefix="releases.", dir=root)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(root, "releases.json"))


def refresh(catalog_dir, if_stale=False, timeout=8, fetch=fetch_text):
    """Downloads Adobe's file (unless --if-stale and the copy is fresh, or a
    download failed in the last hour), then rebuilds the derived entries.
    Returns (data, entries, note) — note is one line for the wizard's log,
    blank when there's nothing to say. Never raises for a network problem."""
    root = cache_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    note = ""
    with open(os.path.join(root, ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load(root)
        known = set(data.get("releases") or {})
        fresh = age(data.get("fetchedAt")) < STALE_AFTER
        recently_failed = data.get("lastError") and age(data.get("lastAttempt")) < RETRY_AFTER
        if not (if_stale and (fresh or recently_failed)):
            data["lastAttempt"] = now_iso()
            try:
                releases = parse(fetch(timeout=timeout))
                if not releases:
                    raise ValueError("no Magento 2.4.8+ releases in it")
                data.update(releases=releases, fetchedAt=now_iso(), lastError=None)
            except Exception as ex:  # any failure keeps the old data
                data["lastError"] = "%s: %s" % (type(ex).__name__, str(ex)[:200])
                note = "Couldn't check Adobe for new Magento releases (%s) — %s." % (
                    data["lastError"], "using the copy from %s" % data["fetchedAt"] if data.get("fetchedAt") else "using the m2/ catalog only")
            save(root, data)
        entries = derive(data.get("releases") or {}, catalog_dir)
        write_derived(root, entries, data.get("fetchedAt"))
    new = [e["version"] for e in entries if e["state"] == "derived" and e["version"] not in known]
    if new and not note:
        note = "New Magento release(s) from Adobe, offered with checked stack versions: %s." % ", ".join(new)
    return data, entries, note


def classify(installed, edition, platform_php, catalog_dir, data=None):
    """What an environment on `installed` could move to: every newer release
    with whether it's compatible with this kit and project, and why not.
      published  — Adobe (or the catalog) lists it and it's newer;
      compatible — catalogued, or derived pins passed; and the project's
                   composer.json config.platform.php (if set) is in Adobe's
                   PHP list for it;
      eligible   — compatible: selectable in the version wizard.
    Commerce (edition "enterprise") releases note the repo.magento.com
    entitlement they need."""
    data = load() if data is None else data
    releases = data.get("releases") or {}
    entries = derive(releases, catalog_dir)
    newer = [e for e in entries if not installed or version_key(e["version"]) > version_key(installed)]
    out = []
    for e in newer:
        reason = None
        if e["state"] == "published":
            reason = e["note"]
        elif platform_php and releases.get(e["version"], {}).get("PHP") \
                and not satisfies(platform_php, releases[e["version"]]["PHP"]):
            reason = "composer.json pins PHP %s (config.platform.php); Adobe lists %s for it." % (
                platform_php, releases[e["version"]]["PHP"])
        out.append({"version": e["version"], "state": e["state"], "compatible": reason is None, "reason": reason,
                    "note": "Needs a repo.magento.com entitlement for Adobe Commerce." if edition == "enterprise" else None})
    eligible = [r["version"] for r in out if r["compatible"]]
    return {
        "installed": installed,
        "latestPublished": entries[0]["version"] if entries else None,
        "latestCompatible": eligible[0] if eligible else None,
        "eligible": eligible,
        "releases": out,
        "fetchedAt": data.get("fetchedAt"),
        "lastError": data.get("lastError"),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(prog="releases.py", description="Magento releases from Adobe for the m2/ catalog.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh")
    r.add_argument("--if-stale", action="store_true")
    r.add_argument("--timeout", type=float, default=8)
    r.add_argument("--catalog", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--tsv", action="store_true")
    ls.add_argument("--catalog", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "refresh":
        _, _, note = refresh(args.catalog, if_stale=args.if_stale, timeout=args.timeout)
        if note:
            print(note)
        return 0
    for e in derive(load().get("releases") or {}, args.catalog):
        print("\t".join((e["version"], e["state"], e["note"] or "")) if args.tsv else
              "%-12s %-10s %s" % (e["version"], e["state"], e["note"] or ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
