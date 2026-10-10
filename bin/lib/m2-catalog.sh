# m2/ version catalog helpers — shared by the setup wizard (fresh-install
# version menu, stack pins for an existing project) and the version switcher
# (bin/local/version). Sourced, never run.
#
# Expects PLATFORM_DIR, ENV_FILE, MAGENTO_DIR, set_env_var (bin/lib/env.sh)
# and bin/lib/ui.sh's helpers.

# Patches Adobe has published that the repo's m2/ doesn't have yet, with the
# stack versions of the newest catalogued release on their line, checked
# against Adobe's requirements (bin/lib/web-wizard/releases.py writes them
# here). Same layout as m2/; a repo folder always wins.
M2_DERIVED_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/platform-releases/m2"

# releases_refresh — Adobe's release list for the version menus below: at
# most one download a day, 5 s at most; offline (or without python3) the
# menus are the m2/ catalog alone.
releases_refresh() {
    local note
    if command -v python3 >/dev/null 2>&1 && command -v timeout >/dev/null 2>&1; then
        note="$(timeout 15 python3 -I "$PLATFORM_DIR/bin/lib/web-wizard/releases.py" refresh --if-stale --timeout 5 \
            --catalog "$PLATFORM_DIR/m2" 2>/dev/null)" \
            || note="Couldn't check Adobe for new Magento releases — using the m2/ catalog only."
    else
        note="Couldn't check Adobe for new Magento releases (needs python3) — using the m2/ catalog only."
    fi
    [ -z "$note" ] || log "$note"
    return 0
}

# m2_patches BASE — its patch folders (p1, p2 …), the repo's and the derived
# ones, oldest first, each once.
m2_patches() {
    local d
    for d in "$PLATFORM_DIR/m2/$1" "$M2_DERIVED_DIR/$1"; do
        [ -d "$d" ] || continue
        find "$d" -mindepth 1 -maxdepth 1 -type d -name 'p*' -printf '%f\n'
    done | sort -uV
}

# m2_patch_dir BASE PATCH — that patch's folder (the repo's if it has one,
# else the derived one); blank if neither has a version.env.
m2_patch_dir() {
    local d
    for d in "$PLATFORM_DIR/m2/$1/$2" "$M2_DERIVED_DIR/$1/$2"; do
        [ -f "$d/version.env" ] && { printf '%s' "$d"; return 0; }
    done
    return 0
}

