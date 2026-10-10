"""bin/lib/web-wizard/releases.py — Magento releases from Adobe's system
requirements file: parsing, the checks behind a derived catalog entry, the
cache (offline keeps the old copy), and what an environment could move to."""

import json
import os
import shutil
import socket
import stat
import tempfile
import unittest
import urllib.error

import helpers
import releases

ADOBE = """---
# comment lines like Adobe's
2.4.9:
  AWS S3: ✔️
  Composer: '2.10'
  MariaDB: '12.3'
  MariaDB Cloud: '12.3 (recommended), 11.8'
  OpenSearch: '3'
  PHP: '8.5'
  nginx: '1.30'
2.4.8-p6 (latest):
  Composer: '2.10'
  MariaDB: '11.4, 11.8'
  OpenSearch: '3'
  PHP: '8.4, 8.3'
  RabbitMQ: '4.3'
  Valkey: '8.1'
  Varnish: '8'
  nginx: '1.30'
2.4.8-p5:
  PHP: '8.4, 8.3'
2.4.7-p9:
  PHP: '8.3'
"""


class Releases(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="releases-")
        self.cat = os.path.join(self.d, "m2")
        shutil.copytree(os.path.join(helpers.REPO, "m2"), self.cat)
        self.env = dict(os.environ)
        os.environ["XDG_CACHE_HOME"] = os.path.join(self.d, "cache")
        self.root = releases.cache_root()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.env)
        shutil.rmtree(self.d, ignore_errors=True)

    def fetch(self, text=ADOBE):
        calls = []

        def fetch(timeout):
            calls.append(timeout)
            if isinstance(text, Exception):
                raise text
            return text
        return fetch, calls

    def test_parse(self):
        r = releases.parse(ADOBE)
        self.assertEqual(sorted(r, key=releases.version_key), ["2.4.8-p5", "2.4.8-p6", "2.4.9"])  # 2.4.7 left out
        self.assertEqual(r["2.4.8-p6"]["PHP"], "8.4, 8.3")  # "(latest)" stripped from the release name
        self.assertEqual(r["2.4.9"]["MariaDB"], "12.3")
        self.assertEqual(r["2.4.9"]["MariaDB Cloud"], "12.3 (recommended), 11.8")

    def test_satisfies(self):
        for pin, want in (("2.19.6", "2.19"), ("2.9", "2.9.3+"), ("8.3", "8.4, 8.3"), ("3", "3"), ("1.30", "1.30")):
            self.assertTrue(releases.satisfies(pin, want), (pin, want))
        for pin, want in (("8.4", "8.5"), ("2.1", "2.10"), ("11.4", "11.8"), ("8", "")):
            self.assertFalse(releases.satisfies(pin, want), (pin, want))

    def test_derive(self):
        r = releases.parse(ADOBE)
        r["2.4.8-p7"] = dict(r["2.4.8-p6"], PHP="8.5")
        r["2.4.10"] = dict(r["2.4.9"])
        by = {e["version"]: e for e in releases.derive(r, self.cat)}
        self.assertEqual(by["2.4.8-p5"]["state"], "catalog")
        p6 = by["2.4.8-p6"]
        self.assertEqual((p6["state"], p6["from"]), ("derived", "2.4.8-p5"))
        self.assertEqual(p6["pins"]["PHP_VERSION"], "8.4")
        self.assertEqual(p6["pins"]["DEBIAN_RELEASE"], "trixie")  # copied, not checked
        self.assertEqual(by["2.4.8-p7"]["state"], "published")
        self.assertIn("PHP 8.4 isn't in Adobe's list (8.5)", by["2.4.8-p7"]["note"])
        self.assertEqual(by["2.4.10"]["state"], "published")
        self.assertIn("No m2/ catalog line for 2.4.10", by["2.4.10"]["note"])
        # A repo folder wins.
        helpers.write(os.path.join(self.cat, "2.4.8", "p6", "version.env"), "MAGENTO_VERSION=2.4.8-p6\n")
        self.assertEqual({e["version"]: e for e in releases.derive(r, self.cat)}["2.4.8-p6"]["state"], "catalog")

    def test_refresh_writes_derived_entries_and_private_cache(self):
        fetch, calls = self.fetch()
        data, entries, note = releases.refresh(self.cat, fetch=fetch)
        self.assertEqual(len(calls), 1)
        self.assertIn("2.4.8-p6", note)
        env = helpers.read(os.path.join(self.root, "m2", "2.4.8", "p6", "version.env"))
        self.assertIn("# Derived from 2.4.8-p5", env)
        self.assertIn("MAGENTO_VERSION=2.4.8-p6\n", env)
        self.assertIn("NGINX_VERSION=1.30\n", env)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.root, "releases.json")).st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(os.stat(self.root).st_mode), 0o700)
        self.assertFalse([f for f in os.listdir(self.root) if f.startswith(("releases.", "m2."))
                          and f != "releases.json"])  # no temp files left
        # Fresh: --if-stale doesn't download; the next note is silent.
        _, _, note = releases.refresh(self.cat, if_stale=True, fetch=fetch)
        self.assertEqual((len(calls), note), (1, ""))
        # Once catalogued, the derived entry goes away.
        helpers.write(os.path.join(self.cat, "2.4.8", "p6", "version.env"), "MAGENTO_VERSION=2.4.8-p6\n")
        releases.refresh(self.cat, if_stale=True, fetch=fetch)
        self.assertFalse(os.path.exists(os.path.join(self.root, "m2", "2.4.8", "p6")))

    def test_offline_keeps_the_old_copy(self):
        releases.refresh(self.cat, fetch=self.fetch()[0])
        for err in (socket.timeout("timed out"), urllib.error.HTTPError(releases.ADOBE_URL, 503, "busy", {}, None),
                    ValueError("the file is over 2 MB"), "not: yaml at all"):
            fetch, calls = self.fetch(err)
            data, entries, note = releases.refresh(self.cat, fetch=fetch)
            self.assertIn("Couldn't check Adobe", note, err)
            self.assertIn("2.4.8-p6", data["releases"], err)  # old data kept
            self.assertTrue(os.path.exists(os.path.join(self.root, "m2", "2.4.8", "p6", "version.env")), err)
        # Stale, but the last try failed within the hour: --if-stale doesn't wait on the network again.
        data = releases.load()
        data["fetchedAt"] = "2000-01-01T00:00:00Z"
        releases.save(self.root, data)
        fetch, calls = self.fetch()
        releases.refresh(self.cat, if_stale=True, fetch=fetch)
        self.assertEqual(calls, [])

    def test_offline_from_scratch_is_the_catalog(self):
        fetch, _ = self.fetch(socket.timeout("timed out"))
        data, entries, note = releases.refresh(self.cat, fetch=fetch)
        self.assertIn("using the m2/ catalog only", note)
        self.assertEqual({e["state"] for e in entries}, {"catalog"})
        self.assertEqual(os.listdir(os.path.join(self.root, "m2")), [])

    def test_too_big_is_refused(self):
        class Resp:
            def __init__(self, n):
                self.n = n
            def read(self, limit):
                return b"x" * min(self.n, limit)
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
        real = releases.urllib.request.urlopen
        releases.urllib.request.urlopen = lambda req, timeout: Resp(releases.MAX_BYTES + 10)
        try:
            with self.assertRaises(ValueError):
                releases.fetch_text()
        finally:
            releases.urllib.request.urlopen = real

    def test_classify(self):
        releases.refresh(self.cat, fetch=self.fetch()[0])
        c = releases.classify("2.4.8-p3", "community", "8.3", self.cat)
        self.assertEqual(c["latestPublished"], "2.4.9")
        self.assertEqual(c["latestCompatible"], "2.4.8-p6")
        self.assertEqual(c["eligible"][:2], ["2.4.8-p6", "2.4.8-p5"])
        nine = [r for r in c["releases"] if r["version"] == "2.4.9"][0]
        self.assertFalse(nine["compatible"])
        self.assertIn("config.platform.php", nine["reason"])
        c = releases.classify("2.4.8-p3", "enterprise", None, self.cat)
        self.assertIn("2.4.9", c["eligible"])
        self.assertIn("entitlement", c["releases"][0]["note"])
        self.assertEqual(releases.classify("2.4.9", "community", None, self.cat)["eligible"], [])

    def test_cli(self):
        releases.refresh(self.cat, fetch=self.fetch()[0])
        import io
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(releases.main(["list", "--tsv", "--catalog", self.cat]), 0)
        rows = [l.split("\t") for l in out.getvalue().splitlines()]
        self.assertEqual(rows[1][:2], ["2.4.8-p6", "derived"])


if __name__ == "__main__":
    unittest.main()
