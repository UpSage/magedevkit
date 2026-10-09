"""Runs the real wizard server (server.py) with a small flow script that uses
the real bin/lib/ui.sh, and drives it through the page API — the same
protocol the setup/refresh/version wizards use."""

import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import helpers

FLOW = """#!/bin/bash
PLATFORM_DIR="$(cd "$(dirname "$0")" && pwd)"
source "%s/bin/lib/ui.sh"
UI_STEPS_refresh=("First" "Second")
ui_flow_begin refresh
ui_step refresh 1
ui_task "Doing a thing" true
name="$(prompt_value 'Your name?' 0)"
pw="$(prompt_value 'A password?' 1)"
echo "got name=$name"
echo "the password is $pw"
ui_step refresh 2
ui_result text "Hello" "$name"
""" % helpers.REPO


RERUN_FLOW = """#!/bin/bash
PLATFORM_DIR="$(cd "$(dirname "$0")" && pwd)"
source "%s/bin/lib/ui.sh"
UI_STEPS_setup=("Only step")
ui_flow_begin setup
ui_step setup 1
name="$(prompt_value 'Your name?' 0)"
if [ ! -f "$PLATFORM_DIR/ran-once" ]; then
    touch "$PLATFORM_DIR/ran-once"
    fail "first run fails on purpose"
fi
echo "second run ok for $name"
""" % helpers.REPO


YESNO_FLOW = """#!/bin/bash
source "%s/bin/lib/ui.sh"
UI_STEPS_setup=("Only step")
ui_flow_begin setup
ui_step setup 1
if prompt_yes_no 'Apply the recommended stack versions?' Y MariaDB '11.4 → 12.3' Valkey '8 → 9'; then
    echo "applied"
else
    echo "kept"
fi
""" % helpers.REPO


BUCKET_FLOW = """#!/bin/bash
source "%s/bin/lib/ui.sh"
UI_STEPS_setup=("Only step")
ui_flow_begin setup
ui_step setup 1
p="$(prompt_aws_profile proj-s3)"
echo "profile=$p"
b="$(prompt_s3_bucket "$p" media-b)"
echo "bucket=$b"
""" % helpers.REPO

FAKE_AWS = """#!/bin/sh
case "$*" in
  *list-profiles*) printf 'proj-s3\ndefault\n' ;;
  *list-buckets*) printf 'media-c\tmedia-a\tmedia-b\n' ;;
  *) exit 1 ;;
esac
"""


CHANGE_FLOW = """#!/bin/bash
source "%s/bin/lib/ui.sh"
UI_STEPS_setup=("Questions" "Review")
ui_flow_begin setup
ui_step setup 1
name="$(prompt_value 'Your name?' 0)"
ui_step setup 2
if [ -n "${CONFIRM_TYPED:-}" ]; then
    ui_confirm_destructive "Replace it?" "shop" || exit 130
else
    ui_review "Check" Name "$name" || exit 130
fi
echo "went ahead with $name"
""" % helpers.REPO


