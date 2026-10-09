# File ownership/permissions engine for magento/, per Adobe's
# file-system-permissions guide:
# https://experienceleague.adobe.com/en/docs/commerce-operations/configuration-guide/deployment/file-system-permissions
# Sourced by bin/permissions/*, bin/ownership/*, bin/mode/* (via
# bin/lib/mode-engine.sh). Never invoke directly. Expects PLATFORM_DIR.
#
#   perm_apply   Ownership + permissions for FILE_OWNERSHIP (the env file):
#                  single    — everything owned by you, u+w on the writable
#                              dirs (Adobe's "one owner" commands).
#                  two-owner — you own the code; the writable dirs are
#                              you:www-data with setgid so php-fpm (www-data)
#                              and CLI (you, group www-data) share them
#                              (Adobe's "two owners"). Needs sudo for
#                              chown/usermod.
#   perm_lock    Production hardening: removes write access (u-w single,
#                g-w two-owner) from everything except var/, generated/,
#                pub/media/ (and .git/), then re-locks var/view_preprocessed,
#                generated/code, generated/metadata. Deny-by-default rather
#                than a fixed list, so no stray path (vendor/, auth.json, ...)
#                stays open.
#   perm_unlock  The reverse, for composer/compile/maintenance.
#
# app/etc/env.php (DB password, crypt key) always loses all "other" access.
# Each perm_* runs in a subshell (cd + errexit stay contained) and reads
# FILE_OWNERSHIP fresh, so it always acts on the model currently configured.

# Shared helpers, incl. a sudo() that asks for the password on the web
# wizard page when running under it (plain sudo otherwise).
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/ui.sh"
# ENV_FILE / MAGENTO_DIR / … for this instance (or legacy checkout).
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/paths.sh"


# cd into magento/ and resolve identity — called at the top of each perm_*.
perm_init() {
    require_env_file
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/identity.sh"
    [ -d "$MAGENTO_DIR" ] || fail "$MAGENTO_DIR not found."
    APP_ROOT="$(cd "$MAGENTO_DIR" && pwd)"
    cd "$APP_ROOT"
    [ -f bin/magento ] && [ -d app ] || fail "$APP_ROOT doesn't look like a Magento checkout."
    ME_UID="$(id -u)"
    ME_GID="$(id -g)"
    mapfile -t WRITABLE < <(existing var generated pub/static pub/media app/etc)
    case "$FILE_OWNERSHIP" in
        single) WBIT=u ;;
        two-owner) WBIT=g ;;
    esac
}

as_owner() {
    "$@" 2>/dev/null && return
    log "Retrying with sudo: $*"
    sudo "$@"
}
existing() {
    local p
    for p in "$@"; do [ -e "$p" ] && printf '%s\n' "$p"; done
    return 0
}
apply_single() {
    if [ "${#WRITABLE[@]}" -gt 0 ] && [ -n "$(find "${WRITABLE[@]}" ! -user "$ME_UID" -print -quit)" ]; then
        log "Some writable dirs aren't owned by you (left over from two-owner?) — chowning back (sudo) ..."
        sudo chown -R "$ME_UID:$ME_GID" "${WRITABLE[@]}"
    fi
    mapfile -t paths < <(existing var generated vendor pub/static pub/media app/etc)
    log "Single owner: u+w on ${paths[*]}"
    as_owner find "${paths[@]}" \( -type f -or -type d \) -exec chmod u+w {} +
    chmod u+x bin/magento
}
apply_two_owner() {
    local me
    me="$(id -un)"
    if ! id -nG "$me" | tr ' ' '\n' | grep -qx www-data; then
        log "Adding $me to the www-data group (sudo) — open a new shell (or run 'wsl --shutdown' from Windows) afterwards so it takes effect for your editor/shell."
        sudo usermod -aG www-data "$me"
    fi
    if [ "${#WRITABLE[@]}" -gt 0 ] && [ -n "$(find "${WRITABLE[@]}" \( ! -user "$ME_UID" -o ! -group "$WWW_DATA_GID" \) -print -quit)" ]; then
        log "Setting ownership $me:www-data on ${WRITABLE[*]} (sudo) ..."
        sudo chown -R "$ME_UID:$WWW_DATA_GID" "${WRITABLE[@]}"
    fi
    log "Two owners: setgid + group write on ${WRITABLE[*]}"
    mapfile -t private < <(existing var generated)
    mapfile -t public < <(existing pub/static pub/media)
    if [ "${#private[@]}" -gt 0 ]; then
        as_owner find "${private[@]}" -type d -exec chmod 2770 {} +
        as_owner find "${private[@]}" -type f -exec chmod 660 {} +
    fi
    if [ "${#public[@]}" -gt 0 ]; then
        as_owner find "${public[@]}" -type d -exec chmod 2775 {} +
        as_owner find "${public[@]}" -type f -exec chmod 664 {} +
    fi
    if [ -d app/etc ]; then
        as_owner find app/etc -type f -exec chmod g+w {} +
        as_owner find app/etc -type d -exec chmod g+ws {} +
    fi
    chmod u+x bin/magento
}
chmod_code() {
    as_owner find . \
        \( -path ./.git -o -path ./var -o -path ./generated -o -path ./pub/media \) -prune \
        -o \( -type f -o -type d \) -exec chmod "$1" {} +
}
chmod_compiled() {
    local p
    for p in var/view_preprocessed generated/code generated/metadata; do
        [ -e "$p" ] && as_owner find "$p" \( -type d -or -type f \) -exec chmod "$1" {} +
    done
    return 0
}
protect_env_php() {
    [ -f app/etc/env.php ] && as_owner chmod o-rwx app/etc/env.php
    return 0
}

perm_apply() (
    set -euo pipefail
    perm_init
    "apply_${FILE_OWNERSHIP//-/_}"
    protect_env_php
    log "✓ Permissions applied ($FILE_OWNERSHIP)."
)

perm_lock() (
    set -euo pipefail
    perm_init
    log "Locking code for production: ${WBIT}-w everywhere except var/, generated/, pub/media/ ..."
    chmod_code "${WBIT}-w"
    chmod_compiled "${WBIT}-w"
    protect_env_php
    log "✓ Permissions locked ($FILE_OWNERSHIP)."
)

perm_unlock() (
    set -euo pipefail
    perm_init
    log "Unlocking: ${WBIT}+w ..."
    if [ "$FILE_OWNERSHIP" = "two-owner" ]; then
        # Adobe's own two-owner "make writable" list — not the whole tree, so
        # files that were never group-writable aren't widened.
        mapfile -t paths < <(existing app/code lib var generated vendor pub/static pub/media app/etc)
        as_owner find "${paths[@]}" \( -type d -or -type f \) -exec chmod g+w {} +
    else
        chmod_code u+w
        chmod_compiled u+w
    fi
    protect_env_php
    log "✓ Permissions unlocked ($FILE_OWNERSHIP)."
)
