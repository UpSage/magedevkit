"""The DB refresh's rewrite stage (bin/lib/db-refresh-engine.sh) on a small
production-style dump: the store URLs come from the dump itself
(bin/lib/prod-urls.sh + dump-config.py), and the SQL that reaches the
database has only the local site's URLs. A fake bin/platform captures what
would have been imported."""

import gzip
import json
import os
import shutil
import subprocess
import tempfile
import unittest

import helpers

FAKE_PLATFORM = """#!/bin/bash
# Records every mariadb call; the import's stdin is the processed SQL.
echo "platform $*" >> "$CAPTURE.calls"
case "$*" in
  *"-N -e"*) echo 0 ;;
  *mariadb*) [ -t 0 ] || cat >> "$CAPTURE.sql" ;;
esac
exit 0
"""

DUMP_ROWS = [
    "(1,'default',0,'web/unsecure/base_url','http://shop.example.com/','2025-01-01 00:00:00')",
    "(2,'default',0,'web/secure/base_url','https://shop.example.com/','2025-01-01 00:00:00')",
    "(3,'default',0,'web/secure/base_media_url','https://cdn.example.com/media/','2025-01-01 00:00:00')",
    "(4,'default',0,'web/cookie/cookie_domain','.example.com','2025-01-01 00:00:00')",
    "(5,'websites',2,'web/secure/base_url','https://b2b.example.com/','2025-01-01 00:00:00')",
]
OTHER = "INSERT INTO `cms_page` VALUES\n(1,'Home','<a href=\"https://shop.example.com/sale\">x</a> <img src=\"https://cdn.example.com/media/a.jpg\">');\n"


