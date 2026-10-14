# Where one Magento instance's files live — sourced by every entry point
# right after it sets PLATFORM_DIR (the platform *code*: bin/, compose/,
# docker/, config templates). Sourced, never run.
#
# Two layouts:
#   instance  PLATFORM_INSTANCE_DIR points at ~/Sites/<name>/ (created by
#             bin/local/new-instance). The code is shared — one repo for
#             every instance — and everything that belongs to this instance
#             lives in its folder:
#               <name>/.platform-instance   marker (PLATFORM_REPO=<repo>)
#               <name>/.env                 its settings (APP_NAME, domain, ports, …)
#               <name>/magento/             Magento code
#               <name>/nginx/ <name>/certs/ generated vhost + mkcert certificate
#               <name>/logs/ <name>/db/     run logs, backups, snapshots
#               <name>/.wizard.lock         one setup/refresh/version/action at a time
#             Its bin/ holds small shims that set PLATFORM_INSTANCE_DIR and
#             exec the repo's scripts, so `cd ~/Sites/<name> && bin/magento …`
#             works as before.
#   legacy    No PLATFORM_INSTANCE_DIR: the original <project>/platform +
#             <project>/magento layout, state inside the platform checkout.
#
# Always recomputed from PLATFORM_INSTANCE_DIR alone (never trusts the other
# variables from the environment), then exported — docker compose
# interpolates MAGENTO_DIR / NGINX_CONF_DIR / CERTS_DIR / INSTANCE_DIR.

paths_fail() {
    echo "Error: $1" >&2
    exit 1
}

if [ -n "${PLATFORM_INSTANCE_DIR:-}" ]; then
    INSTANCE_DIR="$(cd "$PLATFORM_INSTANCE_DIR" 2>/dev/null && pwd)" \
        || paths_fail "Instance folder $PLATFORM_INSTANCE_DIR not found."
    [ -f "$INSTANCE_DIR/.platform-instance" ] \
        || paths_fail "$INSTANCE_DIR isn't an instance folder (no .platform-instance) — create one with: mdk new NAME"
    PLATFORM_MODE=instance
    ENV_FILE="$INSTANCE_DIR/.env"
    MAGENTO_DIR="$INSTANCE_DIR/magento"
    NGINX_CONF_DIR="$INSTANCE_DIR/nginx"
    CERTS_DIR="$INSTANCE_DIR/certs"
    export PLATFORM_INSTANCE_DIR="$INSTANCE_DIR"
else
    PLATFORM_MODE=legacy
    INSTANCE_DIR="$PLATFORM_DIR"
    ENV_FILE="$PLATFORM_DIR/env/.env.local"
    MAGENTO_DIR="$(cd "$PLATFORM_DIR/.." && pwd)/magento"
    NGINX_CONF_DIR="$PLATFORM_DIR/config/nginx-local"
    CERTS_DIR="$NGINX_CONF_DIR/certs"
fi
NGINX_CONF="$NGINX_CONF_DIR/default.conf"
LOGS_DIR="$INSTANCE_DIR/logs"
DB_DIR="$INSTANCE_DIR/db"
LOCK_FILE="$INSTANCE_DIR/.wizard.lock"
# The env file as messages should name it (".env" or "the env file").
ENV_NAME="${ENV_FILE#"$INSTANCE_DIR"/}"
export PLATFORM_MODE INSTANCE_DIR ENV_FILE ENV_NAME MAGENTO_DIR NGINX_CONF_DIR CERTS_DIR NGINX_CONF LOGS_DIR DB_DIR LOCK_FILE

# The platform repo itself (the shared code, no instance chosen) isn't a
# store: point at new-instance instead of setup there.
paths_is_shared_repo() {
    [ "$PLATFORM_MODE" = legacy ] && [ ! -f "$ENV_FILE" ] && [ "$(basename "$PLATFORM_DIR")" != platform ]
}

# Exits with a helpful message unless this instance has been set up.
require_env_file() {
    [ -f "$ENV_FILE" ] && return 0
    if paths_is_shared_repo; then
        paths_fail "This is the shared platform repo, not an instance — create one with: mdk new NAME — or run mdk inside an instance folder (or with -i NAME)."
    fi
    paths_fail "$ENV_NAME not found — set this instance up first: $(cmd_hint setup-wizard)"
}

# cmd_hint SUBCOMMAND [ARGS…] — the command a person should type for this
# instance, for messages and finish screens. Instance: "mdk -i NAME …"
# (works from any folder); legacy checkout: "cd <checkout> && bin/…".
#   cmd_hint magento cache:flush | cmd_hint refresh-wizard | cmd_hint mode production
cmd_hint() {
    local sub="$1"
    shift
    if [ "$PLATFORM_MODE" = instance ]; then
        printf 'mdk -i %s %s' "$(basename "$INSTANCE_DIR")" "$sub${*:+ $*}"
        return
    fi
    case "$sub" in
        setup|setup-wizard|refresh-wizard|version-wizard|pipeline|pipeline-wizard|provision|provision-wizard|rebuild|rebuild-wizard) printf 'cd %s && bin/local/%s' "$PLATFORM_DIR" "$sub${*:+ $*}" ;;
        mode|ownership|permissions) printf 'cd %s && bin/%s/%s' "$PLATFORM_DIR" "$sub" "$*" ;;
        sync-media) printf 'cd %s && bin/sync-media-to-s3' "$PLATFORM_DIR" ;;
        build|module) printf 'cd %s && bin/local/magento-%s' "$PLATFORM_DIR" "$sub${*:+ $*}" ;;
        *) printf 'cd %s && bin/%s' "$PLATFORM_DIR" "$sub${*:+ $*}" ;;
    esac
}
