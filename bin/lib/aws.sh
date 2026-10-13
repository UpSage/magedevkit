# MageDevKit's own AWS connections — sourced, never run. Needs ui.sh (log,
# fail) and, for ensure_aws_cli, tools.sh.
#
# Credential boundaries: the logins MageDevKit uses to create and control AWS
# environments live in their own files,
#   ~/.config/magedevkit/aws/config and credentials   (0600, folder 0700)
# never in ~/.aws, and never in an instance's .env, env.php or the Magento
# repo. Every call goes through mdk_aws, which points the AWS CLI at those
# files for that one command and drops any AWS_* login from the environment,
# so a key exported in your shell can't act for a connection, and nothing is
# exported for other scripts (Magento, composer, the containers) to see.
# The store's own AWS access (S3 media keys in .env, profiles in ~/.aws) is a
# separate thing and never used here. bin/lib/aws-credentials.py edits the
# files; bin/lib/aws-profiles.py checks which account each one reaches.

MDK_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/magedevkit"
MDK_AWS_DIR="${MDK_AWS_DIR:-$MDK_CONFIG_DIR/aws}"
MDK_AWS_CONFIG="$MDK_AWS_DIR/config"
MDK_AWS_CREDENTIALS="$MDK_AWS_DIR/credentials"
export MDK_AWS_DIR
AWS_HELPERS_DIR="$(dirname "${BASH_SOURCE[0]}")"

# mdk_aws NAME ARGS… — the AWS CLI as MageDevKit connection NAME, no pager.
# MDK_AWS_TIMEOUT=N (seconds) limits one call.
mdk_aws() {
    local name="$1"
    shift
    ${MDK_AWS_TIMEOUT:+timeout "$MDK_AWS_TIMEOUT"} env \
        -u AWS_ACCESS_KEY_ID -u AWS_SECRET_ACCESS_KEY -u AWS_SESSION_TOKEN -u AWS_SECURITY_TOKEN \
        -u AWS_DEFAULT_PROFILE -u AWS_CREDENTIAL_EXPIRATION -u AWS_ROLE_ARN -u AWS_WEB_IDENTITY_TOKEN_FILE \
        AWS_CONFIG_FILE="$MDK_AWS_CONFIG" AWS_SHARED_CREDENTIALS_FILE="$MDK_AWS_CREDENTIALS" \
        AWS_PROFILE="$name" AWS_PAGER="" aws "$@"
}

# mdk_aws_names — MageDevKit's connection names, one per line.
mdk_aws_names() {
    python3 "$AWS_HELPERS_DIR/aws-credentials.py" list \
        | python3 -c 'import json, sys; [print(c["name"]) for c in json.load(sys.stdin)]'
}

# mdk_aws_has NAME — whether MageDevKit has a connection called NAME.
mdk_aws_has() {
    mdk_aws_names | grep -qxF -- "$1"
}

# mdk_aws_for_account ACCOUNT — the connection this computer uses for ACCOUNT
# (blank when none was chosen yet, or it has been removed since).
mdk_aws_for_account() {
    valid_account_id "${1:-}" || return 0
    python3 "$AWS_HELPERS_DIR/aws-credentials.py" which "$1" 2>/dev/null || true
}

# mdk_aws_remember ACCOUNT NAME — use connection NAME for ACCOUNT from now on.
mdk_aws_remember() {
    python3 "$AWS_HELPERS_DIR/aws-credentials.py" remember "$1" "$2" || fail "Couldn't save which connection reaches account $1."
}

# mdk_aws_account NAME — the 12-digit account NAME reaches right now (blank
# when it doesn't work; the caller says why that matters).
mdk_aws_account() {
    local a
    a="$(MDK_AWS_TIMEOUT=25 mdk_aws "$1" sts get-caller-identity --query Account --output text 2>/dev/null)" || true
    valid_account_id "$a" && printf '%s' "$a"
    return 0
}

# mdk_aws_require_account NAME ACCOUNT — stops unless connection NAME reaches
# ACCOUNT right now. Every change to an AWS environment checks this first,
# so a connection to the wrong account can never touch it.
mdk_aws_require_account() {
    local got
    got="$(mdk_aws_account "$1")"
    [ -n "$got" ] || fail "MageDevKit connection '$1' doesn't work right now — check it with: mdk aws list (an SSO login may need: mdk aws login $1)."
    [ "$got" = "$2" ] || fail "MageDevKit connection '$1' reaches account $got, not $2 — nothing was changed. Choose the connection for account $2."
}

# ensure_aws_cli — installs the AWS CLI (signed, into ~/.local) when missing.
ensure_aws_cli() {
    command -v aws >/dev/null 2>&1 && return 0
    declare -F install_aws_cli >/dev/null || source "$AWS_HELPERS_DIR/tools.sh"
    install_aws_cli
}
