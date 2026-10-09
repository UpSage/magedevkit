"""The per-checkout lock: <platform>/.wizard.lock, the same file
bin/lib/ui.sh's wizard_lock takes for setup/refresh/version. Dashboard
writes take it with flock(2) (util-linux flock uses the same), so a
dashboard action and a wizard can never run at once in one checkout, while
different checkouts stay independent."""

import fcntl
import os

from webcore import OpError


def lock_path(e):
    return os.path.join(e.platform_dir, ".wizard.lock")


def _lock_text(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(300).strip() or None
    except OSError:
        return None


def probe_lock(e):
    """(held, text). Opens without truncating, so the holder's text survives."""
    path = lock_path(e)
    try:
        fd = os.open(path, os.O_RDWR)  # no lock file yet → nothing ever ran here
    except OSError:
        return False, None
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True, _lock_text(path)
    except OSError:
        return False, None
    finally:
        os.close(fd)  # closing releases a lock we took
    return False, None


def acquire_lock(e):
    """An fd holding the checkout lock, or OpError 409."""
    path = lock_path(e)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise OpError(409, "Another operation is running in this checkout.",
                      _lock_text(path) or "A setup, refresh, version switch or dashboard action holds .wizard.lock.")
    return fd
