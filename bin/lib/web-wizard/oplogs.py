"""Log text for the dashboard: tails of large files and redaction of every
secret this environment knows about (its env file, env.php, auth.json) plus
generic patterns (webcore.SECRET_PATTERNS). Every log view goes through
Redactor before it leaves the server."""

import json
import os
import re

import webcore

LOG_BYTES_MAX = 1 << 20


class Redactor:
    """Literal secrets from the checkout + generic patterns. Short literal
    values (< 8 characters, e.g. the stock "magento" passwords) are left to
    the patterns: replacing a word like that everywhere would mask most of a
    Magento log."""

    def __init__(self, e):
        vals = [v for v in e.secret_values() if v and v != "CHANGE_ME"]
        vals += self._env_php(e.magento_dir) + self._auth_json(e.magento_dir)
        self.secrets = {v for v in vals if len(v) >= 8}

    @staticmethod
    def _env_php(mdir):
        try:
            with open(os.path.join(mdir, "app", "etc", "env.php"), encoding="utf-8", errors="replace") as f:
                text = f.read(262144)
        except OSError:
            return []
        vals = re.findall(r"""['"](?:key|password|secret|token|access_key|secret_key)['"]\s*=>\s*['"]([^'"]+)['"]""", text)
        # crypt keys may be a newline-separated list
        return [p for v in vals for p in v.replace("\\n", "\n").split("\n") if p]

    @staticmethod
    def _auth_json(mdir):
        try:
            with open(os.path.join(mdir, "auth.json"), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            return []
        vals = []

        # Every string in auth.json is a credential (repo.magento.com's
        # "username" is the access key pair's public half).
        def walk(x):
            if isinstance(x, str):
                vals.append(x)
            elif isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):  # e.g. a token list some repositories use
                for v in x:
                    walk(v)
        walk(data)
        return vals

    def __call__(self, text):
        out = []
        for line in text.split("\n"):
            line = webcore.redact_literal(webcore.clean_line(line), self.secrets)
            out.append(webcore.redact_patterns(line))
        return "\n".join(out)


def tail_file(path, lines):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - LOG_BYTES_MAX))
            data = f.read()
    except OSError:
        return None
    text = data.decode("utf-8", "replace")
    return "\n".join(text.splitlines()[-lines:])
