"""kit.js (the UI recipes shared by the wizard and the dashboard) and the
dashboard's tabs in a real browser: tests/js/kit-tests.html and
dash-tests.html run their assertions and print the result into the page,
read back with headless Chrome/Chromium's --dump-dom.

Skipped when no Chrome or Chromium is found (Linux, or Windows Chrome from
WSL). No Node needed."""

import html
import os
import re
import shutil
import subprocess
import unittest

import helpers

PAGES = os.path.join(helpers.REPO, "tests", "js")
WINDOWS_CHROME = ("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def find_browser(page):
    """(argv0, page URL) for a headless-capable browser, or (None, None)."""
    page = os.path.join(PAGES, page)
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path, "file://" + page
    for path in WINDOWS_CHROME:
        if os.path.exists(path) and shutil.which("wslpath"):
            win = subprocess.run(["wslpath", "-w", page], capture_output=True, text=True).stdout.strip()
            # \\wsl.localhost\Distro\… (a UNC path) → file://wsl.localhost/Distro/…
            return path, "file:" + win.replace("\\", "/")
    return None, None


class KitInBrowser(unittest.TestCase):
    def run_page(self, page, minimum):
        browser, url = find_browser(page)
        if not browser:
            self.skipTest("no Chrome/Chromium found")
        p = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--allow-file-access-from-files",
                            "--virtual-time-budget=10000", "--dump-dom", url],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r'<pre id="result">(.*?)</pre>', p.stdout, re.S)
        self.assertIsNotNone(m, "the test page didn't run: %s" % (p.stderr[-500:] or p.stdout[-500:]))
        report = html.unescape(m.group(1))
        self.assertTrue(report.startswith("DONE OK"), report)
        self.assertGreaterEqual(int(report.split("\n")[0].split()[-1]), minimum, report)

    def test_kit(self):
        self.run_page("kit-tests.html", 20)

    def test_dashboard_tabs(self):
        self.run_page("dash-tests.html", 61)


if __name__ == "__main__":
    unittest.main()
