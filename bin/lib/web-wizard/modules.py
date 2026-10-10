"""The dashboard's Modules tab: one environment's Magento modules.

Everything comes from files on the host, so opening the tab never runs a
command in a container:
  * on/off from app/etc/config.php ('Vendor_Module' => 1|0), parsed strictly
    — a file this can't read is reported, never guessed at;
  * name, setup version and <sequence> from each module's etc/module.xml
    (vendor/<v>/<p>/[src/][<Module>/]etc/ — one package can carry several
    modules — and app/code/<V>/<M>/etc/);
  * the owning package and its version from the package's composer.json and
    composer.lock (vendor/composer/installed.json when there's no lock).

A module is "core" when Magento's product metapackage requires its package
(that covers PayPal_Braintree, dotdigitalgroup_Email & co.), or when its
package or name is Magento's own.

Updates come from bin/local/magento-module check-updates (composer outdated
--direct --all, saved to logs/module-updates.json); each module gets one
update status — UPDATE_STATUSES — from its package's line there.

Turning modules on or off, checking and updating run bin/local/magento-module
through the dashboard's actions (ops.ACTIONS module_*); this file only reads,
and checks what an action may ask for.
"""

import glob
import json
import os
import re
import xml.etree.ElementTree as ET

import envs
import webcore
from oplogs import Redactor

MODULE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+$")  # bin/lib/util.sh MODULE_NAME_RE
MAX_MODULES = 100
MODULES_BLOCK_RE = re.compile(r"""['"]modules['"]\s*=>\s*(?:\[|array\s*\()([^\[\]()]*)(?:\]|\))""", re.S)
CONFIG_ROW_RE = re.compile(r"""^['"]([A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+)['"]\s*=>\s*([01])\s*,?$""")
MAX_XML = 256 * 1024
PRODUCT_RE = re.compile(r"^magento/product-(community|enterprise)-edition$")
MAX_PACKAGES = 100
PACKAGE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*$")
# bin/lib/util.sh COMPOSER_SPEC_RE, without the package part.
CONSTRAINT_RE = re.compile(r"^[A-Za-z0-9.*^~|<>=!@ ,_-]+$")
RELEASE_RE = re.compile(r"^v?(\d+(?:\.\d+){0,3}(?:-p\d+)?)$")
UPDATE_STATUSES = ("update-available", "up-to-date", "unable-to-check", "not-updatable", "managed-by-version-wizard")


def module_states(magento_dir):
    """app/etc/config.php → ({name: enabled}, error). The error is a sentence
    for the page; the dict is empty then."""
    path = os.path.join(magento_dir, "app", "etc", "config.php")
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read(2 * 1024 * 1024)
    except FileNotFoundError:
        return {}, "Magento isn't installed yet (no app/etc/config.php)."
    except OSError:
        return {}, "app/etc/config.php isn't readable."
    m = MODULES_BLOCK_RE.search(text)
    if not m:
        return {}, "app/etc/config.php has no modules list this page can read."
    states = {}
    for line in m.group(1).splitlines():
        line = line.strip()
        if not line or line.startswith(("//", "#")):
            continue
        row = CONFIG_ROW_RE.match(line)
        if not row:
            return {}, "app/etc/config.php has a modules line this page can't read: %s" % line[:120]
        states[row.group(1)] = row.group(2) == "1"
    return states, None


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def installed_packages(magento_dir):
    """{package: version} and the product metapackage's requirements, from
    composer.lock (or vendor/composer/installed.json without one)."""
    data = _read_json(os.path.join(magento_dir, "composer.lock"))
    pkgs = (data or {}).get("packages") if isinstance(data, dict) else None
    if pkgs is None:
        data = _read_json(os.path.join(magento_dir, "vendor", "composer", "installed.json"))
        pkgs = data.get("packages") if isinstance(data, dict) else data  # Composer 2 / Composer 1 layout
    versions, core = {}, set()
    for p in pkgs or []:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str):
            continue
        versions[p["name"]] = p.get("version")
        if PRODUCT_RE.match(p["name"]):
            core.update(k for k in (p.get("require") or {}) if isinstance(k, str))
    return versions, core


