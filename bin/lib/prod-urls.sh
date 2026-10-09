# The production URLs a DB dump is rewritten *from* — read from the dump
# itself (bin/lib/dump-config.py) instead of typed in. Sourced by the DB
# refresh (bin/lib/db-refresh-engine.sh), never run.
#
# Expects bin/lib/ui.sh (log, fail, prompt_value, with_default,
# ui_interactive), bin/lib/env.sh (set_env_var, normalize_base_url,
# valid_cookie_domain), ENV_FILE, ENV_NAME, APP_DOMAIN and the saved
# PROD_BASE_URL / PROD_MEDIA_URL / PROD_COOKIE_DOMAIN (if any).
#
# Sets:
#   PROD_BASE_URLS[]   every production base URL to map to the local one
#                      (unsecure, secure, link URLs — they can differ)
#   PROD_MEDIA_URLS[]  every production media URL (CDN included)
#   PROD_COOKIE_DOMAIN the production cookie domain ("" = none set)
# and saves the main ones to the env file as PROD_* for reference.

# detect_prod_urls DUMP.sql.gz — fills the variables above. The dump's own
# values win; saved values are the fallback; asked only when neither exists.
detect_prod_urls() {
    local dump="$1" kind value url line found=0 cookie_found=0 cookie="" error=""
    local -a scoped=()
    PROD_BASE_URLS=()
    PROD_MEDIA_URLS=()
    while IFS=$'\t' read -r kind value; do
        case "$kind" in
            found) found="$value" ;;
            base) url="$(normalize_base_url "$value")" && PROD_BASE_URLS+=("$url") ;;
            media) url="$(normalize_base_url "$value")" && PROD_MEDIA_URLS+=("$url") ;;
            cookie)
                if [ -z "$value" ] || valid_cookie_domain "$value"; then cookie_found=1; cookie="$value"; fi ;;
            scoped) scoped+=("$value") ;;
            error) error="$value" ;;
        esac
    done < <(python3 -I "$(dirname "${BASH_SOURCE[0]}")/dump-config.py" --lines "$dump")

    if [ "$found" = 1 ] && [ "${#PROD_BASE_URLS[@]}" -gt 0 ]; then
        [ "$cookie_found" = 1 ] && PROD_COOKIE_DOMAIN="$cookie"
        log "The dump is from ${PROD_BASE_URLS[*]} (media: ${PROD_MEDIA_URLS[*]:-from the base URL}; cookie domain: ${PROD_COOKIE_DOMAIN:-none})."
        if [ -n "${PROD_BASE_URL:-}" ] && ! printf '%s\n' "${PROD_BASE_URLS[@]}" | grep -qxF "$PROD_BASE_URL"; then
            log "Note: the saved PROD_BASE_URL was $PROD_BASE_URL — using the dump's own URLs instead."
        fi
        if [ "${#scoped[@]}" -gt 0 ]; then
            log "⚠ Some websites/store views have their own URLs — these keep their production values after the import:"
            for line in "${scoped[@]}"; do log "    $line"; done
            log "  Set them for this site afterwards, e.g.: $(cmd_hint magento config:set --scope=websites --scope-code=CODE web/secure/base_url https://${APP_DOMAIN}/)"
        fi
        save_prod_urls
        return 0
    fi

    if [ -n "${PROD_BASE_URL:-}" ]; then
        log "⚠ Couldn't read the store URLs from the dump${error:+ ($error)} — using the saved ones ($PROD_BASE_URL)."
        PROD_BASE_URLS=("$PROD_BASE_URL")
        [ -n "${PROD_MEDIA_URL:-}" ] && PROD_MEDIA_URLS=("$PROD_MEDIA_URL")
        return 0
    fi

    log "Couldn't read the store URLs from the dump (${error:-no core_config_data in it}), so they're needed once."
    ask_prod_urls
    PROD_BASE_URLS=("$PROD_BASE_URL")
    PROD_MEDIA_URLS=("$PROD_MEDIA_URL")
}

