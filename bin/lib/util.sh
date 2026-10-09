# Small helpers every flow can use — loaded by bin/lib/bootstrap.sh.
# Sourced, never run. composer_check uses fail from bin/lib/ui.sh.

# version_lt A B — true when version A sorts before B (sort -V), false when equal.
version_lt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

# version_ge A B — true when A is B or newer.
version_ge() {
    ! version_lt "$1" "$2"
}

# file_sha256 FILE — its SHA-256 (blank if it can't be read).
file_sha256() {
    sha256sum "$1" 2>/dev/null | cut -d' ' -f1
}

# composer_check MODE [PACKAGE...] — the rules every composer entry point
# shares (bin/local/magento-build, bin/local/rebuild, the dashboard's
# ops.parse_composer): MODE is blank, install or update; packages look like
# vendor/package and only go with update.
COMPOSER_PACKAGE_RE='^[a-z0-9][a-z0-9._-]*/[a-z0-9*][a-z0-9._*-]*$'
composer_check() {
    local mode="$1" p
    shift
    case "$mode" in
        ""|install|update) ;;
        *) fail "composer takes install or update (got '$mode')." ;;
    esac
    for p in "$@"; do
        [[ "$p" =~ $COMPOSER_PACKAGE_RE ]] || fail "'$p' isn't a vendor/package name."
    done
    [ "$#" -eq 0 ] || [ "$mode" = update ] || fail "Package names only go with composer update."
}

# Instance / project names (APP_NAME, the instance folder): Docker's project
# and volume name rules minus dots — they would split the edge proxy's
# Traefik label keys — and at most 40 characters. The dashboard uses the
# same rule (dashboard.py new_instance, dashboard.js NAME_RE).
INSTANCE_NAME_RE='^[a-z0-9][a-z0-9_-]{0,39}$'
valid_instance_name() {
    [[ "$1" =~ $INSTANCE_NAME_RE ]]
}

# project_owner NAME [THIS_DIR] — the folder of the stack already using the
# compose project NAME-platform, unless it is THIS_DIR (blank: nobody).
# platform.instance_dir names the instance folder; stacks started before
# that label existed only have compose's working_dir (<checkout>/compose).
project_owner() {
    local row dir
    row="$(docker ps -a --filter "label=com.docker.compose.project=$1-platform" \
        --format '{{.Label "platform.instance_dir"}}|{{.Label "com.docker.compose.project.working_dir"}}' | sort -u | head -1)"
    [ -n "$row" ] || return 0
    dir="${row%%|*}"
    if [ -z "$dir" ] || [ "$dir" = "<no value>" ]; then
        dir="$(dirname "${row#*|}")"
    fi
    [ "$dir" != "${2:-}" ] && echo "$dir"
    return 0
}
