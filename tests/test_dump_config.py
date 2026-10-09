"""bin/lib/dump-config.py: the store URLs read straight from a dump."""

import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

import helpers

TOOL = os.path.join(helpers.REPO, "bin", "lib", "dump-config.py")

CREATE = """CREATE TABLE `{t}` (
  `config_id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `scope` varchar(8) NOT NULL DEFAULT 'default',
  `scope_id` int(11) NOT NULL DEFAULT 0,
  `path` varchar(255) NOT NULL DEFAULT 'general',
  `value` text DEFAULT NULL,
  `updated_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`config_id`)
) ENGINE=InnoDB;
"""


def rows(*items):
    return ["(%d,'%s',%d,'%s',%s,'2025-01-01 00:00:00')" % (i + 1, scope, sid, path, value)
            for i, (scope, sid, path, value) in enumerate(items)]


STORE = rows(
    ("default", 0, "web/unsecure/base_url", "'http://shop.example.com/'"),
    ("default", 0, "web/secure/base_url", "'https://shop.example.com/'"),
    ("default", 0, "web/unsecure/base_media_url", "'{{unsecure_base_url}}media/'"),
    ("default", 0, "web/secure/base_media_url", "'https://cdn.example.com/media/'"),
    ("default", 0, "web/cookie/cookie_domain", "'.example.com'"),
    ("default", 0, "general/store_information/name", "'O\\'Brien''s \\\\ shop, (main)'"),
    ("default", 0, "web/secure/base_link_url", "NULL"),
    ("websites", 2, "web/secure/base_url", "'https://b2b.example.com/'"),
    ("stores", 1, "web/secure/base_url", "'https://shop.example.com/'"),  # same as default: not reported
)


class DumpConfig(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="dumpcfg-")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def dump(self, body, name="d.sql.gz"):
        path = os.path.join(self.d, name)
        with gzip.open(path, "wt") as f:
            f.write(body)
        return path

    def read(self, path):
        p = subprocess.run([sys.executable, "-I", TOOL, path], capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return json.loads(p.stdout)

    def check_store(self, d):
        self.assertTrue(d["found"])
        self.assertEqual(d["base"], ["http://shop.example.com/", "https://shop.example.com/"])
        self.assertEqual(d["media"], ["http://shop.example.com/media/", "https://cdn.example.com/media/"])
        self.assertEqual(d["cookie"], ".example.com")
        self.assertEqual([(s["scope"], s["value"]) for s in d["scoped"]], [("websites", "https://b2b.example.com/")])

    def test_mysqldump_extended_insert_on_one_line(self):
        t = "core_config_data"
        body = CREATE.format(t=t) + "INSERT INTO `%s` VALUES %s;\n" % (t, ",".join(STORE))
        self.check_store(self.read(self.dump(body)))

    def test_mariadb_dump_one_row_per_line_with_prefix(self):
        t = "mg_core_config_data"
        body = ("-- MariaDB dump 10.19-11.4.7-MariaDB\n" + CREATE.format(t=t)
                + "INSERT INTO `%s` VALUES\n%s;\n" % (t, ",\n".join(STORE)))
        self.check_store(self.read(self.dump(body)))

    def test_single_row_inserts_with_column_list(self):
        t = "core_config_data"
        body = "".join("INSERT INTO `%s` (`config_id`, `scope`, `scope_id`, `path`, `value`, `updated_at`) VALUES %s;\n"
                       % (t, r) for r in STORE)
        self.check_store(self.read(self.dump(body)))

    def test_empty_media_url_and_cookie(self):
        body = "INSERT INTO `core_config_data` VALUES %s;\n" % ",".join(rows(
            ("default", 0, "web/secure/base_url", "'https://only.example.com/'"),
            ("default", 0, "web/secure/base_media_url", "NULL"),
            ("default", 0, "web/cookie/cookie_domain", "''")))
        d = self.read(self.dump(body))
        self.assertEqual(d["base"], ["https://only.example.com/"])
        self.assertEqual(d["media"], ["https://only.example.com/media/"])
        self.assertEqual(d["cookie"], "")

    def test_not_found(self):
        self.assertEqual(self.read(self.dump("INSERT INTO `sales_order` VALUES (1,'x');\n"))["found"], False)
        no_url = "INSERT INTO `core_config_data` VALUES %s;\n" % ",".join(
            rows(("default", 0, "general/locale/code", "'en_US'")))
        self.assertEqual(self.read(self.dump(no_url))["found"], False)
        bad = os.path.join(self.d, "bad.sql.gz")
        helpers.write(bad, "not gzip at all")
        self.assertEqual(self.read(bad)["found"], False)
        # The reason reaches bash (and the refresh's log) too.
        p = subprocess.run([sys.executable, "-I", TOOL, "--lines", bad], capture_output=True, text=True)
        lines = p.stdout.splitlines()
        self.assertEqual(lines[0], "found\t0")
        self.assertTrue(lines[1].startswith("error\t") and len(lines[1]) > 6, lines)

    def test_stops_after_the_table(self):
        # A big table after core_config_data is never read (the reader stops).
        path = os.path.join(self.d, "big.sql.gz")
        with gzip.open(path, "wt", compresslevel=1) as f:
            f.write("INSERT INTO `core_config_data` VALUES %s;\n" % ",".join(STORE))
            f.write("INSERT INTO `sales_order` VALUES\n")
            line = "(1,'" + "x" * 1000 + "'),\n"
            for _ in range(150000):
                f.write(line)
            f.write("(2,'y');\n")
        t0 = time.time()
        self.check_store(self.read(path))
        self.assertLess(time.time() - t0, 5)


if __name__ == "__main__":
    unittest.main()
