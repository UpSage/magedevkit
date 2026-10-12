# The production URLs a DB dump is rewritten *from* — read from the dump
# itself (bin/lib/dump-config.py) instead of typed in. Sourced by the DB
# refresh (bin/lib/db-refresh-engine.sh), never run.
#
# Expects bin/lib/ui.sh (log, fail, cancel, prompt_value, prompt_yes_no,
# with_default, ui_interactive), bin/lib/env.sh (set_env_var,
# normalize_base_url, valid_cookie_domain), ENV_FILE, ENV_NAME, APP_DOMAIN
# and the saved PROD_BASE_URL / PROD_MEDIA_URL / PROD_COOKIE_DOMAIN (if any).
#
# Sets:
#   PROD_BASE_URLS[]   every production base URL to map to the local one
#                      (unsecure, secure, link URLs — they can differ)
#   PROD_MEDIA_URLS[]  every production media URL (CDN included)
#   PROD_COOKIE_DOMAIN the production cookie domain ("" = none set)
#   PROD_SCOPED_URLS[] website/store-view URLs that are not rewritten
#   PROD_URLS_FROM     where they came from: dump | saved | typed
# confirm_prod_urls then shows the rewrite and, once it's confirmed, saves
# the main ones to the env file as PROD_* for reference.

# detect_prod_urls DUMP.sql.gz — fills the variables above. The dump's own
# values win; saved values are the fallback; asked only when neither exists.
detect_prod_urls() {
    local dump="$1" kind value url line found=0 cookie_found=0 cookie="" error=""
    PROD_BASE_URLS=()
    PROD_MEDIA_URLS=()
    PROD_SCOPED_URLS=()
    while IFS=$'\t' read -r kind value; do
        case "$kind" in
            found) found="$value" ;;
            base) url="$(normalize_base_url "$value")" && PROD_BASE_URLS+=("$url") ;;
            media) url="$(normalize_base_url "$value")" && PROD_MEDIA_URLS+=("$url") ;;
            cookie)
                if [ -z "$value" ] || valid_cookie_domain "$value"; then cookie_found=1; cookie="$value"; fi ;;
            scoped) PROD_SCOPED_URLS+=("$value") ;;
            error) error="$value" ;;
        esac
    done < <(python3 -I "$(dirname "${BASH_SOURCE[0]}")/dump-config.py" --lines "$dump")

    if [ "$found" = 1 ] && [ "${#PROD_BASE_URLS[@]}" -gt 0 ]; then
        [ "$cookie_found" = 1 ] && PROD_COOKIE_DOMAIN="$cookie"
        log "The dump is from ${PROD_BASE_URLS[*]} (media: ${PROD_MEDIA_URLS[*]:-from the base URL}; cookie domain: ${PROD_COOKIE_DOMAIN:-none})."
        if [ -n "${PROD_BASE_URL:-}" ] && ! printf '%s\n' "${PROD_BASE_URLS[@]}" | grep -qxF "$PROD_BASE_URL"; then
            log "Note: the saved PROD_BASE_URL was $PROD_BASE_URL — using the dump's own URLs instead."
        fi
        if [ "${#PROD_SCOPED_URLS[@]}" -gt 0 ]; then
            log "⚠ Some websites/store views have their own URLs — these keep their production values after the import:"
            for line in "${PROD_SCOPED_URLS[@]}"; do log "    $line"; done
            log "  Set them for this site afterwards, e.g.: $(cmd_hint magento config:set --scope=websites --scope-code=CODE web/secure/base_url https://${APP_DOMAIN}/)"
        fi
        PROD_URLS_FROM=dump
        return 0
    fi

    if [ -n "${PROD_BASE_URL:-}" ]; then
        log "⚠ Couldn't read the store URLs from the dump${error:+ ($error)} — using the saved ones ($PROD_BASE_URL)."
        PROD_BASE_URLS=("$PROD_BASE_URL")
        [ -n "${PROD_MEDIA_URL:-}" ] && PROD_MEDIA_URLS=("$PROD_MEDIA_URL")
        PROD_URLS_FROM=saved
        return 0
    fi

    log "Couldn't read the store URLs from the dump (${error:-no core_config_data in it}), so they're needed once."
    ask_prod_urls
    PROD_BASE_URLS=("$PROD_BASE_URL")
    PROD_MEDIA_URLS=("$PROD_MEDIA_URL")
    PROD_URLS_FROM=typed
}

