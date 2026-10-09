#!/usr/bin/env python3
"""Reads a Magento database dump's store URL settings without importing it.

  dump-config.py DUMP.sql.gz           → JSON on stdout
  dump-config.py --lines DUMP.sql.gz   → one "KIND<TAB>VALUE" line each, for
      bash (bin/lib/prod-urls.sh): found 1|0, error REASON (when the dump
      couldn't be read), base URL, media URL, cookie DOMAIN,
      scoped SCOPE/ID PATH=VALUE. Values with control
      characters are left out.

The DB refresh (bin/lib/db-refresh-engine.sh) uses it to learn which URLs
and cookie domain the dump came from, so they can be rewritten to the local
site without anyone typing them in.

Streams the gzip, finds the INSERTs into `core_config_data` (any table
prefix; single-row or extended inserts, on one line as mysqldump writes
them or one row per line as mariadb-dump 11 does) and stops once that
table's data is over, without reading the rest of the dump. Pure text parsing: nothing is executed or
imported. Standard library only.

Output:
  found     false when the dump has no core_config_data rows
  default   scope=default values of the paths below (missing → absent)
  base      distinct default base URLs (secure, unsecure, link URLs)
  media     distinct default media URLs, placeholders expanded; an empty
            value becomes <base_url>media/
  cookie    the default cookie domain ("" when unset)
  scoped    [{scope, scope_id, path, value}] base/media URLs set for a
            website or store view that differ from the default ones
"""

import gzip
import json
import re
import sys

PATHS = (
    "web/unsecure/base_url", "web/secure/base_url",
    "web/unsecure/base_link_url", "web/secure/base_link_url",
    "web/unsecure/base_media_url", "web/secure/base_media_url",
    "web/cookie/cookie_domain",
)
URL_PATHS = PATHS[:6]
MAX_VALUE = 2048
DEFAULT_COLUMNS = ["config_id", "scope", "scope_id", "path", "value", "updated_at"]

TABLE_RE = re.compile(r"`(\w*core_config_data)`")
CREATE_RE = re.compile(r"^CREATE TABLE `(\w*core_config_data)`")
INSERT_RE = re.compile(r"^INSERT INTO `(\w*core_config_data)`\s*(\(([^)]*)\))?\s*VALUES\s*", re.I)
COLUMN_RE = re.compile(r"^\s*`(\w+)`")


def parse_values(text):
    """Yields each row tuple (list of str/None) of an INSERT's VALUES list."""
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n,":
            i += 1
        if i >= n or text[i] != "(":
            return
        i += 1
        row = []
        while i < n:
            while i < n and text[i] in " \t\r\n":
                i += 1
            if text[i] == "'":
                i += 1
                buf = []
                while i < n:
                    c = text[i]
                    if c == "\\" and i + 1 < n:
                        nxt = text[i + 1]
                        buf.append({"n": "\n", "r": "\r", "t": "\t", "0": "\0", "Z": "\x1a"}.get(nxt, nxt))
                        i += 2
                    elif c == "'" and i + 1 < n and text[i + 1] == "'":
                        buf.append("'")
                        i += 2
                    elif c == "'":
                        i += 1
                        break
                    else:
                        buf.append(c)
                        i += 1
                row.append("".join(buf))
            else:
                j = i
                while j < n and text[j] not in ",)":
                    j += 1
                token = text[i:j].strip()
                row.append(None if token.upper() == "NULL" else token)
                i = j
            while i < n and text[i] in " \t\r\n":
                i += 1
            if i < n and text[i] == ",":
                i += 1
                continue
            if i < n and text[i] == ")":
                i += 1
                yield row
                break