def _module_xml(path):
    """etc/module.xml → (name, setup_version, [sequence]) or None."""
    try:
        if os.path.getsize(path) > MAX_XML:
            return None
        node = ET.parse(path).getroot().find("module")
    except (OSError, ET.ParseError):
        return None
    if node is None or not MODULE_RE.match(node.get("name") or ""):
        return None
    seq = [s.get("name") for s in node.findall("sequence/module") if MODULE_RE.match(s.get("name") or "")]
    return node.get("name"), node.get("setup_version"), seq


def module_index(magento_dir):
    """{name: {source, package, setupVersion, sequence}} for every module.xml
    under vendor/ and app/code/."""
    index = {}
    vendor = os.path.join(magento_dir, "vendor")
    for pattern in ("*/*/etc/module.xml", "*/*/src/etc/module.xml", "*/*/*/etc/module.xml", "*/*/src/*/etc/module.xml"):
        for path in sorted(glob.glob(os.path.join(vendor, pattern))):
            found = _module_xml(path)
            if not found or found[0] in index:
                continue
            pkg_dir = os.path.join(vendor, *os.path.relpath(path, vendor).split(os.sep)[:2])
            meta = _read_json(os.path.join(pkg_dir, "composer.json"))
            package = meta.get("name") if isinstance(meta, dict) and isinstance(meta.get("name"), str) else None
            index[found[0]] = {"source": "composer", "package": package, "setupVersion": found[1], "sequence": found[2]}
    for path in sorted(glob.glob(os.path.join(magento_dir, "app", "code", "*", "*", "etc", "module.xml"))):
        found = _module_xml(path)
        if found:  # app/code wins, as it does in Magento when both register the same name
            index[found[0]] = {"source": "app/code", "package": None, "setupVersion": found[1], "sequence": found[2]}
    return index


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def check_path(e):
    return os.path.join(e.platform_dir, "logs", "module-updates.json")


def direct_requires(magento_dir):
    """composer.json's require: {package: constraint}."""
    data = _read_json(os.path.join(magento_dir, "composer.json"))
    req = data.get("require") if isinstance(data, dict) else None
    return {k: v for k, v in (req or {}).items() if isinstance(k, str) and isinstance(v, str)}


def update_status(pkg, source, core, installed, check):
    """One module's update status from the last check (see UPDATE_STATUSES)."""
    if source == "app/code" or not pkg:
        return {"status": "not-updatable", "reason": "Not a composer package (app/code or no composer.json), so composer can't update it."}
    if core:
        return {"status": "managed-by-version-wizard", "reason": "Part of Magento — it moves with Magento itself (Workflows → Switch Magento version)."}
    if not check:
        return {"status": "unable-to-check", "reason": "No update check has run yet."}
    if check.get("packages") is None:
        return {"status": "unable-to-check", "reason": "The last update check failed."}
    line = check["packages"].get(pkg)
    if not line:
        return {"status": "unable-to-check",
                "reason": "Not in the last check: composer.json doesn't require it directly (it comes with another package), or it was added since."}
    if installed and line.get("version") and line["version"] != installed:
        return {"status": "unable-to-check", "reason": "Installed version changed since the last check (now %s) — check again." % installed}
    if line.get("warning") or line.get("abandoned"):
        note = line.get("warning") or ("Abandoned" + (" — use %s instead" % line["abandoned"] if isinstance(line["abandoned"], str) else ""))
    else:
        note = None
    if line.get("latestStatus") == "up-to-date" or not line.get("latest") or line.get("latest") == line.get("version"):
        return {"status": "up-to-date", "latest": line.get("latest"), "note": note}
    return {"status": "update-available", "latest": line["latest"], "major": line.get("latestStatus") == "update-possible",
            "note": note}


def load_check(e):
    data = _read_json(check_path(e))
    return data if isinstance(data, dict) and "checkedAt" in data else None


def listing(e):
    """GET …/modules: every module in config.php (plus any module.xml that
    isn't there yet), with what the page needs to filter and warn."""
    md = e.magento_dir
    key = ("modules", md, _mtime(os.path.join(md, "app", "etc", "config.php")), _mtime(os.path.join(md, "composer.lock")),
           _mtime(os.path.join(md, "composer.json")), _mtime(os.path.join(md, "vendor", "composer", "installed.json")),
           _mtime(os.path.join(md, "app", "code")), _mtime(check_path(e)))
    return envs.CACHE.get(key, 600, lambda: _build(e))


