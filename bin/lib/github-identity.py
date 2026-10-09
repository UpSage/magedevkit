#!/usr/bin/env python3
"""The commit name and email of a GitHub account, from GitHub's API answers.

  USER_JSON='<GET /user>' EMAILS_JSON='<GET /user/emails>' github-identity.py
      → "NAME<TAB>EMAIL" on stdout (nothing if USER_JSON isn't an account)

bin/lib/git-repo.sh github_identity fetches the two documents with curl
(the token never reaches this script or a command line) and passes them in
the environment. NAME is the account's display name, else its username.
EMAIL is its primary verified email when the token may read the email list,
else its public email, else the account's id+login@users.noreply.github.com
address — GitHub credits commits made with it to the account. Standard
library only; no network access here.
"""

import json
import os
import sys


def identity(user_json, emails_json):
    try:
        user = json.loads(user_json)
    except ValueError:
        return None
    if not isinstance(user, dict) or not user.get("login") or not user.get("id"):
        return None
    try:
        emails = json.loads(emails_json)
    except ValueError:
        emails = []
    if not isinstance(emails, list):
        emails = []
    login = user["login"]
    primary = next((e.get("email") for e in emails
                    if isinstance(e, dict) and e.get("primary") and e.get("verified")), None)
    email = primary or user.get("email") or "%s+%s@users.noreply.github.com" % (user["id"], login)
    name = " ".join((user.get("name") or login).split())  # no tabs or line breaks
    return name, email


def main():
    found = identity(os.environ.get("USER_JSON", ""), os.environ.get("EMAILS_JSON", "[]"))
    if found:
        print("%s\t%s" % found)
    return 0


if __name__ == "__main__":
    sys.exit(main())
