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
and one derived tree per catalog (m2-<hash of the catalog's real path>, so
two checkouts with different catalogs never mix), written whole and
swapped in under a lock. Offline,
refresh keeps the old data, records the error, prints a note and exits 0, so
the wizards look exactly like they do without this.

Standard library only, and no imports from this repo: bash runs it with
python3 -I (which leaves the script's folder off the import path); the
dashboard imports it as a module.
"""

import argparse
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
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
# Without these in Adobe's entry, nothing can be checked — never derived.
REQUIRED_SERVICES = ("PHP", "MariaDB", "OpenSearch")
# Every stack key a version.env can pin (bin/lib/m2-catalog.sh M2_STACK_KEYS).
STACK_KEYS = tuple(k for _, k in SERVICES) + ("DEBIAN_RELEASE",)


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "platform-releases")


def derived_dir(catalog_dir, root=None):
    """This catalog's derived tree — bin/lib/m2-catalog.sh M2_DERIVED_DIR
    computes the same name (sha1 of the catalog's real path, 12 hex)."""
    digest = hashlib.sha1(os.path.realpath(catalog_dir).encode("utf-8")).hexdigest()[:12]
    return os.path.join(root or cache_root(), "m2-" + digest)


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

class _HttpsOnly(urllib.request.HTTPRedirectHandler):
    """Follows redirects only to https:// URLs."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.lower().startswith("https://"):
            raise urllib.error.URLError("refused a redirect to a non-https URL")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def fetch_text(timeout=8):
    """Adobe's file as text. Raises on any network/HTTP error, a redirect
    away from https, a body over MAX_BYTES, or a download that takes longer
    than `timeout` in all (not just per read)."""
    req = urllib.request.Request(ADOBE_URL, headers={"User-Agent": "MageDevKit release check"})
    deadline = time.monotonic() + timeout
    body = b""
    with urllib.request.build_opener(_HttpsOnly).open(req, timeout=timeout) as r:
        while len(body) <= MAX_BYTES:
            chunk = r.read(65536)
            if not chunk:
                break
            body += chunk
            if time.monotonic() > deadline:
                raise TimeoutError("the download took over %gs" % timeout)
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
        failed = ["Adobe lists no %s for it" % svc for svc in REQUIRED_SERVICES if not releases[ver].get(svc)]
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


def write_derived(final, entries, fetched_at):
    """Rebuilds the derived tree `final` (derived_dir) from the derived
    entries: written in a temp folder next to it and swapped in, so a
    release that's now catalogued (or no longer passes) disappears. Call
    with the cache lock held."""
    root = os.path.dirname(final)
    for name in os.listdir(root):  # leftovers of an interrupted run, and the pre-1.0.1 shared tree
        if name.startswith("tmp.m2-") or name.endswith(".old") or name == "m2":
            shutil.rmtree(os.path.join(root, name), ignore_errors=True)
    tmp = tempfile.mkdtemp(prefix="tmp." + os.path.basename(final) + ".", dir=root)
    try:
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
        old = final + ".old"
        if os.path.exists(final):
            os.rename(final, old)
        os.rename(tmp, final)
        shutil.rmtree(old, ignore_errors=True)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


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
    fd, tmp = tempfile.mkstemp(prefix="releases.", dir=root)  # 0600
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, os.path.join(root, "releases.json"))
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


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
        write_derived(derived_dir(catalog_dir, root), entries, data.get("fetchedAt"))
    new = [e["version"] for e in entries if e["state"] == "derived" and e["version"] not in known]
    if new and not note:
        note = "New Magento release(s) from Adobe, offered with checked stack versions: %s." % ", ".join(new)
    return data, entries, note


def classify(installed, edition, platform_php, catalog_dir, data=None):
    """What an environment on `installed` could move to: every newer release,
    and whether the version wizard offers it.
      compatible — catalogued, or derived pins passed: the version wizard
                   lists it (state catalog / derived); otherwise "published"
                   only, with the reason;
      phpChange  — {from, to} when composer.json pins config.platform.php to
                   another PHP than the release's: the wizard moves that pin
                   along (bin/lib/version-engine.sh), so every extension has
                   to support the new PHP — or composer refuses and the
                   wizard puts everything back;
      eligible   — the compatible ones, newest first.
    Commerce (edition "enterprise") releases note the repo.magento.com
    entitlement they need."""
    data = load() if data is None else data
    entries = derive(data.get("releases") or {}, catalog_dir)
    cat = catalog(catalog_dir)
    newer = [e for e in entries if not installed or version_key(e["version"]) > version_key(installed)]
    out = []
    for e in newer:
        compatible = e["state"] != "published"
        target_php = (cat.get(e["version"]) or e["pins"] or {}).get("PHP_VERSION")
        out.append({
            "version": e["version"], "state": e["state"], "compatible": compatible,
            "reason": None if compatible else e["note"],
            "phpChange": {"from": platform_php, "to": target_php}
            if compatible and platform_php and target_php and platform_php != target_php else None,
            "note": "Needs a repo.magento.com entitlement for Adobe Commerce." if edition == "enterprise" else None,
        })
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
        note = (e["note"] or "").replace("\t", " ")
        print("\t".join((e["version"], e["state"], note)) if args.tsv else
              "%-12s %-10s %s" % (e["version"], e["state"], note))
    return 0


if __name__ == "__main__":
    sys.exit(main())