class RefreshRewrite(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="rewrite-")
        self.repo = os.path.join(self.d, "repo")
        helpers.copy_scripts(self.repo)
        helpers.write(os.path.join(self.repo, "bin", "platform"), FAKE_PLATFORM, 0o755)
        self.inst = os.path.join(self.d, "shop")
        os.makedirs(os.path.join(self.inst, "db"))
        helpers.write(os.path.join(self.inst, ".platform-instance"), "PLATFORM_REPO=%s\n" % self.repo)
        self.env = os.path.join(self.inst, ".env")
        helpers.write(self.env, "APP_NAME=shop\nAPP_DOMAIN=shop.test\nPROD_BASE_URL=https://stale.example.org/\n")
        self.capture = os.path.join(self.d, "cap")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def refresh(self, body, extra=""):
        dump = os.path.join(self.d, "dump.sql.gz")
        with gzip.open(dump, "wt") as f:
            f.write(body)
        script = """
            PLATFORM_DIR=%(repo)s; export PLATFORM_INSTANCE_DIR=%(inst)s
            source "$PLATFORM_DIR/bin/lib/ui.sh"; source "$PLATFORM_DIR/bin/lib/env.sh"; source "$PLATFORM_DIR/bin/lib/paths.sh"
            set -a; source "$ENV_FILE"; set +a
            MARIADB_DB=magento MARIADB_USER=magento MARIADB_PASSWORD=pw MARIADB_ROOT_PASSWORD=rootpw
            TARGET_BASE_URL="https://shop.test/"; TABLES_TO_STRIP=(); APPLY_URL_REWRITE=1; BACKUP_BEFORE_REPLACE=0
            LOCAL_SOURCE_FILE=%(dump)s
            %(extra)s
            source "$PLATFORM_DIR/bin/lib/db-refresh-engine.sh"
        """ % {"repo": self.repo, "inst": self.inst, "dump": dump, "extra": extra}
        p = subprocess.run(["bash", "-c", script], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                           env=dict(os.environ, CAPTURE=self.capture), timeout=60)
        sql = helpers.read(self.capture + ".sql") if os.path.exists(self.capture + ".sql") else ""
        return p, sql

    def test_urls_come_from_the_dump(self):
        body = "INSERT INTO `core_config_data` VALUES\n%s;\n%s" % (",\n".join(DUMP_ROWS), OTHER)
        p, sql = self.refresh(body)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        out = p.stdout + p.stderr
        self.assertIn("The dump is from http://shop.example.com/ https://shop.example.com/", out)
        self.assertIn("saved PROD_BASE_URL was https://stale.example.org/", out)
        self.assertIn("b2b.example.com", out)  # other website's URL is listed, not rewritten
        # Both base URLs, the CDN media URL and the cookie domain are rewritten.
        self.assertNotIn("shop.example.com", sql)
        self.assertNotIn("cdn.example.com", sql)
        self.assertIn("'web/unsecure/base_url','https://shop.test/'", sql)
        self.assertIn("'web/secure/base_media_url','https://shop.test/media/'", sql)
        self.assertIn("'web/cookie/cookie_domain',''", sql)
        self.assertIn('https://shop.test/sale', sql)
        self.assertIn('https://shop.test/media/a.jpg', sql)
        self.assertIn("https://b2b.example.com/", sql)
        env = helpers.read(self.env)
        self.assertIn("PROD_BASE_URL=https://shop.example.com/\n", env)
        self.assertIn("PROD_MEDIA_URL=https://cdn.example.com/media/\n", env)
        self.assertIn("PROD_COOKIE_DOMAIN=.example.com\n", env)
        # Nobody to ask (no terminal, no page): the check is logged, not asked.
        self.assertIn("Store URL: https://shop.example.com/ → https://shop.test/", out)
        self.assertIn("Nobody to ask — going ahead", out)

    def web(self, answer):
        """The same refresh as answered on the wizard page: every event the
        page would get is appended to events.log (ui_emit overwrites)."""
        ui = os.path.join(self.d, "ui")
        os.makedirs(ui)
        helpers.write(os.path.join(ui, "answer"), answer + "\n")
        body = "INSERT INTO `core_config_data` VALUES\n%s;\n%s" % (",\n".join(DUMP_ROWS), OTHER)
        p, sql = self.refresh(body, extra="""
            SETUP_UI=web SETUP_UI_DIR=%s
            ui_emit() { printf '%%s\\n' "$1" >> "$SETUP_UI_DIR/events.log"; }""" % ui)
        events = [json.loads(line) for line in helpers.read(os.path.join(ui, "events.log")).splitlines()]
        asks = [e for e in events if e.get("t") == "ask"]
        return p, sql, asks

    def test_page_shows_the_rewrite_before_it_runs(self):
        p, sql, asks = self.web("Y")
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertEqual(len(asks), 1)
        ask = asks[0]
        self.assertEqual((ask["type"], ask["default"]), ("yesno", "Y"))
        rows = dict(zip(ask["options"][1::2], ask["options"][0::2]))
        self.assertEqual(rows["https://shop.example.com/ → https://shop.test/"], "Store URL")
        self.assertEqual(rows["https://cdn.example.com/media/ → https://shop.test/media/"], "Media URL")
        self.assertEqual(rows[".example.com → blank (uses each request host)"], "Cookie domain")
        self.assertIn("Not rewritten", ask["options"])
        self.assertNotIn("shop.example.com", sql)
        self.assertIn("PROD_BASE_URL=https://shop.example.com/\n", helpers.read(self.env))

    def test_no_at_the_check_changes_nothing(self):
        before = helpers.read(self.env)
        p, sql, asks = self.web("N")
        self.assertEqual(p.returncode, 130, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertEqual(len(asks), 1)
        self.assertIn("nothing was replaced", p.stdout + p.stderr)
        self.assertEqual(sql, "")  # nothing imported
        self.assertNotIn("mariadb", helpers.read(self.capture + ".calls") if os.path.exists(self.capture + ".calls") else "")
        self.assertEqual(helpers.read(self.env), before)  # PROD_* not saved

    def test_dump_summary_for_the_setup_review(self):
        dump = os.path.join(self.d, "s.sql.gz")
        with gzip.open(dump, "wt") as f:
            f.write("INSERT INTO `core_config_data` VALUES\n%s;\n" % ",\n".join(DUMP_ROWS))
        script = 'source %s/bin/lib/prod-urls.sh; dump_prod_summary "$1"' % self.repo
        out = subprocess.run(["bash", "-c", script, "x", dump], capture_output=True, text=True, timeout=30).stdout
        self.assertEqual(out, "https://shop.example.com/ (media https://cdn.example.com/media/; cookie domain .example.com)")

    def test_empty_cookie_domain_is_fine(self):
        rows = [r for r in DUMP_ROWS if "cookie" not in r and "websites" not in r]
        p, sql = self.refresh("INSERT INTO `core_config_data` VALUES %s;\n" % ",".join(rows))
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertNotIn("shop.example.com", sql)

    def test_dump_without_urls_uses_saved_values(self):
        body = "INSERT INTO `cms_page` VALUES (1,'see https://stale.example.org/x');\n"
        p, sql = self.refresh(body)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertIn("using the saved ones (https://stale.example.org/)", p.stdout + p.stderr)
        self.assertIn("https://shop.test/x", sql)
        # Values that came from the env file aren't written back to it.
        self.assertNotIn("PROD_MEDIA_URL", helpers.read(self.env))

    def test_no_urls_anywhere_and_nobody_to_ask_stops(self):
        helpers.write(self.env, "APP_NAME=shop\nAPP_DOMAIN=shop.test\n")
        p, sql = self.refresh("INSERT INTO `cms_page` VALUES (1,'x');\n", extra="unset PROD_BASE_URL PROD_MEDIA_URL")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("doesn't say which site it's from", p.stdout + p.stderr)
        self.assertEqual(sql, "")  # nothing imported

    def test_already_local_dump_is_not_rewritten(self):
        body = "INSERT INTO `core_config_data` VALUES (1,'default',0,'web/secure/base_url','https://shop.test/','2025-01-01 00:00:00');\n"
        p, sql = self.refresh(body)
        self.assertEqual(p.returncode, 0, p.stdout[-2000:] + p.stderr[-2000:])
        self.assertIn("already uses this site's domain", p.stdout + p.stderr)


if __name__ == "__main__":
    unittest.main()