class WizardSmoke(unittest.TestCase):
    def setUp(self):
        self.pd = tempfile.mkdtemp(prefix="wiz-")
        self.script = os.path.join(self.pd, "flow.sh")
        helpers.write(self.script, FLOW, 0o755)
        self.url_file = os.path.join(self.pd, "url")
        env = dict(os.environ, WIZARD_TOKEN="wiz-token-abc", WIZARD_URL_FILE=self.url_file, WIZARD_PORT="0")
        self.proc = subprocess.Popen([sys.executable, os.path.join(helpers.WEB, "server.py"), "refresh", self.pd, self.script],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = helpers.wait_for(lambda: helpers.read(self.url_file) if os.path.exists(self.url_file) else None)
        self.port = int(url.split(":")[2].split("/")[0])

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=15)
        shutil.rmtree(self.pd, ignore_errors=True)

    def req(self, method, path, body=None, cookie="wiz_%d=wiz-token-abc"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port}
        if cookie:
            h["Cookie"] = cookie % self.port if "%d" in cookie else cookie
        if method == "POST":
            h["X-Wizard"] = "1"
        conn.request(method, path, body=json.dumps(body or {}).encode() if method == "POST" else None, headers=h)
        r = conn.getresponse()
        raw = r.read()
        return r.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw), dict(r.getheaders())

    def question(self, label_part):
        def q():
            _, st, _ = self.req("GET", "/api/state")
            qq = st.get("question")
            return qq if qq and label_part in qq["label"] else None
        found = helpers.wait_for(q)
        self.assertIsNotNone(found, "question %r never came" % label_part)
        return found

    def test_full_run(self):
        code, _, headers = self.req("GET", "/?token=wiz-token-abc", cookie=None)
        self.assertEqual(code, 303)
        self.assertIn("wiz_%d=wiz-token-abc" % self.port, headers["Set-Cookie"])
        self.assertNotIn("Max-Age", headers["Set-Cookie"])
        self.assertEqual(self.req("GET", "/api/state", cookie="wiz=wiz-token-abc")[0], 403)  # old cookie name
        self.assertEqual(self.req("GET", "/kit.js")[0], 200)

        q = self.question("Your name")
        self.assertEqual(q["type"], "text")
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Ada"})[0], 200)
        q = self.question("A password")
        self.assertEqual(q["type"], "password")
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "s3cret-pass"})[0], 200)

        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertIsNotNone(st)
        self.assertEqual([s["status"] for s in st["steps"]], ["done", "done"])
        self.assertEqual(st["results"], [{"kind": "text", "label": "Hello", "value": "Ada"}])
        self.assertTrue(any("got name=Ada" in l for l in st["log"]))
        self.assertFalse(any("s3cret-pass" in l for l in st["log"]))
        self.assertTrue(any("the password is ••••" in l for l in st["log"]))

        # operation record for the dashboard
        ops_dir = os.path.join(self.pd, "logs", "operations")
        recs = [helpers.read_json(os.path.join(ops_dir, n)) for n in os.listdir(ops_dir) if n.endswith(".json")]
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual((rec["kind"], rec["source"], rec["status"], rec["exitCode"]), ("refresh", "wizard", "done", 0))
        self.assertTrue(rec["logFile"].startswith("logs/refresh-"))
        self.assertNotIn("s3cret-pass", json.dumps(rec))
        self.assertEqual(os.stat(os.path.join(self.pd, rec["logFile"])).st_mode & 0o777, 0o600)

    def test_malformed_posts_are_400(self):
        q = self.question("Your name")
        for body in ([1, 2], "text", {"id": "x"}, {"id": [1]}):
            self.assertEqual(self.req("POST", "/api/answer", body)[0], 400, body)
        self.assertEqual(self.req("POST", "/api/cancel", [0])[0], 400)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Ada"})[0], 200)  # still alive

    def test_cancel_at_question(self):
        self.question("Your name")
        self.assertEqual(self.req("POST", "/api/cancel", {"mode": "after_task"})[0], 200)
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "cancelled" else None)(self.req("GET", "/api/state")[1]))
        self.assertIsNotNone(st)
        ops_dir = os.path.join(self.pd, "logs", "operations")
        rec = helpers.wait_for(lambda: next((r for r in (helpers.read_json(os.path.join(ops_dir, n)) for n in os.listdir(ops_dir)
                                                         if n.endswith(".json")) if r.get("status") == "cancelled"), None))
        self.assertIsNotNone(rec, "record never became cancelled")

    def restart_with(self, flow_text, flow="setup", extra_env=None):
        """Restart this test's server with another flow script."""
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=15)
        helpers.write(self.script, flow_text, 0o755)
        os.remove(self.url_file)
        shutil.rmtree(os.path.join(self.pd, "logs"), ignore_errors=True)
        env = dict(os.environ, WIZARD_TOKEN="wiz-token-abc", WIZARD_URL_FILE=self.url_file, WIZARD_PORT="0")
        env.update(extra_env or {})
        self.proc = subprocess.Popen([sys.executable, os.path.join(helpers.WEB, "server.py"), flow, self.pd, self.script],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = helpers.wait_for(lambda: helpers.read(self.url_file) if os.path.exists(self.url_file) else None)
        self.port = int(url.split(":")[2].split("/")[0])

    def test_s3_bucket_menu(self):
        fakebin = os.path.join(self.pd, "fakebin")
        helpers.write(os.path.join(fakebin, "aws"), FAKE_AWS, 0o755)
        self.restart_with(BUCKET_FLOW, extra_env={"PATH": fakebin + os.pathsep + os.environ["PATH"]})
        q = self.question("AWS CLI profile")
        self.assertEqual(q["type"], "menu")
        self.assertEqual(q["options"], ["default", "proj-s3 (current)", "Set up a new profile — enter its name and access keys"])
        self.assertEqual(q["default"], "0")                        # nothing preselected
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "1"})[0], 200)
        q = self.question("S3 bucket")
        self.assertEqual(q["type"], "menu")
        self.assertIn("buckets profile 'default' can see", q["label"])
        self.assertEqual(q["options"], ["media-a", "media-b (current)", "media-c", "Another bucket — type its name"])
        self.assertEqual(q["default"], "0")                        # nothing preselected
        self.req("POST", "/api/answer", {"id": q["id"], "value": "3"})
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertIn("profile=default", st["log"])
        self.assertIn("bucket=media-c", st["log"])

    def test_s3_bucket_typed_when_other_chosen(self):
        fakebin = os.path.join(self.pd, "fakebin")
        helpers.write(os.path.join(fakebin, "aws"), FAKE_AWS, 0o755)
        self.restart_with(BUCKET_FLOW, extra_env={"PATH": fakebin + os.pathsep + os.environ["PATH"]})
        q = self.question("AWS CLI profile")
        self.req("POST", "/api/answer", {"id": q["id"], "value": "3"})       # set up a new profile
        q = self.question("new AWS CLI profile")
        self.req("POST", "/api/answer", {"id": q["id"], "value": "client-x"})
        q = self.question("S3 bucket")
        self.req("POST", "/api/answer", {"id": q["id"], "value": "4"})
        q = self.question("bucket name")
        self.assertEqual(q["type"], "text")
        self.req("POST", "/api/answer", {"id": q["id"], "value": "my-own-bucket"})
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertIn("profile=client-x", st["log"])
        self.assertIn("bucket=my-own-bucket", st["log"])

    def test_yes_no_question_carries_details(self):
        self.restart_with(YESNO_FLOW)
        q = self.question("stack versions")
        self.assertEqual((q["type"], q["default"]), ("yesno", "Y"))
        self.assertEqual(q["options"], ["MariaDB", "11.4 → 12.3", "Valkey", "8 → 9"])
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "N"})[0], 200)
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertIn("kept", st["log"])

    def change_answers(self, confirm_typed):
        self.restart_with(CHANGE_FLOW, extra_env={"CONFIRM_TYPED": "1"} if confirm_typed else {})
        q = self.question("Your name")
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Ada"})[0], 200)
        kind = "confirm_typed" if confirm_typed else "review"
        q = helpers.wait_for(lambda: (lambda st: st.get("question") if (st.get("question") or {}).get("type") == kind else None)(
            self.req("GET", "/api/state")[1]))
        self.assertIsNotNone(q)
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "restart"})[0], 200)
        q = self.question("Your name")                                       # back at the first question
        st = self.req("GET", "/api/state")[1]
        self.assertEqual((st["status"], st["current"]), ("running", 1))
        self.assertTrue(any("Changing answers" in l for l in st["log"]))
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Bob"})[0], 200)
        q = helpers.wait_for(lambda: (lambda st: st.get("question") if (st.get("question") or {}).get("type") == kind else None)(
            self.req("GET", "/api/state")[1]))
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "shop" if confirm_typed else "start"})[0], 200)
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertTrue(any("went ahead with Bob" in l for l in st["log"]))
        ops_dir = os.path.join(self.pd, "logs", "operations")
        recs = sorted((helpers.read_json(os.path.join(ops_dir, n)) for n in os.listdir(ops_dir) if n.endswith(".json")),
                      key=lambda r: r["id"])
        self.assertEqual([r["status"] for r in recs], ["cancelled", "done"])

    def test_change_answers_at_review(self):
        self.change_answers(confirm_typed=False)

    def test_change_answers_at_typed_confirmation(self):
        self.change_answers(confirm_typed=True)

    def test_run_again_after_failure(self):
        # Replace this test's flow with one that fails the first time.
        self.proc.send_signal(signal.SIGTERM)
        self.proc.wait(timeout=15)
        helpers.write(self.script, RERUN_FLOW, 0o755)
        os.remove(self.url_file)
        shutil.rmtree(os.path.join(self.pd, "logs"), ignore_errors=True)   # drop setUp's run
        env = dict(os.environ, WIZARD_TOKEN="wiz-token-abc", WIZARD_URL_FILE=self.url_file, WIZARD_PORT="0")
        self.proc = subprocess.Popen([sys.executable, os.path.join(helpers.WEB, "server.py"), "setup", self.pd, self.script],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = helpers.wait_for(lambda: helpers.read(self.url_file) if os.path.exists(self.url_file) else None)
        self.port = int(url.split(":")[2].split("/")[0])
        ops_dir = os.path.join(self.pd, "logs", "operations")

        q = self.question("Your name")
        self.assertEqual(self.req("POST", "/api/restart")[0], 409)          # not while running
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Ada"})[0], 200)
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "failed" else None)(self.req("GET", "/api/state")[1]))
        self.assertEqual(st["error"], "first run fails on purpose")
        links = [n for n in os.listdir(ops_dir) if n.endswith(".url")]
        self.assertEqual(len(links), 1)
        self.assertIn("token=wiz-token-abc", helpers.read(os.path.join(ops_dir, links[0])))

        code, body, _ = self.req("POST", "/api/restart")
        self.assertEqual(code, 200, body)
        q = self.question("Your name")                                       # fresh run, fresh question
        st = self.req("GET", "/api/state")[1]
        self.assertEqual((st["status"], st["error"], st["results"]), ("running", None, []))
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": "Bob"})[0], 200)
        st = helpers.wait_for(lambda: (lambda s: s if s["status"] == "done" else None)(self.req("GET", "/api/state")[1]))
        self.assertTrue(any("second run ok for Bob" in l for l in st["log"]))
        self.assertTrue(any("Running the setup again" in l for l in st["log"]))
        self.assertEqual(self.req("POST", "/api/restart")[0], 409)          # not after success

        recs = sorted((helpers.read_json(os.path.join(ops_dir, n)) for n in os.listdir(ops_dir) if n.endswith(".json")),
                      key=lambda r: r["id"])
        self.assertEqual([r["status"] for r in recs], ["failed", "done"])
        self.assertNotEqual(recs[0]["logFile"], recs[1]["logFile"])
        self.assertEqual(len([n for n in os.listdir(ops_dir) if n.endswith(".url")]), 2)
        self.req("POST", "/api/quit")
        self.proc.wait(timeout=15)
        self.assertEqual([n for n in os.listdir(ops_dir) if n.endswith(".url")], [])   # links removed on close

    def test_security(self):
        self.assertEqual(self.req("GET", "/api/state", cookie=None)[0], 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/api/state", headers={"Host": "evil:%d" % self.port, "Cookie": "wiz_%d=wiz-token-abc" % self.port})
        self.assertEqual(conn.getresponse().status, 403)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("POST", "/api/cancel", body=b"{}", headers={"Host": "127.0.0.1:%d" % self.port,
                                                                  "Cookie": "wiz_%d=wiz-token-abc" % self.port})
        self.assertEqual(conn.getresponse().status, 403)  # no X-Wizard


if __name__ == "__main__":
    unittest.main()
