"""The dashboard's Git tab: one environment's Magento repo (magento/).

Reads (status, diff) run directly. Local writes (stage, unstage, commit,
create_branch, switch, discard, delete_branch, set_identity, forget_login)
run synchronously while holding the instance lock, so they never overlap a
wizard or a dashboard action.
Network commands (fetch, pull, push) run as dashboard operations
(ops.spawn_git), so their output shows in the watch panel and in Operations.

Every git call is a fixed argv — no shell — and the only user text that
reaches it is validated: paths must be in the current `git status`, branch
names must pass `git check-ref-format --branch`, and the commit message goes
in on stdin. Nothing here force-pushes, rewrites history or merges: pull is
fast-forward only.

Identity and logins are per repo, never global: the commit name/email are
set in magento/.git/config (set_identity), and a login the user chose to
remember lives in magento/.git/credentials (0600) through a repo-local
`store --file=…` helper — the same layout bin/lib/git-repo.sh uses during
setup. Otherwise a login is handed to one network command only:
ops.spawn_git writes it into a private temp dir that a GIT_ASKPASS script
reads, and oprun deletes that dir when the command ends. It never reaches
argv, a record or a log.
"""

import os
import re
import shutil
import subprocess
import time

import envs
import ops
import webcore

BRANCH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
MAX_FILES = 500
MAX_IGNORED = 200
MAX_DIFF = 200 * 1024
MAX_MESSAGE = 5000
LOG_COUNT = 15
CRED_RE = re.compile(r"^https://[^@\s/]+@github\.com/?$")
GITHUB_ERASE = "protocol=https\nhost=github.com\n\n"


def creds_file(e):
    """This repo's own saved login (bin/lib/git-repo.sh repo_credentials_file)."""
    return os.path.join(e.magento_dir, ".git", "credentials")


def _store_helper(e):
    return "store --file=%s" % creds_file(e)


def saved_login(e):
    """Whether this repo has a GitHub login saved for it: its local helper is
    the per-repo store and that file has a github.com line (only matched,
    never read out or sent anywhere)."""
    if _store_helper(e) not in _helpers(e):
        return False
    try:
        with open(creds_file(e)) as f:
            return any(CRED_RE.match(line.strip()) for line in f)
    except OSError:
        return False


def _helpers(e):
    """The credential.helper values set in this repo's own config."""
    code, out, _ = _run(e, "config", "--local", "--get-all", "credential.helper", timeout=10)
    return [l.strip() for l in out.splitlines() if l.strip()] if code == 0 else []


def remember_here(e):
    """Points this repo (only) at its own credential file, created 0600, so
    git saves the next accepted login there — never in the global config or
    a file other repos read."""
    path = creds_file(e)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    os.close(fd)
    os.chmod(path, 0o600)
    code, _, err = _run(e, "config", "--local", "--replace-all", "credential.helper", _store_helper(e), timeout=10)
    _check(code, err, "git config credential.helper")