# The main values, written to the env file for reference and as the fallback
# for a dump that doesn't carry them.
save_prod_urls() {
    local base="${PROD_BASE_URLS[0]}" media="${PROD_MEDIA_URLS[0]:-}" u
    # The https ones when there are both (that's what the live site serves).
    for u in "${PROD_BASE_URLS[@]}"; do
        case "$u" in https://*) base="$u"; break ;; esac
    done
    for u in "${PROD_MEDIA_URLS[@]}"; do
        case "$u" in https://*) media="$u"; break ;; esac
    done
    PROD_BASE_URL="$base"
    PROD_MEDIA_URL="${media:-${base}media/}"
    set_env_var "$ENV_FILE" PROD_BASE_URL "$PROD_BASE_URL"
    set_env_var "$ENV_FILE" PROD_MEDIA_URL "$PROD_MEDIA_URL"
    set_env_var "$ENV_FILE" PROD_COOKIE_DOMAIN "${PROD_COOKIE_DOMAIN:-}"
}

# True when every production base URL is already this site's own domain
# (the dump was prepared for local use already).
prod_urls_are_local() {
    local u host
    for u in "${PROD_BASE_URLS[@]}"; do
        host="${u#*://}"; host="${host%%/*}"; host="${host%%:*}"
        [ "$host" = "$APP_DOMAIN" ] || return 1
    done
    return 0
}

# Asks for the live site's URL, media URL and cookie domain — only when the
# dump doesn't carry them. Validated; without anyone to ask, it stops.
ask_prod_urls() {
    local input candidate normalized
    while true; do
        input="$(prompt_value "$(with_default "What's the live site's URL? (e.g. https://example.com/)" "${PROD_BASE_URL:-}")" 0)"
        candidate="${input:-${PROD_BASE_URL:-}}"
        normalized="$(normalize_base_url "$candidate")" && break
        ui_interactive || fail "The dump doesn't say which site it's from, and there's nobody to ask — set PROD_BASE_URL (e.g. https://example.com/) in $ENV_NAME."
        log "'$candidate' isn't a URL — use the full address, e.g. https://example.com/"
    done
    PROD_BASE_URL="$normalized"
    while true; do
        input="$(prompt_value "$(with_default "Where does the live site load media from? (its media/CDN URL)" "${PROD_MEDIA_URL:-${PROD_BASE_URL}media/}")" 0)"
        candidate="${input:-${PROD_MEDIA_URL:-${PROD_BASE_URL}media/}}"
        normalized="$(normalize_base_url "$candidate")" && break
        ui_interactive || fail "PROD_MEDIA_URL '$candidate' isn't a URL — set it in $ENV_NAME."
        log "'$candidate' isn't a URL — e.g. https://example.com/media/ or https://cdn.example.com/media/"
    done
    PROD_MEDIA_URL="$normalized"
    while true; do
        input="$(prompt_value "$(with_default "What cookie domain does the live site use? (e.g. .example.com — blank if none)" "${PROD_COOKIE_DOMAIN:-}")" 0)"
        candidate="${input:-${PROD_COOKIE_DOMAIN:-}}"
        { [ -z "$candidate" ] || valid_cookie_domain "$candidate"; } && break
        ui_interactive || fail "PROD_COOKIE_DOMAIN '$candidate' isn't a domain — set it in $ENV_NAME."
        log "'$candidate' isn't a domain — e.g. .example.com or www.example.com"
    done
    PROD_COOKIE_DOMAIN="$candidate"
    set_env_var "$ENV_FILE" PROD_BASE_URL "$PROD_BASE_URL"
    set_env_var "$ENV_FILE" PROD_MEDIA_URL "$PROD_MEDIA_URL"
    set_env_var "$ENV_FILE" PROD_COOKIE_DOMAIN "$PROD_COOKIE_DOMAIN"
}
