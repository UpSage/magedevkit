"""File-question uploads in the wizard server: gzipped dumps (default) and
JSON files such as auth.json (prompt_file_source ... json)."""

import gzip
import http.client
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest

import helpers

FLOW = """#!/bin/bash
PLATFORM_DIR="$(cd "$(dirname "$0")" && pwd)"
source "%s/bin/lib/ui.sh"
UI_STEPS_setup=("Files")
ui_flow_begin setup
ui_step setup 1
auth="$(prompt_file_source 'Where is your auth.json? (file path or URL)' json)"
cp "$auth" "$PLATFORM_DIR/copied-auth.json"
dump="$(prompt_db_source 'Where is the database dump? (file path or URL)')"
echo "dump=$dump"
[ -n "${FAIL_AT_END:-}" ] && exit 1
exit 0
""" % helpers.REPO

AUTH = {"http-basic": {"repo.magento.com": {"username": "pubkey123", "password": "privkey456"}}}


class Uploads(unittest.TestCase):
    def start(self, fail_at_end=False):
        self.pd = tempfile.mkdtemp(prefix="wiz-up-")
        script = os.path.join(self.pd, "flow.sh")
        helpers.write(script, FLOW, 0o755)
        url_file = os.path.join(self.pd, "url")
        env = dict(os.environ, WIZARD_TOKEN="tok-up", WIZARD_URL_FILE=url_file, WIZARD_PORT="0")
        if fail_at_end:
            env["FAIL_AT_END"] = "1"
        self.proc = subprocess.Popen([sys.executable, os.path.join(helpers.WEB, "server.py"), "setup", self.pd, script],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        url = helpers.wait_for(lambda: helpers.read(url_file) if os.path.exists(url_file) else None)
        self.port = int(url.split(":")[2].split("/")[0])
        self.updir = os.path.join(self.pd, "db", "db-refresh", "uploads")

    def tearDown(self):
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            self.proc.wait(timeout=15)
        shutil.rmtree(self.pd, ignore_errors=True)

    def req(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": "127.0.0.1:%d" % self.port, "Cookie": "wiz_%d=tok-up" % self.port}
        if method in ("POST", "PUT"):
            h["X-Wizard"] = "1"
        if method == "POST":
            body = json.dumps(body or {}).encode()
        try:
            conn.request(method, path, body=body, headers=h)
            r = conn.getresponse()
            raw = r.read()
        finally:
            conn.close()
        return r.status, json.loads(raw) if raw[:1] in (b"{", b"[") else raw

    def question(self, part):
        def q():
            st = self.req("GET", "/api/state")[1]
            qq = st.get("question")
            return qq if qq and part in qq["label"] else None
        found = helpers.wait_for(q)
        self.assertIsNotNone(found, part)
        return found

    def upload(self, q, data, name):
        return self.req("PUT", "/api/upload?q=%d&name=%s" % (q["id"], name), data)

    def state(self, status):
        return helpers.wait_for(lambda: (lambda s: s if s["status"] == status else None)(self.req("GET", "/api/state")[1]))

    def test_auth_json_and_dump_uploads(self):
        self.start()
        q = self.question("auth.json")
        self.assertEqual((q["type"], q["options"]), ("file", ["kind=json"]))
        # wrong content for this question
        self.assertEqual(self.upload(q, gzip.compress(b"x"), "auth.json")[0], 415)
        self.assertEqual(self.upload(q, b"not json", "auth.json")[0], 415)
        self.assertEqual(self.upload(q, b"[1, 2]", "auth.json")[0], 415)
        # too big: refused before the body is read (the page checks the size first anyway)
        try:
            self.assertEqual(self.upload(q, b"{" + b" " * (1 << 20) + b"}", "auth.json")[0], 413)
        except (BrokenPipeError, ConnectionResetError):
            pass
        self.assertEqual(os.listdir(self.updir) if os.path.isdir(self.updir) else [], [])
        code, body = self.upload(q, json.dumps(AUTH).encode(), "auth.json")
        self.assertEqual(code, 200, body)
        self.assertTrue(body["path"].startswith(self.updir) and body["path"].endswith(".json"))
        self.assertEqual(oct(os.stat(body["path"]).st_mode & 0o777), "0o600")
        auth_upload = body["path"]
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": auth_upload})[0], 200)

        q = self.question("database dump")
        self.assertEqual(q["options"], ["kind=sqlgz"])
        self.assertEqual(self.upload(q, json.dumps(AUTH).encode(), "dump.sql.gz")[0], 415)  # not gzip
        code, body = self.upload(q, gzip.compress(b"CREATE TABLE t (id int);"), "dump.sql.gz")
        self.assertEqual(code, 200, body)
        dump_upload = body["path"]
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": dump_upload})[0], 200)

        self.assertIsNotNone(self.state("done"))
        self.assertEqual(json.loads(helpers.read(os.path.join(self.pd, "copied-auth.json"))), AUTH)
        self.assertFalse(os.path.exists(auth_upload))   # credentials gone
        self.assertFalse(os.path.exists(dump_upload))   # done → uploads removed

    def test_failed_run_keeps_dump_but_never_auth(self):
        self.start(fail_at_end=True)
        q = self.question("auth.json")
        auth_upload = self.upload(q, json.dumps(AUTH).encode(), "auth.json")[1]["path"]
        self.req("POST", "/api/answer", {"id": q["id"], "value": auth_upload})
        q = self.question("database dump")
        dump_upload = self.upload(q, gzip.compress(b"x"), "d.sql.gz")[1]["path"]
        self.req("POST", "/api/answer", {"id": q["id"], "value": dump_upload})
        st = self.state("failed")
        self.assertIsNotNone(st)
        self.assertFalse(os.path.exists(auth_upload))
        self.assertEqual(st["uploadsKept"], [dump_upload])   # offered for re-use; auth.json isn't

    def test_unknown_upload_path_rejected(self):
        self.start()
        q = self.question("auth.json")
        fake = os.path.join(self.updir, "deadbeefdeadbeef.json")
        self.assertEqual(self.req("POST", "/api/answer", {"id": q["id"], "value": fake})[0], 400)


if __name__ == "__main__":
    unittest.main()
