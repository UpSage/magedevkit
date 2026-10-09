"""kit.js (the UI recipes shared by the wizard and the dashboard) in a real
browser: tests/js/kit-tests.html runs its assertions and prints the result
into the page, read back with headless Chrome/Chromium's --dump-dom.

Skipped when no Chrome or Chromium is found (Linux, or Windows Chrome from
WSL). No Node needed."""

import html
import os
import re
import shutil
import subprocess
import unittest

import helpers

PAGE = os.path.join(helpers.REPO, "tests", "js", "kit-tests.html")
WINDOWS_CHROME = ("/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
                  "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe")


def find_browser():
    """(argv0, page URL) for a headless-capable browser, or (None, None)."""
    for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable"):
        path = shutil.which(name)
        if path:
            return path, "file://" + PAGE
    for path in WINDOWS_CHROME:
        if os.path.exists(path) and shutil.which("wslpath"):
            win = subprocess.run(["wslpath", "-w", PAGE], capture_output=True, text=True).stdout.strip()
            # \\wsl.localhost\Distro\… (a UNC path) → file://wsl.localhost/Distro/…
            return path, "file:" + win.replace("\\", "/")
    return None, None


class KitInBrowser(unittest.TestCase):
    def test_kit(self):
        browser, url = find_browser()
        if not browser:
            self.skipTest("no Chrome/Chromium found")
        p = subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--allow-file-access-from-files",
                            "--virtual-time-budget=10000", "--dump-dom", url],
                           capture_output=True, text=True, timeout=120)
        m = re.search(r'<pre id="result">(.*?)</pre>', p.stdout, re.S)
        self.assertIsNotNone(m, "the test page didn't run: %s" % (p.stderr[-500:] or p.stdout[-500:]))
        report = html.unescape(m.group(1))
        self.assertTrue(report.startswith("DONE OK"), report)
        self.assertGreaterEqual(int(report.split("\n")[0].split()[-1]), 20, report)


if __name__ == "__main__":
    unittest.main()
