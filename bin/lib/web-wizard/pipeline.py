"""Pipeline readiness of one environment's Magento repo, for the dashboard's
Pipeline tab — read-only, no fetch, no network.

The pipeline wizard (bin/lib/pipeline-engine.sh) writes the kit into
magento/deploy/ and records it in deploy/pipeline.json: the template version,
the targets (environments) and a sha256 of every file it generated. This
module compares that record with the files on disk and the wizard's current
template version (pipeline/VERSION in the instance's wizard repo), checks the
version pins in .platform/stack.env against the instance's .env, and reads
the git state the kit needs (committed, pushed to each target's branch).
"""

import hashlib
import json
import os

import envs
import webcore

OLD_CI_FILES = ("buildspec.yml", "appspec.yml", "scripts/after_install.sh", "scripts/application_start.sh",
                "scripts/application_stop.sh", "scripts/before_install.sh", "scripts/build-release.sh",
                "scripts/validate_service.sh")
PIN_KEYS = envs.VERSION_KEYS + ("MAGENTO_VERSION",)  # VERSION_KEYS mirrors M2_STACK_KEYS (bin/lib/m2-catalog.sh)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def kit_version(e):
    """The template version this environment's wizard repo generates (None if it has no kit)."""
    try:
        with open(os.path.join(e.code_dir, "pipeline", "VERSION")) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _git(path, *args):
    code, out = envs.run(["git", "--no-optional-locks", "-C", path] + list(args), timeout=10, env=webcore.git_env())
    return out.strip() if code == 0 else None


def _git_state(mdir, branches):
    if not os.path.exists(os.path.join(mdir, ".git")):
        return None
    status = _git(mdir, "status", "--porcelain", "--", "deploy", ".platform")
    upstream = _git(mdir, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    ahead = behind = None
    if upstream:
        counts = _git(mdir, "rev-list", "--left-right", "--count", "@{upstream}...HEAD")
        if counts:
            behind, ahead = (int(x) for x in counts.split())
    pushed = {}
    for b in sorted(set(branches)):
        # From the last fetch only — the dashboard never fetches.
        pushed[b] = _git(mdir, "cat-file", "-e", "origin/%s:deploy/pipeline.json" % b) is not None
    return {
        "branch": _git(mdir, "rev-parse", "--abbrev-ref", "HEAD"),
        "kitChanges": len([l for l in (status or "").splitlines() if l.strip()]),
        "upstream": upstream, "ahead": ahead, "behind": behind,
        "kitOnBranch": pushed,
    }


def info(e):
    def probe():
        mdir = e.magento_dir
        current = kit_version(e)
        manifest_path = os.path.join(mdir, "deploy", "pipeline.json")
        old = [f for f in OLD_CI_FILES if os.path.isfile(os.path.join(mdir, f))]
        out = {"available": current is not None, "kitVersion": current, "status": "missing",
               "templateVersion": None, "targets": [], "files": 0, "edited": [], "missingFiles": [],
               "oldFiles": old, "pins": None, "git": None,
               "isRepo": os.path.exists(os.path.join(mdir, ".git"))}
        try:
            with open(manifest_path) as f:
                m = json.load(f)
        except (OSError, ValueError):
            out["git"] = _git_state(mdir, [])
            return out

        files = m.get("files") or {}
        for rel, recorded in sorted(files.items()):
            p = os.path.join(mdir, rel)
            if not os.path.isfile(p):
                out["missingFiles"].append(rel)
            elif _sha256(p) != recorded:
                out["edited"].append(rel)
        out["files"] = len(files)
        out["templateVersion"] = m.get("templateVersion")
        out["targets"] = [dict(t, env=name) for name, t in sorted((m.get("targets") or {}).items())]

        if current is not None and (out["templateVersion"] or 0) < current:
            out["status"] = "outdated"
        elif out["edited"] or out["missingFiles"]:
            out["status"] = "edited"
        else:
            out["status"] = "current"

        stack = envs.parse_env_file(os.path.join(mdir, ".platform", "stack.env"))
        diff = [{"key": k, "repo": stack.get(k), "instance": e.env.get(k)}
                for k in PIN_KEYS if (stack.get(k) or None) != (e.env.get(k) or None)]
        out["pins"] = {"inSync": not diff, "diff": diff}
        out["git"] = _git_state(mdir, [t.get("branch") for t in out["targets"] if t.get("branch")])
        return out

    return envs.CACHE.get(("pipeline", e.magento_dir), 15, probe)


def drop_cache():
    envs.CACHE.drop("pipeline")
    envs.CACHE.drop("gitstatus")
    envs.CACHE.drop("git")
