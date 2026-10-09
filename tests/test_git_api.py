"""The dashboard's Git tab API (bin/lib/web-wizard/gitrepo.py) against a real
git repo: an instance whose magento/ has a bare local `origin`, so fetch,
pull and push run for real without a network or a login."""

import fcntl
import glob
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers


def git(repo, *args):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True, text=True, check=True).stdout.strip()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


class GitApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.path.realpath(tempfile.mkdtemp(prefix="git-api-"))
        cls.tmpdir = os.path.join(cls.root, "tmp")
        os.makedirs(cls.tmpdir)
        repo = helpers.make_repo(cls.root)
        cls.inst = helpers.make_instance(cls.root, "gitty", repo)
        cls.m = os.path.join(cls.inst, "magento")
        cls.origin = os.path.join(cls.root, "origin.git")
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", cls.origin], check=True)
        subprocess.run(["git", "init", "-q", "-b", "main", cls.m], check=True)
        git(cls.m, "config", "user.name", "Dash Test")
        git(cls.m, "config", "user.email", "dash@test")
        write(os.path.join(cls.m, ".gitignore"), "/app/etc/env.php\n")
        write(os.path.join(cls.m, "a.txt"), "one\n")
        write(os.path.join(cls.m, "b.txt"), "two\n")
        git(cls.m, "add", "-A")
        git(cls.m, "commit", "-qm", "first")
        git(cls.m, "remote", "add", "origin", cls.origin)
        git(cls.m, "push", "-q", "-u", "origin", "main")
        cls.fakebin = helpers.fake_docker_dir(cls.root, [])
        cls.home = os.path.join(cls.root, "home")
        os.makedirs(cls.home)
        cls.dash = helpers.Dashboard(repo, cls.root, cls.fakebin, {"TMPDIR": cls.tmpdir, "HOME": cls.home})

    @classmethod
    def tearDownClass(cls):
        cls.dash.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def setUp(self):
        # Every test starts on a clean main that matches origin.
        git(self.m, "switch", "-q", "main")
        git(self.m, "reset", "-q", "--hard", "origin/main")
        git(self.m, "clean", "-qfd")

    def status(self):
        code, st, _ = self.dash.request("GET", "/api/environments/gitty/git")
        self.assertEqual(code, 200, st)
        return st

    def post(self, cmd, body=None):
        return self.dash.request("POST", "/api/environments/gitty/git/" + cmd, body or {})

    def run_op(self, cmd, body=None):
        code, res, _ = self.post(cmd, body)
        self.assertEqual(code, 202, res)

        def done():
            c, rec, _ = self.dash.request("GET", "/api/environments/gitty/operations/" + res["operation"])
            return rec if c == 200 and rec["status"] not in ("starting", "running") else None
        rec = helpers.wait_for(done, timeout=60)
        self.assertIsNotNone(rec)
        return rec

    def file(self, st, path):
        return next((f for f in st["files"] if f["path"] == path), None)

    def test_status_stage_unstage_commit(self):
        write(os.path.join(self.m, "a.txt"), "one changed\n")
        write(os.path.join(self.m, "c.txt"), "new\n")
        st = self.status()
        self.assertEqual((st["branch"], st["upstream"], st["ahead"], st["behind"]), ("main", "origin/main", 0, 0))
        self.assertTrue(self.file(st, "a.txt")["unstaged"])
        self.assertTrue(self.file(st, "c.txt")["untracked"])
        self.assertIsNone(self.file(st, "app/etc/env.php"))  # ignored
        self.assertEqual(st["identity"], {"name": "Dash Test", "email": "dash@test"})

        for bad in (["../x"], ["-rf"], ["nope.txt"], [], "a.txt", [1]):
            self.assertEqual(self.post("stage", {"paths": bad})[0], 400, bad)
        code, res, _ = self.post("stage", {"paths": ["a.txt"]})
        self.assertEqual(code, 200, res)
        st = self.status()
        self.assertTrue(self.file(st, "a.txt")["staged"])
        self.assertFalse(self.file(st, "a.txt")["unstaged"])

        code, d, _ = self.dash.request("GET", "/api/environments/gitty/git/diff?path=a.txt&staged=1")
        self.assertEqual(code, 200)
        self.assertIn("+one changed", d["text"])
        self.assertEqual(self.dash.request("GET", "/api/environments/gitty/git/diff?path=zzz&staged=0")[0], 400)

        self.assertEqual(self.post("unstage", {"paths": ["a.txt"]})[0], 200)
        self.assertFalse(self.file(self.status(), "a.txt")["staged"])

        self.assertEqual(self.post("commit", {"message": "x"})[0], 400)  # nothing staged
        self.assertEqual(self.post("stage", {"paths": "all"})[0], 200)
        for bad in ({"message": ""}, {"message": "   "}, {"message": "x" * 6000}, {}):
            self.assertEqual(self.post("commit", bad)[0], 400, bad)
        code, res, _ = self.post("commit", {"message": "Change a, add c\n\nBody line"})
        self.assertEqual(code, 200, res)
        self.assertEqual(git(self.m, "log", "-1", "--format=%s|%an"), "Change a, add c|Dash Test")
        self.assertEqual(git(self.m, "log", "-1", "--format=%b"), "Body line")
        st = self.status()
        self.assertEqual(st["ahead"], 1)
        self.assertFalse(st["commits"][0]["pushed"])
        self.assertTrue(st["commits"][1]["pushed"])

    def test_ignored_and_partly_staged(self):
        os.makedirs(os.path.join(self.m, "var", "cache"), exist_ok=True)
        write(os.path.join(self.m, ".gitignore"), "/app/etc/env.php\n/var/\n")
        git(self.m, "commit", "-qam", "ignore var")
        git(self.m, "push", "-q", "origin", "main")
        write(os.path.join(self.m, "var", "cache", "x"), "cache\n")
        write(os.path.join(self.m, "a.txt"), "staged part\n")
        git(self.m, "add", "a.txt")
        write(os.path.join(self.m, "a.txt"), "staged part\nand more\n")
        st = self.status()
        # app/ only holds the ignored env.php here, so git lists the folder.
        self.assertIn("app/", st["ignored"])
        self.assertIn("var/", st["ignored"])
        self.assertNotIn("var/cache/x", st["ignored"])  # whole folders once
        a = self.file(st, "a.txt")
        self.assertTrue(a["staged"] and a["unstaged"])  # partly staged: both columns
        git(self.m, "reset", "-q", "--hard", "HEAD~1")
        git(self.m, "push", "-q", "-f", "origin", "main")

    def test_branches(self):
        for bad in ("-x", "a..b", "a b", "x.lock", "", "feature/", "../evil"):
            self.assertEqual(self.post("create_branch", {"name": bad})[0], 400, bad)
        code, res, _ = self.post("create_branch", {"name": "feature/x", "switch": True})
        self.assertEqual(code, 200, res)
        self.assertEqual(self.status()["branch"], "feature/x")
        self.assertEqual(self.post("create_branch", {"name": "feature/x"})[0], 409)
        self.assertEqual(self.post("switch", {"branch": "nope"})[0], 400)
        self.assertEqual(self.post("switch", {"branch": "main"})[0], 200)
        self.assertEqual(self.status()["branch"], "main")
        git(self.m, "branch", "-D", "feature/x")

    def test_delete_branch(self):
        self.assertEqual(self.post("delete_branch", {"branch": "main"})[0], 409)   # the current branch
        self.assertEqual(self.post("delete_branch", {"branch": "nope"})[0], 400)
        git(self.m, "branch", "merged")
        code, res, _ = self.post("delete_branch", {"branch": "merged"})
        self.assertEqual(code, 200, res)
        self.assertNotIn("merged", git(self.m, "branch", "--list", "merged"))
        # A branch with a commit nowhere else needs force.
        git(self.m, "switch", "-q", "-c", "lonely")
        write(os.path.join(self.m, "a.txt"), "only here\n")
        git(self.m, "commit", "-qam", "only here")
        git(self.m, "switch", "-q", "main")
        code, res, _ = self.post("delete_branch", {"branch": "lonely"})
        self.assertEqual(code, 409, res)
        self.assertIn("1 commit(s)", res["error"])
        code, res, _ = self.post("delete_branch", {"branch": "lonely", "force": True})
        self.assertEqual(code, 200, res)
        self.assertEqual(git(self.m, "branch", "--list", "lonely"), "")
        self.assertIn("main", git(self.origin, "branch"))  # origin untouched

    def test_discard(self):
        write(os.path.join(self.m, "b.txt"), "two changed\n")
        write(os.path.join(self.m, "d.txt"), "scratch\n")
        code, res, _ = self.post("discard", {"paths": ["b.txt", "d.txt"]})
        self.assertEqual(code, 200, res)
        with open(os.path.join(self.m, "b.txt")) as f:
            self.assertEqual(f.read(), "two\n")
        self.assertFalse(os.path.exists(os.path.join(self.m, "d.txt")))

    def test_push_publish_pull_and_login_never_logged(self):
        write(os.path.join(self.m, "a.txt"), "pushed\n")
        git(self.m, "commit", "-qam", "to push")
        token = "ghp_TESTTOKEN0123456789abcdef"
        rec = self.run_op("push", {"username": "someone", "token": token})
        self.assertEqual(rec["status"], "done", rec)
        self.assertEqual(self.status()["ahead"], 0)
        with open(os.path.join(self.inst, rec["logFile"])) as f:
            self.assertNotIn(token, f.read())
        for path in glob.glob(os.path.join(self.inst, "logs", "operations", "*.json")):
            with open(path) as f:
                self.assertNotIn(token, f.read())
        self.assertEqual(glob.glob(os.path.join(self.tmpdir, "dash-git-*")), [])  # login dir removed
        self.assertEqual(self.post("push", {"username": "someone"})[0], 400)       # half a login

        # A new branch is published with an upstream.
        self.assertEqual(self.post("create_branch", {"name": "topic", "switch": True})[0], 200)
        st = self.status()
        self.assertIsNone(st["upstream"])
        self.assertEqual(self.run_op("push")["status"], "done")
        self.assertEqual(self.status()["upstream"], "origin/topic")
        self.assertEqual(self.post("switch", {"branch": "main"})[0], 200)

        # Someone else pushes → fetch shows it behind → pull fast-forwards.
        other = os.path.join(self.root, "other")
        subprocess.run(["git", "clone", "-q", self.origin, other], check=True)
        write(os.path.join(other, "b.txt"), "from elsewhere\n")
        git(other, "-c", "user.name=o", "-c", "user.email=o@o", "commit", "-qam", "elsewhere")
        git(other, "push", "-q", "origin", "main")
        self.assertEqual(self.run_op("fetch")["status"], "done")
        self.assertEqual(self.status()["behind"], 1)
        self.assertEqual(self.run_op("pull")["status"], "done")
        st = self.status()
        self.assertEqual((st["ahead"], st["behind"]), (0, 0))

        # Diverged: pull is fast-forward only, so it fails and changes nothing.
        write(os.path.join(other, "b.txt"), "again\n")
        git(other, "-c", "user.name=o", "-c", "user.email=o@o", "commit", "-qam", "again")
        git(other, "push", "-q", "origin", "main")
        write(os.path.join(self.m, "a.txt"), "local\n")
        git(self.m, "commit", "-qam", "local")
        head = git(self.m, "rev-parse", "HEAD")
        self.assertEqual(self.run_op("fetch")["status"], "done")
        self.assertEqual(self.run_op("pull")["status"], "failed")
        self.assertEqual(git(self.m, "rev-parse", "HEAD"), head)

    def test_remember_and_forget_login(self):
        creds = os.path.join(self.home, ".git-credentials")
        self.assertFalse(self.status()["savedLogin"])
        rec = self.run_op("fetch", {"username": "someone", "token": "ghp_remember0123456789", "remember": True})
        self.assertEqual(rec["status"], "done", rec)
        with open(os.path.join(self.inst, rec["logFile"])) as f:
            self.assertIn("-c credential.helper=store", f.readline())
        # Only this repo is told to use the store; the global config isn't touched.
        self.assertEqual(git(self.m, "config", "--local", "--get", "credential.helper"), "store")
        self.assertFalse(os.path.exists(os.path.join(self.home, ".gitconfig")))
        # A file:// origin never authenticates, so git saved nothing; a saved
        # GitHub login looks like this:
        write(creds, "https://someone:ghp_saved0123456789@github.com\n")
        os.chmod(creds, 0o600)
        self.assertTrue(self.status()["savedLogin"])
        code, res, _ = self.post("forget_login")
        self.assertEqual(code, 200, res)
        self.assertFalse(self.status()["savedLogin"])
        with open(creds) as f:
            self.assertNotIn("github.com", f.read())

    def test_login_never_written_when_locked(self):
        fd = os.open(os.path.join(self.inst, ".wizard.lock"), os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            code, _, _ = self.post("fetch", {"username": "someone", "token": "ghp_locked0123456789"})
            self.assertEqual(code, 409)
        finally:
            os.close(fd)
        self.assertEqual(glob.glob(os.path.join(self.tmpdir, "dash-git-*")), [])

    def test_git_children_never_see_the_dashboard_token(self):
        seen = os.path.join(self.root, "hook-env")
        hook = os.path.join(self.m, ".git", "hooks", "pre-commit")
        write(hook, "#!/bin/sh\nenv > '%s'\n" % seen)
        os.chmod(hook, 0o755)
        try:
            write(os.path.join(self.m, "a.txt"), "hooked\n")
            self.assertEqual(self.post("stage", {"paths": ["a.txt"]})[0], 200)
            code, res, _ = self.post("commit", {"message": "hooked"})
            self.assertEqual(code, 200, res)
            with open(seen) as f:
                env = f.read()
            self.assertIn("GIT_TERMINAL_PROMPT=0", env)
            self.assertNotIn("DASHBOARD_TOKEN", env)
            self.assertNotIn(self.dash.token, env)
        finally:
            os.remove(hook)

    def test_network_op_log_is_private(self):
        rec = self.run_op("fetch")
        self.assertEqual(os.stat(os.path.join(self.inst, rec["logFile"])).st_mode & 0o777, 0o600)

    def test_lock_held_refuses_writes(self):
        write(os.path.join(self.m, "a.txt"), "x\n")
        fd = os.open(os.path.join(self.inst, ".wizard.lock"), os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            self.assertEqual(self.post("stage", {"paths": ["a.txt"]})[0], 409)
            self.assertEqual(self.post("fetch")[0], 409)
            self.assertEqual(self.status()["branch"], "main")  # reads still work
        finally:
            os.close(fd)

    def test_unknown_command(self):
        self.assertEqual(self.post("rebase", {})[0], 404)
        self.assertEqual(self.post("push", {"username": "a\nb", "token": "t"})[0], 400)


if __name__ == "__main__":
    unittest.main()