# confirm_prod_urls — the check before the dump is rewritten: every
# production URL and the cookie domain, "from → to", plus the website/store
# URLs left alone. Asked as a yes/no (not ui_review: on the setup page the
# install has already run by now, so "Change answers" would start it over).
# No cancels with nothing replaced and the env file unchanged. With nobody
# to ask (no terminal, no page — the replace was confirmed in advance with
# PRESET_REPLACE_CHOICE=Y) the list is only logged. Saves PROD_* once it's
# confirmed, unless they came from the env file in the first place.
confirm_prod_urls() {
    local -a rows=()
    local url i
    case "${PROD_URLS_FROM:-dump}" in
        dump) rows+=("Read from" "the dump's store configuration") ;;
        saved) rows+=("Read from" "the saved PROD_* values in $ENV_NAME (the dump has none)") ;;
        typed) rows+=("Read from" "your answers (the dump has none)") ;;
    esac
    for url in "${PROD_BASE_URLS[@]}"; do rows+=("Store URL" "$url → $TARGET_BASE_URL"); done
    for url in "${PROD_MEDIA_URLS[@]}"; do rows+=("Media URL" "$url → $EFFECTIVE_MEDIA_URL"); done
    if [ -n "${PROD_COOKIE_DOMAIN:-}" ]; then
        rows+=("Cookie domain" "$PROD_COOKIE_DOMAIN → ${COOKIE_DOMAIN:-blank (uses each request host)}")
    else
        rows+=("Cookie domain" "none set — nothing to change")
    fi
    for url in "${PROD_SCOPED_URLS[@]}"; do rows+=("Not rewritten" "$url"); done

    log "The dump's URLs will be rewritten for this site:"
    for ((i = 0; i < ${#rows[@]}; i += 2)); do
        log "  ${rows[i]}: ${rows[i + 1]}"
    done
    if ui_interactive; then
        prompt_yes_no "Rewrite the dump's URLs like this?" Y "${rows[@]}" \
            || cancel "Refresh cancelled at the URL check — nothing was replaced, and $ENV_NAME is unchanged."
    else
        log "Nobody to ask — going ahead, as the replace was confirmed in advance."
    fi
    [ "${PROD_URLS_FROM:-dump}" = saved ] || save_prod_urls
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

# dump_prod_summary DUMP.sql.gz — one line for the setup review: the site
# the dump is from, its media URL and cookie domain ("" when the dump doesn't
# say). Only reads core_config_data, so it's quick even on a large dump.
dump_prod_summary() {
    local kind value base="" media="" cookie=""
    while IFS=$'\t' read -r kind value; do
        case "$kind" in
            base) [ -z "$base" ] || [[ "$value" == https://* && "$base" != https://* ]] && base="$value" ;;
            media) [ -z "$media" ] || [[ "$value" == https://* && "$media" != https://* ]] && media="$value" ;;
            cookie) cookie="$value" ;;
        esac
    done < <(python3 -I "$(dirname "${BASH_SOURCE[0]}")/dump-config.py" --lines "$1" 2>/dev/null)
    [ -n "$base" ] || return 0
    printf '%s (media %s; cookie domain %s)' "$base" "${media:-from the base URL}" "${cookie:-none}"
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
# Saved by confirm_prod_urls, not here, so cancelling there keeps the env
# file as it was.
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
}