def _run(e, *args, input_text=None, timeout=30):
    """(code, stdout, stderr) of git in e.magento_dir."""
    try:
        p = subprocess.run(["git", "-C", e.magento_dir] + list(args), env=webcore.git_env(), input=input_text,
                           stdin=None if input_text is not None else subprocess.DEVNULL,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout, p.stderr
    except (OSError, subprocess.TimeoutExpired) as ex:
        return None, "", str(ex)


def _out(e, *args):
    code, out, _ = _run(e, *args)
    return out.strip() if code == 0 else None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def _parse_status(raw):
    """git status --porcelain=v2 --branch -z → (branch info, files)."""
    info = {"oid": None, "branch": None, "upstream": None, "ahead": None, "behind": None}
    files = []
    parts = raw.split("\0")
    i = 0
    while i < len(parts):
        rec = parts[i]
        i += 1
        if not rec:
            continue
        if rec.startswith("# "):
            key, _, val = rec[2:].partition(" ")
            if key == "branch.oid":
                info["oid"] = None if val == "(initial)" else val
            elif key == "branch.head":
                info["branch"] = None if val == "(detached)" else val
            elif key == "branch.upstream":
                info["upstream"] = val
            elif key == "branch.ab":
                a, b = val.split()
                info["ahead"], info["behind"] = int(a[1:]), int(b[1:])
            continue
        kind = rec[0]
        if kind == "?":
            files.append({"path": rec[2:], "x": "?", "y": "?", "untracked": True})
        elif kind == "1":
            f = rec.split(" ", 8)
            files.append({"path": f[8], "x": f[1][0], "y": f[1][1]})
        elif kind == "2":
            f = rec.split(" ", 9)
            files.append({"path": f[9], "orig": parts[i], "x": f[1][0], "y": f[1][1]})
            i += 1
        elif kind == "u":
            f = rec.split(" ", 10)
            files.append({"path": f[10], "x": f[1][0], "y": f[1][1], "conflict": True})
    for f in files:
        f.setdefault("untracked", False)
        f.setdefault("conflict", False)
        f["staged"] = not f["untracked"] and not f["conflict"] and f["x"] != "."
        f["unstaged"] = f["untracked"] or f["conflict"] or f["y"] != "."
    return info, files


def _files(e):
    code, out, err = _run(e, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=all")
    if code != 0:
        raise ops.OpError(500, "git status failed.", err.strip())
    return _parse_status(out)


def status(e):
    e.require_repo()
    info, files = _files(e)
    branches = []
    out = _out(e, "for-each-ref", "--sort=-committerdate",
               "--format=%(refname:short)%00%(upstream:short)%00%(upstream:track,nobracket)", "refs/heads") or ""
    for line in out.splitlines():
        name, up, track = (line.split("\0") + ["", ""])[:3]
        ahead = re.search(r"ahead (\d+)", track)
        behind = re.search(r"behind (\d+)", track)
        branches.append({"name": name, "upstream": up or None, "gone": track == "gone",
                         "ahead": int(ahead.group(1)) if ahead else 0, "behind": int(behind.group(1)) if behind else 0})
    remotes = [r for r in (_out(e, "for-each-ref", "--format=%(refname:short)", "refs/remotes") or "").splitlines()
               if r and not r.endswith("/HEAD") and "/" in r]

    commits = []
    if info["oid"]:
        unpushed = set((_out(e, "rev-list", "HEAD", "--not", "--remotes") or "").split())
        log = _out(e, "log", "-n", str(LOG_COUNT), "--format=%H%x00%h%x00%s%x00%an%x00%cI", "HEAD") or ""
        for line in log.splitlines():
            full, short, subject, author, date = (line.split("\0") + [""] * 5)[:5]
            commits.append({"hash": short, "subject": subject, "author": author, "date": date,
                            "pushed": full not in unpushed})

    # What .gitignore keeps out — whole ignored folders once (vendor/, …).
    ignored = []
    code, out, _ = _run(e, "status", "--porcelain", "-z", "--ignored=traditional", "--untracked-files=normal")
    if code == 0:
        ignored = sorted(p[3:] for p in out.split("\0") if p.startswith("!! "))

    fetch_head = os.path.join(e.magento_dir, ".git", "FETCH_HEAD")
    try:
        fetched = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(os.path.getmtime(fetch_head)))
    except OSError:
        fetched = None
    name, email = _out(e, "config", "user.name"), _out(e, "config", "user.email")
    return {
        **info,
        "files": files[:MAX_FILES], "filesTotal": len(files),
        "ignored": ignored[:MAX_IGNORED], "ignoredTotal": len(ignored),
        "branches": branches, "remoteBranches": remotes, "commits": commits,
        "remote": _out(e, "remote", "get-url", "origin"),
        "lastFetch": fetched,
        "identity": {"name": name, "email": email} if name and email else None,
        "savedLogin": saved_login(e),
        "mode": e.env.get("MAGENTO_MODE") or None,
    }


def diff(e, path, staged):
    e.require_repo()
    _, files = _files(e)
    f = next((x for x in files if x["path"] == path), None)
    if not f:
        raise ops.OpError(400, "That file has no changes.")
    if f["untracked"]:
        full = os.path.join(e.magento_dir, path)
        try:
            with open(full, "rb") as fh:
                data = fh.read(MAX_DIFF + 1)
        except OSError as ex:
            raise ops.OpError(400, "Couldn't read the file.", str(ex))
        if b"\0" in data[:8000]:
            return {"path": path, "binary": True, "text": "", "truncated": False}
        text = data[:MAX_DIFF].decode("utf-8", "replace")
        return {"path": path, "binary": False, "untracked": True, "truncated": len(data) > MAX_DIFF,
                "text": "\n".join("+" + l for l in text.splitlines())}
    args = ["diff", "--no-color", "--no-ext-diff"] + (["--cached"] if staged else []) + ["--"]
    args += [p for p in (f.get("orig"), path) if p]
    code, out, err = _run(e, *args)
    if code != 0:
        raise ops.OpError(500, "git diff failed.", err.strip())
    binary = "\nBinary files " in "\n" + out and "@@" not in out
    return {"path": path, "binary": binary, "truncated": len(out) > MAX_DIFF, "text": out[:MAX_DIFF]}


# ---------------------------------------------------------------------------
# Local writes (instance lock held)
# ---------------------------------------------------------------------------

def _paths(e, body, files, want=None):
    """The request's paths, each one a changed file (optionally filtered)."""
    paths = (body or {}).get("paths")
    if paths == "all":
        return [f for f in files if want is None or want(f)]
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES or not all(isinstance(p, str) for p in paths):
        raise ops.OpError(400, "Choose the files first.")
    by_path = {f["path"]: f for f in files}
    chosen = []
    for p in paths:
        f = by_path.get(p)
        if not f or (want is not None and not want(f)):
            raise ops.OpError(400, "%s isn't in the list of changed files any more — refresh and try again." % p)
        chosen.append(f)
    return chosen


def _check(code, err, what):
    if code != 0:
        raise ops.OpError(400, "%s failed." % what, (err or "").strip()[-2000:] or None)


def _valid_branch(e, name):
    if not isinstance(name, str) or not BRANCH_RE.match(name) or name.endswith((".lock", "/", ".")) or ".." in name:
        raise ops.OpError(400, "Use a branch name like feature/my-change (letters, digits, . _ - /).")
    code, _, _ = _run(e, "check-ref-format", "--branch", name)
    if code != 0:
        raise ops.OpError(400, "%s isn't a valid branch name." % name)


def _permissions(e, which):
    """Production mode locks the code read-only; worktree changes open it first."""
    if not e.production:
        return
    if e.two_owner:
        raise ops.OpError(409, "In production mode with two-owner file ownership this needs sudo.",
                          "Run it in the terminal instead (git -C %s …), or switch to developer mode first." % e.magento_dir)
    script = os.path.join(e.code_dir, "bin", "permissions", which)
    if os.access(script, os.X_OK):
        code, _ = envs.run([script], timeout=300, env=e.script_env(ops.child_env([e])))
        if code != 0 and which == "unlock":
            raise ops.OpError(500, "Couldn't unlock the file permissions (production mode).")
        if code != 0:
            # Never leave production code writable without saying so.
            raise ops.OpError(500, "The git change was made, but the file permissions couldn't be locked again.",
                              "Run: %s" % e.cmd_hint("permissions", "lock"))


def write(e, command, body):
    e.require_writable()
    e.require_repo()
    fn = LOCAL.get(command)
    if not fn:
        raise ops.OpError(404, "Unknown git command.")
    fd = ops.acquire_lock(e)
    try:
        return fn(e, body or {})
    finally:
        os.close(fd)


def _stage(e, body):
    _, files = _files(e)
    chosen = _paths(e, body, files, want=lambda f: f["unstaged"])
    if body.get("paths") == "all":
        code, _, err = _run(e, "add", "-A")
    else:
        code, _, err = _run(e, "add", "--", *[f["path"] for f in chosen])
    _check(code, err, "git add")
    return {"message": "Staged %d file(s)" % len(chosen)}


def _unstage(e, body):
    info, files = _files(e)
    chosen = _paths(e, body, files, want=lambda f: f["staged"])
    paths = []
    for f in chosen:
        paths += [p for p in (f.get("orig"), f["path"]) if p]
    if info["oid"]:
        code, _, err = _run(e, "restore", "--staged", "--", *paths)
    else:  # nothing committed yet
        code, _, err = _run(e, "rm", "--cached", "-q", "--", *paths)
    _check(code, err, "git restore --staged")
    return {"message": "Unstaged %d file(s)" % len(chosen)}


def _commit(e, body):
    msg = body.get("message")
    if not isinstance(msg, str) or not msg.strip():
        raise ops.OpError(400, "Write a commit message first.")
    if len(msg) > MAX_MESSAGE:
        raise ops.OpError(400, "The commit message is too long (at most %d characters)." % MAX_MESSAGE)
    if not (_out(e, "config", "user.name") and _out(e, "config", "user.email")):
        raise ops.OpError(400, "This repo has no commit name and email yet.",
                          "Set them on the Git tab (they're saved in this repo only).")
    _, files = _files(e)
    if not any(f["staged"] for f in files):
        raise ops.OpError(400, "Nothing is staged — stage the files to commit first.")
    code, _, err = _run(e, "commit", "-q", "-F", "-", input_text=msg.strip() + "\n")
    _check(code, err, "git commit")
    return {"message": "Committed %s" % (_out(e, "rev-parse", "--short", "HEAD") or "")}


def _forget_login(e, body):
    """Removes this repo's saved GitHub login (its own credential file)."""
    code, _, err = _run(e, "credential", "reject", input_text=GITHUB_ERASE)
    _check(code, err, "git credential reject")
    path = creds_file(e)
    try:
        if os.path.getsize(path) == 0:
            os.remove(path)
            _run(e, "config", "--local", "--unset-all", "credential.helper", "^store --file=", timeout=10)
    except OSError:
        pass
    return {"message": "Forgot the GitHub login saved for this repo"}


EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _set_identity(e, body):
    """This repo's own commit name and email (bin/lib/git-repo.sh rules)."""
    name, email = body.get("name"), body.get("email")
    name = name.strip() if isinstance(name, str) else ""
    email = email.strip() if isinstance(email, str) else ""
    if not name or len(name) > 100 or any(c in name for c in "<>\r\n"):
        raise ops.OpError(400, "Enter a name (at most 100 characters, no < > or line breaks).")
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise ops.OpError(400, "Enter a valid email address.")
    for key, value in (("user.name", name), ("user.email", email)):
        code, _, err = _run(e, "config", "--local", key, value, timeout=10)
        _check(code, err, "git config " + key)
    return {"message": "Commits in this repo will be made as %s <%s>" % (name, email)}


def _create_branch(e, body):
    name = body.get("name")
    _valid_branch(e, name)
    if _out(e, "rev-parse", "--verify", "--quiet", "refs/heads/%s" % name):
        raise ops.OpError(409, "There's already a branch called %s." % name)
    start = body.get("from") or "HEAD"
    if start != "HEAD":
        st = status(e)
        if start not in [b["name"] for b in st["branches"]] + st["remoteBranches"]:
            raise ops.OpError(400, "Unknown branch %s." % start)
    if body.get("switch"):
        _permissions(e, "unlock")
        try:
            code, _, err = _run(e, "switch", "-c", name, start)
        finally:
            _permissions(e, "lock")
        _check(code, err, "git switch -c")
        return {"message": "Created %s and switched to it" % name}
    code, _, err = _run(e, "branch", name, start)
    _check(code, err, "git branch")
    return {"message": "Created %s" % name}


def _delete_branch(e, body):
    """Deletes a local branch (never the current one, never anything on
    origin). git branch -d first, which refuses when the branch has commits
    no other branch or remote has; `force` (git branch -D) only after the
    page has shown how many commits would be lost."""
    name = body.get("branch")
    st = status(e)
    if name not in [b["name"] for b in st["branches"]]:
        raise ops.OpError(400, "Unknown local branch.")
    if name == st["branch"]:
        raise ops.OpError(409, "That's the branch you're on — switch to another branch first.")
    only_here = _out(e, "rev-list", "--count", "refs/heads/%s" % name, "--not",
                     "--exclude=%s" % name, "--branches", "--remotes") or "0"  # --exclude takes the bare name with --branches
    if body.get("force"):
        code, _, err = _run(e, "branch", "-D", "--", name)
        _check(code, err, "git branch -D")
    else:
        code, _, err = _run(e, "branch", "-d", "--", name)
        if code != 0:
            raise ops.OpError(409, "%s has %s commit(s) that no other branch or origin has." % (name, only_here),
                              "Deleting it anyway loses them (git branch -D). Push or merge it first if you need them.")
    return {"message": "Deleted the local branch %s (origin untouched)" % name, "lost": int(only_here) if body.get("force") else 0}


def _switch(e, body):
    target = body.get("branch")
    st = status(e)
    local = [b["name"] for b in st["branches"]]
    if target in local:
        args = ["switch", target]
    elif target in st["remoteBranches"]:
        name = target.split("/", 1)[1]
        if name in local:
            args = ["switch", name]
        else:
            args = ["switch", "-c", name, "--track", target]
    else:
        raise ops.OpError(400, "Unknown branch.")
    _permissions(e, "unlock")
    try:
        code, _, err = _run(e, *args, timeout=120)
    finally:
        _permissions(e, "lock")
    _check(code, err, "git switch")
    return {"message": "Switched to %s" % args[-1] if args[1] != "-c" else "Switched to %s (tracking %s)" % (args[2], target)}


def _discard(e, body):
    info, files = _files(e)
    chosen = _paths(e, body, files, want=lambda f: not f["conflict"])
    _permissions(e, "unlock")
    try:
        for f in chosen:
            full = os.path.join(e.magento_dir, f["path"])
            if f["untracked"] or (f["x"] == "A" and not f.get("orig")):
                if not f["untracked"]:
                    _check(*_run(e, "rm", "--cached", "-q", "--", f["path"])[::2], "git rm --cached")
                if os.path.isdir(full) and not os.path.islink(full):
                    shutil.rmtree(full)
                elif os.path.lexists(full):
                    os.remove(full)
            elif f.get("orig"):
                _check(*_run(e, "restore", "--source=HEAD", "--staged", "--worktree", "--", f["orig"])[::2], "git restore")
                _check(*_run(e, "rm", "--cached", "-q", "--ignore-unmatch", "--", f["path"])[::2], "git rm --cached")
                if os.path.lexists(full):
                    os.remove(full)
            else:
                _check(*_run(e, "restore", "--source=HEAD", "--staged", "--worktree", "--", f["path"])[::2], "git restore")
    finally:
        _permissions(e, "lock")
    return {"message": "Discarded the changes in %d file(s)" % len(chosen)}


LOCAL = {"stage": _stage, "unstage": _unstage, "commit": _commit, "create_branch": _create_branch,
         "switch": _switch, "discard": _discard, "forget_login": _forget_login, "delete_branch": _delete_branch,
         "set_identity": _set_identity}


# ---------------------------------------------------------------------------
# Network commands → dashboard operations
# ---------------------------------------------------------------------------

NETWORK = ("fetch", "pull", "push")


def network(e, command, body, all_envs):
    e.require_writable()
    e.require_repo()
    st = status(e)
    if command == "fetch":
        args, label = ["fetch", "--prune", "origin"], "git fetch"
    elif command == "pull":
        if not st["branch"] or not st["upstream"]:
            raise ops.OpError(400, "This branch has no upstream to pull from.")
        args, label = ["pull", "--ff-only"], "git pull (%s)" % st["upstream"]
    elif command == "push":
        if not st["branch"]:
            raise ops.OpError(400, "Switch to a branch first (HEAD is detached).")
        if st["upstream"]:
            args, label = ["push"], "git push (%s)" % st["upstream"]
        else:
            args, label = ["push", "-u", "origin", st["branch"]], "git push -u origin %s" % st["branch"]
    else:
        raise ops.OpError(404, "Unknown git command.")
    login = None
    user, token = (body or {}).get("username"), (body or {}).get("token")
    if user or token:
        if not (isinstance(user, str) and isinstance(token, str) and user.strip() and token.strip()
                and len(user) < 200 and len(token) < 500 and "\n" not in user + token):
            raise ops.OpError(400, "Enter both the GitHub username and the token (or neither).")
        login = (user.strip(), token.strip())
    remember = bool((body or {}).get("remember")) and login is not None
    if remember:
        # git saves the login itself once GitHub accepts it (credential
        # approve → this repo's own store), and later commands find it there.
        remember_here(e)
    worktree = command == "pull" and e.production
    if worktree and e.two_owner:
        raise ops.OpError(409, "In production mode with two-owner file ownership a pull needs sudo — run it in the terminal.")
    return ops.spawn_git(e, "git-" + command, label, args, login, all_envs, unlock=worktree)
