import io
import os
import unittest

import helpers  # noqa: F401  (puts bin/lib/web-wizard on sys.path)
import envs
import webcore


class FakeHeaders(dict):
    def get(self, k, default=None):
        return super().get(k, default)


def handler(headers, body=b"", port=5000, token="tok-123"):
    """A SecureHandler with just enough state to call its checks."""
    h = webcore.SecureHandler.__new__(webcore.SecureHandler)
    h.headers = FakeHeaders(headers)
    h.rfile = io.BytesIO(body)
    h.hosts = webcore.allowed_hosts(port)
    h.token = token
    h.cookie_name = "wiz_%d" % port
    return h


class HostAndCookie(unittest.TestCase):
    def test_host_allow_list(self):
        self.assertTrue(handler({"Host": "127.0.0.1:5000"}).host_ok())
        self.assertTrue(handler({"Host": "localhost:5000"}).host_ok())
        self.assertFalse(handler({"Host": "127.0.0.1:5001"}).host_ok())
        self.assertFalse(handler({"Host": "evil.example:5000"}).host_ok())
        self.assertFalse(handler({}).host_ok())

    def test_cookie_name_carries_port(self):
        self.assertTrue(handler({"Cookie": "a=b; wiz_5000=tok-123"}).authed())
        self.assertFalse(handler({"Cookie": "wiz=tok-123"}).authed())        # old, port-less name
        self.assertFalse(handler({"Cookie": "wiz_5001=tok-123"}).authed())   # another server's cookie
        self.assertFalse(handler({"Cookie": "wiz_5000=tok-12"}).authed())
        self.assertFalse(handler({}).authed())

    def test_login_cookie_is_a_strict_session_cookie(self):
        c = handler({}).login_cookie()
        self.assertIn("wiz_5000=tok-123", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Strict", c)
        self.assertNotIn("Max-Age", c)

    def test_token_check(self):
        self.assertTrue(handler({}).token_ok("tok-123"))
        self.assertFalse(handler({}).token_ok("tok-124"))
        self.assertFalse(handler({}).token_ok(""))


class ReadJson(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(handler({"Content-Length": "9"}, b'{"a": 1}\n').read_json(), {"a": 1})

    def test_empty_body_is_empty_object(self):
        self.assertEqual(handler({}).read_json(), {})

    def test_bad_lengths(self):
        self.assertIsNone(handler({"Content-Length": "abc"}).read_json())
        self.assertIsNone(handler({"Content-Length": "-1"}, b"{}").read_json())
        self.assertIsNone(handler({"Content-Length": str(webcore.MAX_JSON + 1)}).read_json())

    def test_invalid_json(self):
        self.assertIsNone(handler({"Content-Length": "3"}, b"{x}").read_json())


class Redaction(unittest.TestCase):
    def test_literal_longest_first_and_min_length(self):
        out = webcore.redact_literal("pw=abcdef and abc and ab", {"abc", "abcdef", "ab"})
        self.assertEqual(out, "pw=•••• and •••• and ab")

    def test_patterns(self):
        cases = {
            "DB_PASSWORD=hunter2": "hunter2",
            "'password' => 'p4ssw0rd!'": "p4ssw0rd!",
            "--db-password=magento123": "magento123",
            "key AKIAABCDEFGHIJKLMNOP here": "AKIAABCDEFGHIJKLMNOP",
            "Authorization: Bearer abcdefghijklmnop": "abcdefghijklmnop",
            "https://user:s3cretpass@repo.example.com/x": "s3cretpass",
            "GET /x?X-Amz-Signature=deadbeef&y=1": "deadbeef",
            "Open:  http://127.0.0.1:1/?token=AbC_123": "AbC_123",
            '"secret_key": "wJalrXUtnFEMI"': "wJalrXUtnFEMI",
            "key ASIAABCDEFGHIJKLMNOP here": "ASIAABCDEFGHIJKLMNOP",
            "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG": "wJalrXUtnFEMI/K7MDENG",
            "aws_session_token=IQoJb3JpZ2luX2Vj": "IQoJb3JpZ2luX2Vj",
            '{"SecretAccessKey": "abc123def", "SessionToken": "FwoGZXIvYXdz"}': "FwoGZXIvYXdz",
            '"accessToken":"eyJhbGciOi.payload"': "eyJhbGciOi.payload",
        }
        for line, secret in cases.items():
            self.assertNotIn(secret, webcore.redact_patterns(line), line)

    def test_child_environment_has_no_aws_login(self):
        keep = dict(os.environ)
        try:
            os.environ.update(AWS_ACCESS_KEY_ID="AKIAABCDEFGHIJKLMNOP", AWS_SECRET_ACCESS_KEY="x" * 40,
                              AWS_SESSION_TOKEN="tok", AWS_PROFILE="admin", AWS_CONFIG_FILE="/tmp/c",
                              AWS_REGION="us-east-2", DASHBOARD_TOKEN="t")
            env = webcore.base_env()
            scripts_env = envs.child_env([])  # what an instance's bin/ scripts get
        finally:
            os.environ.clear()
            os.environ.update(keep)
        for k in webcore.AWS_ENV_KEYS + webcore.SECRET_ENV_KEYS:
            self.assertNotIn(k, env)
            self.assertNotIn(k, scripts_env)
        self.assertEqual(env.get("AWS_REGION"), "us-east-2")  # not a login

    def test_patterns_leave_normal_lines(self):
        line = "Container allfasteners-249-platform-php-1 Started"
        self.assertEqual(webcore.redact_patterns(line), line)

    def test_clean_line(self):
        self.assertEqual(webcore.clean_line("\x1b[32mok\x1b[0m\n"), "ok")
        self.assertEqual(webcore.clean_line("10%\r50%\r100%"), "100%")


if __name__ == "__main__":
    unittest.main()
