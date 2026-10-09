# Per-repo git identity and login for magento/ — sourced, never run.
#
# Every store's repo stands on its own: its commit name/email live in its
# .git/config, and a GitHub login is saved (only when asked to) in its own
# .git/credentials (0600) through a repo-local credential helper. Nothing
# goes into the global git config, so a company repo and a personal repo on
# the same machine never share an identity or a token.
#
# The dashboard's Git tab (bin/lib/web-wizard/gitrepo.py) uses the same
# layout: repo_credentials_file is gitrepo.creds_file.
#
# Expects bin/lib/ui.sh (log, fail, prompt_value, with_default,
# ui_interactive).

# The file a repo's saved login lives in.
repo_credentials_file() {
    printf '%s/.git/credentials' "$1"
}

# repo_remember_login DIR HOST USER TOKEN — saves the login for this repo
# only: a repo-local `store --file=…` helper, the file created 0600.
repo_remember_login() {
    local dir="$1" host="$2" user="$3" token="$4" file
    file="$(repo_credentials_file "$dir")"
    ( umask 077; : >> "$file" )
    chmod 600 "$file"
    git -C "$dir" config --local credential.helper "store --file=$file" || return 1
    printf 'protocol=https\nhost=%s\nusername=%s\npassword=%s\n\n' "$host" "$user" "$token" \
        | git -C "$dir" credential approve
}

# An email git will accept for commits (something@domain.tld, no spaces).
valid_git_email() {
    [[ "$1" =~ ^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$ ]] && [ "${#1}" -le 254 ]
}

# A commit name: 1-100 characters, no line breaks, no < or > (they'd break
# the "Name <email>" author line).
valid_git_name() {
    [ -n "$1" ] && [ "${#1}" -le 100 ] && [[ "$1" != *[$'\n\r<>']* ]]
}

# github_identity TOKEN — "NAME<TAB>EMAIL" of the GitHub account the token
# belongs to (api.github.com, the same party the clone just talked to; how
# NAME and EMAIL are chosen: bin/lib/github-identity.py). Prints nothing if
# GitHub can't be asked. The token goes in a header read from stdin
# (curl -K -), never on a command line.
github_identity() {
    local token="$1" user emails
    user="$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
        | curl -fsS --max-time 15 -K - -H 'Accept: application/vnd.github+json' https://api.github.com/user 2>/dev/null)" \
        || return 0
    emails="$(printf 'header = "Authorization: Bearer %s"\n' "$token" \
        | curl -fsS --max-time 15 -K - -H 'Accept: application/vnd.github+json' https://api.github.com/user/emails 2>/dev/null)" \
        || emails="[]"
    USER_JSON="$user" EMAILS_JSON="$emails" python3 -I "$(dirname "${BASH_SOURCE[0]}")/github-identity.py"
}

# repo_ensure_identity DIR [NAME EMAIL] — makes sure this repo has its own
# commit name and email (DIR/.git/config). Given NAME and EMAIL (e.g. from
# github_identity), they're used as they are; otherwise it asks once.
# Without anyone to ask, it says how to set them later and carries on.
repo_ensure_identity() {
    local dir="$1" name="${2:-}" email="${3:-}"
    [ -d "$dir/.git" ] || return 0
    if [ -z "$(git -C "$dir" config --local user.email 2>/dev/null)" ] \
        && valid_git_name "$name" && valid_git_email "$email"; then
        git -C "$dir" config --local user.name "$name"
        git -C "$dir" config --local user.email "$email"
        log "✓ Commits in magento/ will be made as $name <$email> (your GitHub account; change it on the dashboard's Git tab)"
        return 0
    fi
    if [ -n "$(git -C "$dir" config --local user.email 2>/dev/null)" ]; then
        log "✓ Commits in magento/ are made as $(git -C "$dir" config --local user.name) <$(git -C "$dir" config --local user.email)>"
        return 0
    fi
    if ! ui_interactive; then
        log "⚠ magento/ has no commit identity of its own yet — set it in the dashboard's Git tab, or: git -C $dir config user.name 'Your Name' && git -C $dir config user.email you@example.com"
        return 0
    fi
    log "Commits in magento/ use their own name and email (this repo only — never your global git config)."
    while true; do
        name="$(prompt_value 'Name for commits in this repo (e.g. your GitHub username or full name)' 0)"
        valid_git_name "$name" && break
        log "The name can't be blank or contain < > or line breaks."
    done
    while true; do
        email="$(prompt_value 'Email for commits in this repo (the one this GitHub account uses)' 0)"
        valid_git_email "$email" && break
        log "'$email' isn't an email address."
    done
    git -C "$dir" config --local user.name "$name"
    git -C "$dir" config --local user.email "$email"
    log "✓ Commits in magento/ will be made as $name <$email>"
}