# m2_version_label VERSION — its menu label: the version, plus where the
# stack versions come from when it's a derived entry (the " — " sub-line the
# wizard page shows under the option).
m2_version_label() {
    local base="${1%%-*}" dir from
    [ "$1" != "$base" ] && dir="$(m2_patch_dir "$base" "${1#*-}")"
    if [ -n "${dir:-}" ] && [ "${dir#"$M2_DERIVED_DIR/"}" != "$dir" ]; then
        from="$(sed -n 's/^# Derived from \([^ ]*\).*/\1/p' "$dir/version.env" | head -1)"
        printf "%s — new: versions from %s, checked against Adobe's requirements" "$1" "${from:-the catalog}"
    else
        printf '%s' "$1"
    fi
}

# Version catalog: platform/m2/<version>/version.env, plus optional
# m2/<version>/p<N>/version.env security patches loaded on top (see
# m2/README.md), plus derived patches (M2_DERIVED_DIR) for that line. Menus
# are built from the folders, so adding one adds an entry; the newest —
# derived or not — is the default. Sets M2_BASE_DIR / M2_PATCH_DIR (blank
# for the base release).
choose_magento_version() {
    local versions patches labels choice base p
    mapfile -t versions < <(find "$PLATFORM_DIR/m2" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -V)
    [ "${#versions[@]}" -gt 0 ] || fail "No versions found under platform/m2/ — see m2/README.md."

    labels=("${versions[@]}")
    labels[-1]+=" (latest, default)"
    choice="$(prompt_menu "Which Magento version should be installed?" "${#versions[@]}" "${labels[@]}")"
    base="${versions[$((choice - 1))]}"
    M2_BASE_DIR="$PLATFORM_DIR/m2/$base"
    M2_PATCH_DIR=""

    # Option 1 is the base release; patches follow as 2.4.8-p1, -p2, ...
    # A derived one says where its stack versions come from.
    mapfile -t patches < <(m2_patches "$base")
    if [ "${#patches[@]}" -gt 0 ]; then
        labels=("$base — original release, no patches")
        for p in "${patches[@]}"; do labels+=("$(m2_version_label "$base-$p")"); done
        labels[-1]+=" (latest, default)"
        choice="$(prompt_menu "Which $base release? (patches are security/bug fixes on top of $base)" "${#labels[@]}" "${labels[@]}")"
        [ "$choice" -gt 1 ] && M2_PATCH_DIR="$(m2_patch_dir "$base" "${patches[$((choice - 2))]}")"
    fi
    return 0
}

# $1=KEY -> value from the patch's version.env, else the base's (blank if
# neither sets it). Read with grep, not sourced.
m2_get() {
    local f v
    for f in "${M2_PATCH_DIR:+$M2_PATCH_DIR/version.env}" "$M2_BASE_DIR/version.env"; do
        [ -n "$f" ] && [ -f "$f" ] || continue
        v="$(env_get "$f" "$1")"
        [ -n "$v" ] && { printf '%s' "$v"; return; }
    done
    return 0
}

# Stack services the m2 catalog can pin (see m2/README.md).
M2_STACK_KEYS=(PHP_VERSION MARIADB_VERSION OPENSEARCH_VERSION VALKEY_VERSION RABBITMQ_VERSION VARNISH_VERSION NGINX_VERSION COMPOSER_VERSION DEBIAN_RELEASE)

# Version of package $2 locked in composer.lock $1 (blank if absent).
lock_package_version() {
    awk -v pkg="$2" '$0 ~ "\"name\": \"" pkg "\"" {f=1; next}
         f && /"version":/{gsub(/[",]/, "", $2); sub(/^v/, "", $2); print $2; exit}' "$1"
}

# Installed Magento version as "version<TAB>source" (blank if unknown),
# e.g. "2.4.8-p3	composer.lock". Tries, in order: the locked product
# metapackage, the locked magento2-base (some projects don't lock the
# metapackage), then composer.json's product requirement — only when it
# pins one concrete version (^2.4.8 → 2.4.8; a range → unknown).
detect_magento_version() {
    local lock="$MAGENTO_DIR/composer.lock" json="$MAGENTO_DIR/composer.json" ver
    if [ -f "$lock" ]; then
        ver="$(lock_package_version "$lock" 'magento/product-(community|enterprise)-edition')"
        [ -n "$ver" ] || ver="$(lock_package_version "$lock" 'magento/magento2-base')"
        [ -n "$ver" ] && { printf '%s\tcomposer.lock\n' "$ver"; return 0; }
    fi
    [ -f "$json" ] || return 0
    ver="$(grep -m1 -oE '"magento/product-(community|enterprise)-edition"[[:space:]]*:[[:space:]]*"[^"]*"' "$json" \
        | sed -E 's/.*:[[:space:]]*"([^"]*)"/\1/; s/^[[:space:]=^~v]+//; s/[[:space:]]+$//')"
    [[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-p[0-9]+)?$ ]] && printf '%s\tcomposer.json\n' "$ver"
    return 0
}

# PHP major.minor the project pins in composer.json's config.platform.php
# (e.g. "8.4.18" -> "8.4"), blank if it doesn't pin one. Its composer.lock
# was resolved against that version, so it outranks the m2 catalog's
# "highest PHP Adobe supports" for an existing project.
project_php_version() {
    local json="$MAGENTO_DIR/composer.json"
    [ -f "$json" ] || return 0
    awk '/"platform"[[:space:]]*:[[:space:]]*\{/ {f=1; next}
         f && /"php"[[:space:]]*:/ {if (match($0, /[0-9]+\.[0-9]+/)) print substr($0, RSTART, RLENGTH); exit}
         f && /\}/ {exit}' "$json"
}

# The version apply_m2_stack_pins should use for stack key $1: the m2
# catalog's, except PHP_VERSION follows PROJECT_PHP (set for existing
# projects that pin config.platform.php) when there is one.
stack_pin() {
    if [ "$1" = PHP_VERSION ] && [ -n "${PROJECT_PHP:-}" ]; then
        printf '%s' "$PROJECT_PHP"
    else
        m2_get "$1"
    fi
}

# "2.4.8-p3" -> sets M2_BASE_DIR/M2_PATCH_DIR if the catalog has that
# folder (or a derived one); returns 1 otherwise. A patch with neither (e.g.
# a newer -p6 than the catalog knows, offline) falls back to its base
# release's pins — stack requirements rarely change between patches.
m2_select_detected() {
    local ver="$1" base patch=""
    base="${ver%%-*}"
    [ "$ver" != "$base" ] && patch="${ver#*-}"
    [ -f "$PLATFORM_DIR/m2/$base/version.env" ] || return 1
    M2_BASE_DIR="$PLATFORM_DIR/m2/$base"
    M2_PATCH_DIR=""
    if [ -n "$patch" ]; then
        M2_PATCH_DIR="$(m2_patch_dir "$base" "$patch")"
        if [ -z "$M2_PATCH_DIR" ]; then
            log "⚠ No m2/ catalog entry for $ver — using $base's stack versions."
        fi
    fi
    return 0
}

# Human name for a stack key (for messages and the wizard page).
stack_name() {
    case "$1" in
        PHP_VERSION) echo PHP ;;
        MARIADB_VERSION) echo MariaDB ;;
        OPENSEARCH_VERSION) echo OpenSearch ;;
        VALKEY_VERSION) echo Valkey ;;
        RABBITMQ_VERSION) echo RabbitMQ ;;
        VARNISH_VERSION) echo Varnish ;;
        NGINX_VERSION) echo Nginx ;;
        COMPOSER_VERSION) echo Composer ;;
        DEBIAN_RELEASE) echo "Debian (php/nginx images)" ;;
        *) echo "$1" ;;
    esac
}