def _build(e):
    md = e.magento_dir
    check = load_check(e)
    states, error = module_states(md)
    index = module_index(md)
    versions, core_pkgs = installed_packages(md)
    dependents = {}
    for name, info in index.items():
        for dep in info["sequence"]:
            dependents.setdefault(dep, []).append(name)
    modules = []
    for name in sorted(set(states) | set(index), key=str.lower):
        info = index.get(name) or {"source": "unknown", "package": None, "setupVersion": None, "sequence": []}
        pkg = info["package"]
        core = bool((pkg and (pkg in core_pkgs or pkg.startswith("magento/"))) or name.startswith("Magento_"))
        modules.append({
            "name": name,
            "vendor": name.split("_", 1)[0],
            "enabled": states.get(name),  # None: not in config.php yet (setup:upgrade adds it)
            "source": info["source"],
            "package": pkg,
            "version": versions.get(pkg) if pkg else None,
            "setupVersion": info["setupVersion"],
            "sequence": info["sequence"],
            "dependents": sorted(dependents.get(name, []), key=str.lower),
            "core": core,
            "update": update_status(pkg, info["source"], core, versions.get(pkg) if pkg else None, check),
        })
    updates = None
    if check:
        lock_time = _mtime(os.path.join(md, "composer.lock"))
        updates = {"checkedAt": check.get("checkedAt"), "ok": check.get("packages") is not None,
                   "error": Redactor(e)(check.get("error") or "") or None,
                   "stale": bool(lock_time and lock_time > (_mtime(check_path(e)) or 0))}
    return {"modules": modules, "error": error, "updates": updates}


def update_specs(e, body):
    """body["packages"] → ["vendor/package:constraint", …] for magento-module
    update. Every package must have an update in the last check and be a
    direct requirement; Magento's own packages and app/code never pass, so a
    crafted request can't reach them. A major update gets ^<latest>; any
    other keeps composer.json's constraint (composer then installs the newest
    version that allows)."""
    names = (body or {}).get("packages") or []
    if not isinstance(names, list) or not names or len(names) > MAX_PACKAGES \
            or not all(isinstance(n, str) and PACKAGE_RE.match(n) for n in names):
        raise webcore.OpError(400, "Name 1 to %d packages, as vendor/package." % MAX_PACKAGES)
    by_pkg = {}
    for m in listing(e)["modules"]:
        if m["package"]:
            by_pkg.setdefault(m["package"], m)
    requires = direct_requires(e.magento_dir)
    specs, refused = [], []
    for name in dict.fromkeys(names):
        m = by_pkg.get(name)
        up = m["update"] if m else {}
        constraint = requires.get(name)
        if up.get("status") != "update-available" or not constraint or name.startswith("magento/"):
            refused.append(name)
            continue
        if up.get("major"):
            release = RELEASE_RE.match(up.get("latest") or "")
            if not release:
                refused.append(name)
                continue
            constraint = "^" + release.group(1)
        if not CONSTRAINT_RE.match(constraint):
            refused.append(name)
            continue
        specs.append("%s:%s" % (name, constraint))
    if refused:
        raise webcore.OpError(400, "No update to apply for: %s." % ", ".join(refused[:5]),
                              "Only packages composer.json requires directly, with an update in the last check, can be "
                              "updated here — Magento's own packages move with the version wizard.")
    return specs


def parse_modules(e, body):
    """body["modules"] → validated list: well-formed names, at most
    MAX_MODULES, each one in this environment's config.php."""
    names = (body or {}).get("modules") or []
    if not isinstance(names, list) or not names or len(names) > MAX_MODULES \
            or not all(isinstance(n, str) and MODULE_RE.match(n) for n in names):
        raise webcore.OpError(400, "Name 1 to %d modules, as Vendor_Module." % MAX_MODULES)
    states, error = module_states(e.magento_dir)
    if error:
        raise webcore.OpError(409, error)
    unknown = [n for n in names if n not in states]
    if unknown:
        raise webcore.OpError(400, "Not in app/etc/config.php: %s." % ", ".join(unknown[:5]),
                                   "Run Build Magento (setup:upgrade) first if the module was just added.")
    return list(dict.fromkeys(names))