def read_rows(path):
    """(columns, rows) of the dump's core_config_data INSERTs."""
    columns = list(DEFAULT_COLUMNS)
    rows = []
    in_create = in_data = False
    create_cols = []
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in iter(f.readline, ""):
            if in_create:
                m = COLUMN_RE.match(line)
                if m:
                    create_cols.append(m.group(1))
                elif line.lstrip().startswith(")"):
                    in_create = False
                    if create_cols:
                        columns = create_cols
                continue
            if CREATE_RE.match(line):
                in_create, create_cols = True, []
                continue
            m = INSERT_RE.match(line)
            if m:
                in_data = True
                if m.group(3):
                    columns = [c.strip().strip("`") for c in m.group(3).split(",")]
                # One statement: on this line (mysqldump), or one row per line
                # after "INSERT … VALUES" (mariadb-dump 11+) up to the ";".
                parts = [line[m.end():]]
                while not parts[-1].rstrip().endswith(";"):
                    nxt = f.readline()
                    if not nxt:
                        break
                    parts.append(nxt)
                rows.extend(parse_values("".join(parts)))
                continue
            if in_data and line.startswith(("INSERT INTO", "CREATE TABLE", "DROP TABLE", "UNLOCK TABLES", "/*!40000 ALTER TABLE")):
                if not TABLE_RE.search(line):
                    break  # the table's data is over: stop reading the dump
    return columns, rows


def resolve(value, base):
    """Expands {{unsecure_base_url}} / {{secure_base_url}} placeholders."""
    if value is None:
        return None
    return value.replace("{{unsecure_base_url}}", base.get("unsecure", "")).replace("{{secure_base_url}}", base.get("secure", ""))


def summarize(columns, rows):
    idx = {name: k for k, name in enumerate(columns)}
    need = ("scope", "scope_id", "path", "value")
    if not rows or not all(c in idx for c in need):
        return {"found": False}
    default, scoped = {}, []
    for row in rows:
        try:
            scope, scope_id, path, value = (row[idx[c]] for c in need)
        except IndexError:
            continue
        if path not in PATHS or (value is not None and len(value) > MAX_VALUE):
            continue
        if scope == "default":
            default[path] = value
        elif path in URL_PATHS and value:
            scoped.append({"scope": scope, "scope_id": scope_id, "path": path, "value": value})
    if not default.get("web/unsecure/base_url") and not default.get("web/secure/base_url"):
        return {"found": False}
    base = {"unsecure": default.get("web/unsecure/base_url") or "", "secure": default.get("web/secure/base_url") or ""}
    bases = []
    for key in ("web/unsecure/base_url", "web/secure/base_url", "web/unsecure/base_link_url", "web/secure/base_link_url"):
        v = resolve(default.get(key), base)
        if v and "{{" not in v and v not in bases:
            bases.append(v)
    media = []
    for key, which in (("web/unsecure/base_media_url", "unsecure"), ("web/secure/base_media_url", "secure")):
        fallback = base[which] or base["secure"] or base["unsecure"]
        v = resolve(default.get(key), base) or (fallback + "media/" if fallback else "")
        if v and "://" in v and "{{" not in v and v not in media:
            media.append(v)
    known = set(bases) | set(media)
    scoped = [s for s in scoped if resolve(s["value"], base) not in known]
    return {"found": True, "default": default, "base": bases, "media": media,
            "cookie": default.get("web/cookie/cookie_domain") or "", "scoped": scoped}


def as_lines(d):
    def ok(v):
        return isinstance(v, str) and not any(ord(c) < 32 for c in v)
    out = ["found\t%d" % (1 if d.get("found") else 0)]
    if ok(d.get("error")):
        out.append("error\t%s" % d["error"])
    for kind in ("base", "media"):
        out += ["%s\t%s" % (kind, v) for v in d.get(kind, []) if ok(v)]
    if d.get("found") and ok(d.get("cookie", "")):
        out.append("cookie\t%s" % d.get("cookie", ""))
    for sc in d.get("scoped", []):
        line = "%s/%s %s=%s" % (sc["scope"], sc["scope_id"], sc["path"], sc["value"])
        if ok(line):
            out.append("scoped\t" + line)
    return "\n".join(out)


def main():
    args = sys.argv[1:]
    lines = bool(args) and args[0] == "--lines"
    if lines:
        args = args[1:]
    if len(args) != 1:
        print("usage: dump-config.py [--lines] DUMP.sql.gz", file=sys.stderr)
        return 2
    try:
        columns, rows = read_rows(args[0])
        result = summarize(columns, rows)
    except (OSError, EOFError, gzip.BadGzipFile, UnicodeError) as e:
        result = {"found": False, "error": str(e)[:200]}
    print(as_lines(result) if lines else json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
