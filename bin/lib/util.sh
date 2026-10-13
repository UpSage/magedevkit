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
# shares (bin/local/magento-build, bin/local/rebuild, bin/local/magento-module,
# the dashboard's ops.parse_composer / modules.update_specs): MODE is blank,
# install, update or require; packages look like vendor/package and only go
# with update; require (the Modules tab's updates) takes one or more
# vendor/package:constraint.
COMPOSER_PACKAGE_RE='^[a-z0-9][a-z0-9._-]*/[a-z0-9*][a-z0-9._*-]*$'
COMPOSER_SPEC_RE='^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*:[A-Za-z0-9.*^~|<>=!@ ,_-]+$'
composer_check() {
    local mode="$1" p
    shift
    case "$mode" in
        ""|install|update) ;;
        require)
            [ "$#" -gt 0 ] || fail "composer require needs vendor/package:constraint."
            for p in "$@"; do
                [[ "$p" =~ $COMPOSER_SPEC_RE ]] || fail "'$p' isn't vendor/package:constraint."
            done
            return 0 ;;
        *) fail "composer takes install, update or require (got '$mode')." ;;
    esac
    for p in "$@"; do
        [[ "$p" =~ $COMPOSER_PACKAGE_RE ]] || fail "'$p' isn't a vendor/package name."
    done
    [ "$#" -eq 0 ] || [ "$mode" = update ] || fail "Package names only go with composer update."
}

# require_php_running — stops unless this stack's php container runs (every
# bin/magento / bin/composer step runs in it; nothing here starts it).
require_php_running() {
    "$PLATFORM_DIR/bin/platform" ps --status running --services 2>/dev/null | grep -qx php \
        || fail "The php container isn't running — start the environment first (this doesn't start or recreate containers)."
}

# module_check MODULE... — Magento module names (Vendor_Module; a lowercase
# vendor exists too: dotdigitalgroup_Email), at least one. The dashboard uses
# the same rule (ops.MODULE_RE).
MODULE_NAME_RE='^[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9]+$'
module_check() {
    local m
    [ "$#" -gt 0 ] || fail "Name at least one module (Vendor_Module)."
    for m in "$@"; do
        [[ "$m" =~ $MODULE_NAME_RE ]] || fail "'$m' isn't a module name (Vendor_Module)."
    done
}

# Instance / project names (APP_NAME, the instance folder): Docker's project
# and volume name rules minus dots — they would split the edge proxy's
# Traefik label keys — and at most 40 characters. The dashboard uses the
# same rule (dashboard.py new_instance, dashboard.js NAME_RE).
INSTANCE_NAME_RE='^[a-z0-9][a-z0-9_-]{0,39}$'
valid_instance_name() {
    [[ "$1" =~ $INSTANCE_NAME_RE ]]
}

# AWS names users type, checked before they reach a file, a command line or
# an AWS call. bin/lib/aws-credentials.py and the dashboard use the same rules.
#   valid_aws_profile    a MageDevKit connection name (letters, digits, . _ -)
#   valid_aws_region     us-east-2, eu-central-1, ap-southeast-3, …
#   valid_account_id     the 12-digit AWS account number
#   valid_env_name       a deploy environment (dev, staging, prod-eu, …) — the
#                        pipeline wizard's rule, 2–16 characters
#   valid_s3_uri         s3://bucket/key — the only dump source an AWS refresh takes
ENV_NAME_RE='^[a-z][a-z0-9-]{1,15}$'
valid_aws_profile() {
    [[ "$1" =~ ^[A-Za-z0-9._-]{1,64}$ ]]
}
valid_aws_region() {
    [[ "$1" =~ ^[a-z]{2}(-gov|-iso[a-z]*)?-[a-z]+-[0-9]$ ]]
}
valid_account_id() {
    [[ "$1" =~ ^[0-9]{12}$ ]]
}
valid_env_name() {
    [[ "$1" =~ $ENV_NAME_RE ]]
}
valid_s3_uri() {
    [[ "$1" =~ ^s3://[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]/[A-Za-z0-9!_.*\'\(\)/-]+$ ]] && [[ "$1" != *..* ]]
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