# Every stack version in use, as "Name version · Name version …".
stack_summary() {
    local k out=()
    for k in "${M2_STACK_KEYS[@]}"; do
        [ -n "${!k:-}" ] && out+=("$(stack_name "$k") ${!k}")
    done
    local IFS='·'
    printf '%s' "${out[*]}" | sed 's/·/ · /g'
}

# Writes the selected catalog entry's stack pins into the env file (and
# this shell), guarding existing data volumes:
#   OpenSearch — a different major can't open the old index data; offers to
#                drop ${APP_NAME}-opensearch-data (rebuilt by reindex),
#                otherwise keeps the current version.
#   MariaDB    — never downgrades an existing ${APP_NAME}-db-data volume;
#                upgrades are handled by MARIADB_AUTO_UPGRADE.
# $1 = 1 to ask before applying (existing installs), 0 to apply directly.
apply_m2_stack_pins() {
    local ask="$1" k cur new changes=() details=() os_vol db_vol
    for k in "${M2_STACK_KEYS[@]}"; do
        new="$(stack_pin "$k")"
        cur="${!k:-}"
        [ -n "$new" ] && [ "$new" != "$cur" ] && changes+=("$k")
    done
    if [ "${#changes[@]}" -eq 0 ]; then
        log "✓ Stack versions already match the m2 catalog"
        return 0
    fi
    log "Stack versions recommended for this Magento version (m2 catalog, from Adobe's system requirements):"
    for k in "${changes[@]}"; do
        log "  $k: ${!k:-unset} → $(stack_pin "$k")"
        details+=("$(stack_name "$k")" "${!k:-not set} → $(stack_pin "$k")")
    done
    if [ "$ask" = 1 ] && ! prompt_yes_no "Apply the recommended stack versions for Magento $(m2_get MAGENTO_VERSION)?" Y "${details[@]}"; then
        log "Keeping the current stack versions."
        return 0
    fi

    os_vol="${APP_NAME:-app}-opensearch-data"
    db_vol="${APP_NAME:-app}-db-data"
    for k in "${changes[@]}"; do
        new="$(stack_pin "$k")"
        cur="${!k:-}"
        if [ "$k" = OPENSEARCH_VERSION ] && [ -n "$cur" ] && [ "${cur%%.*}" != "${new%%.*}" ] \
            && docker volume inspect "$os_vol" >/dev/null 2>&1; then
            log "OpenSearch $cur → $new is a major-version change; the existing index volume ($os_vol) can't be opened by it."
            if ! prompt_yes_no "Remove $os_vol so OpenSearch $new starts clean? (search is rebuilt by reindex)" Y; then
                log "Keeping OpenSearch $cur."
                continue
            fi
            "$PLATFORM_DIR/bin/platform" rm -sf opensearch >/dev/null 2>&1 || true
            docker volume rm "$os_vol" >/dev/null
            log "✓ Removed $os_vol"
        fi
        if [ "$k" = MARIADB_VERSION ] && [ -n "$cur" ] && docker volume inspect "$db_vol" >/dev/null 2>&1 \
            && ! version_lt "$cur" "$new"; then
            log "⚠ Not downgrading MariaDB $cur → $new: the existing database volume ($db_vol) was written by $cur and MariaDB can't downgrade it. Keeping $cur."
            continue
        fi
        set_env_var "$ENV_FILE" "$k" "$new"
        printf -v "$k" '%s' "$new"
        log "✓ $k=$new"
    done
    return 0
}

# Every catalog version, oldest first, one per line: each base release
# followed by its patches, derived ones included (2.4.8, 2.4.8-p1, ...,
# 2.4.9).
m2_list_versions() {
    local base p
    while IFS= read -r base; do
        [ -f "$PLATFORM_DIR/m2/$base/version.env" ] || continue
        printf '%s\n' "$base"
        while IFS= read -r p; do
            [ -n "$(m2_patch_dir "$base" "$p")" ] && printf '%s-%s\n' "$base" "$p"
        done < <(m2_patches "$base")
    done < <(find "$PLATFORM_DIR/m2" -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -V)
    return 0
}
