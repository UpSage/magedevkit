# Setup wizard, part 4 of 11 — Step 4: project name, domain, admin path, URLs; production facts for imported dumps.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Project Configuration
# ------------------------------------------------------------------

ui_step setup 4

# The compose project (other than "$2-platform") whose nginx already claims
# domain $1 on the edge proxy, if any — see docker-compose.local.yml's labels.
app_domain_owner() {
    docker ps -a --filter "label=platform.domain=$1" \
        --format '{{.Label "com.docker.compose.project"}}' \
        | grep -vxF "$2-platform" | head -1
    return 0
}

# Keyed on the generated nginx file's existence — once it's there, this
# one-time block has already run.
PROJECT_CONFIG_ALREADY_DONE=0
[ -f "$NGINX_CONF" ] && PROJECT_CONFIG_ALREADY_DONE=1

if [ "$PROJECT_CONFIG_ALREADY_DONE" = 0 ]; then
    while true; do
        if [ "$PLATFORM_MODE" = instance ]; then
            # The instance's name is its folder's (bin/local/new-instance wrote it).
            APP_NAME_CANDIDATE="$APP_NAME"
        else
            APP_NAME_INPUT="$(prompt_value "$(with_default "What should this project be called? (lowercase, no spaces — used for Docker names)" "$APP_NAME")" 0)"
            APP_NAME_CANDIDATE="${APP_NAME_INPUT:-$APP_NAME}"
        fi
        # valid_instance_name / project_owner: bin/lib/util.sh. Rejected early
        # with a clear message instead of a cryptic `docker compose up` error.
        if ! valid_instance_name "$APP_NAME_CANDIDATE"; then
            log "Invalid project name '$APP_NAME_CANDIDATE' — lowercase letters/digits/_- only (max 40), no spaces or dots, must start with a letter or digit."
        elif OTHER_DIR="$(project_owner "$APP_NAME_CANDIDATE" "$INSTANCE_DIR")" && [ -n "$OTHER_DIR" ]; then
            log "Project name '$APP_NAME_CANDIDATE' is already used by another checkout ($OTHER_DIR) — its containers, volumes and images would be shared. Pick another name."
        else
            APP_NAME="$APP_NAME_CANDIDATE"
            break
        fi
        [ "$PLATFORM_MODE" = instance ] \
            && fail "This instance's name ('$APP_NAME') can't be used (see above) — create the instance under another name: bin/local/new-instance OTHER-NAME."
        ui_interactive || fail "No TTY to ask for a different project name — set APP_NAME in $ENV_NAME and re-run."
    done
    set_env_var "$ENV_FILE" APP_NAME "$APP_NAME"

    if [ "$APP_DOMAIN_JUST_SET" = 1 ]; then
        log "✓ Local domain already set via Data Refresh planning ($APP_DOMAIN)"
    else
        while true; do
            APP_DOMAIN_INPUT="$(prompt_value "$(with_default "Which local domain should the site use?" "$APP_DOMAIN")" 0)"
            APP_DOMAIN_CANDIDATE="${APP_DOMAIN_INPUT:-$APP_DOMAIN}"
            if ! valid_hostname "$APP_DOMAIN_CANDIDATE"; then
                log "'$APP_DOMAIN_CANDIDATE' isn't a valid domain — use something like shop.test (letters, digits, hyphens and dots)."
                ui_interactive || fail "No TTY to ask for a different domain — set APP_DOMAIN in $ENV_NAME and re-run."
                continue
            fi
            OTHER_PROJECT="$(app_domain_owner "$APP_DOMAIN_CANDIDATE" "$APP_NAME")"
            if [ -z "$OTHER_PROJECT" ]; then
                APP_DOMAIN="$APP_DOMAIN_CANDIDATE"
                break
            fi
            log "Domain '$APP_DOMAIN_CANDIDATE' is already served by another stack ($OTHER_PROJECT) — the edge proxy can't route one hostname to two sites. Pick another domain."
            ui_interactive || fail "No TTY to ask for a different domain — set APP_DOMAIN in $ENV_NAME and re-run."
        done
        set_env_var "$ENV_FILE" APP_DOMAIN "$APP_DOMAIN"
    fi

    ADMIN_URI_INPUT="$(prompt_value "$(with_default "What URL path should the admin panel use? (e.g. admin → https://$APP_DOMAIN/admin)" "$MAGENTO_ADMIN_URI")" 0)"
    MAGENTO_ADMIN_URI="${ADMIN_URI_INPUT:-$MAGENTO_ADMIN_URI}"
    set_env_var "$ENV_FILE" MAGENTO_ADMIN_URI "$MAGENTO_ADMIN_URI"

    OPENSEARCH_INDEX_PREFIX_INPUT="$(prompt_value "$(with_default "What prefix should the search indexes use?" "$OPENSEARCH_INDEX_PREFIX")" 0)"
    OPENSEARCH_INDEX_PREFIX="${OPENSEARCH_INDEX_PREFIX_INPUT:-$OPENSEARCH_INDEX_PREFIX}"
    set_env_var "$ENV_FILE" OPENSEARCH_INDEX_PREFIX "$OPENSEARCH_INDEX_PREFIX"

    # Rarely needed locally, so hidden behind one question (pre-answered
    # yes when one of them is already set). Blank keeps each current value.
    URL_OVERRIDES_DEFAULT=N
    [ -n "${BASE_MEDIA_URL:-}${BASE_STATIC_URL:-}${COOKIE_DOMAIN:-}" ] && URL_OVERRIDES_DEFAULT=Y
    if prompt_yes_no "Do you need a custom media URL, static-files URL or cookie domain (e.g. a CDN)? Most local setups don't." "$URL_OVERRIDES_DEFAULT"; then
        BASE_MEDIA_URL_INPUT="$(prompt_value "Which URL should media files load from? (Enter = ${BASE_MEDIA_URL:-https://$APP_DOMAIN/media/})" 0)"
        BASE_MEDIA_URL="${BASE_MEDIA_URL_INPUT:-${BASE_MEDIA_URL:-}}"
        set_env_var "$ENV_FILE" BASE_MEDIA_URL "$BASE_MEDIA_URL"
        BASE_STATIC_URL_INPUT="$(prompt_value "Which URL should static files load from? (Enter = ${BASE_STATIC_URL:-https://$APP_DOMAIN/static/})" 0)"
        BASE_STATIC_URL="${BASE_STATIC_URL_INPUT:-${BASE_STATIC_URL:-}}"
        set_env_var "$ENV_FILE" BASE_STATIC_URL "$BASE_STATIC_URL"
        COOKIE_DOMAIN_INPUT="$(prompt_value "Which cookie domain should be used? (Enter = ${COOKIE_DOMAIN:-$APP_DOMAIN})" 0)"
        COOKIE_DOMAIN="${COOKIE_DOMAIN_INPUT:-${COOKIE_DOMAIN:-}}"
        set_env_var "$ENV_FILE" COOKIE_DOMAIN "$COOKIE_DOMAIN"
    fi
else
    log "✓ Project configuration already set (APP_NAME=$APP_NAME, APP_DOMAIN=$APP_DOMAIN)."
fi

# Production facts — needed so an imported production DB dump's URLs/cookie
# domain get sanitized (rewritten to this environment's own) instead of
# leaking production URLs. A separate one-time gate from Project
# Configuration above (keyed on the vars themselves being unset, not on
# config/nginx-local/default.conf existing) so this still reaches anyone
# who ran this wizard before this project added these.
if [ "$FRESH_SOURCE" = 1 ]; then
    log "✓ Production facts not needed for a fresh vanilla install (only used to sanitize imported dumps)."
elif [ -z "${PROD_BASE_URL:-}" ] || [ -z "${PROD_MEDIA_URL:-}" ] || [ -z "${PROD_COOKIE_DOMAIN:-}" ]; then
    log "Production facts (needed so an imported DB dump's URLs get sanitized correctly) — asked once."
    # Checked here, because a wrong value would make the DB refresh rewrite
    # the wrong URLs (bin/lib/db-refresh-engine.sh matches them literally).
    while true; do
        PROD_BASE_URL_INPUT="$(prompt_value "$(with_default "What's the live site's URL? (e.g. https://example.com/)" "${PROD_BASE_URL:-}")" 0)"
        PROD_BASE_URL_CANDIDATE="${PROD_BASE_URL_INPUT:-${PROD_BASE_URL:-}}"
        PROD_BASE_URL_NORMALIZED="$(normalize_base_url "$PROD_BASE_URL_CANDIDATE")" && break
        ui_interactive || fail "PROD_BASE_URL '$PROD_BASE_URL_CANDIDATE' isn't a URL like https://example.com/ — set it in $ENV_NAME."
        log "'$PROD_BASE_URL_CANDIDATE' isn't a URL — use the full address, e.g. https://example.com/"
    done
    PROD_BASE_URL="$PROD_BASE_URL_NORMALIZED"
    set_env_var "$ENV_FILE" PROD_BASE_URL "$PROD_BASE_URL"

    while true; do
        PROD_MEDIA_URL_INPUT="$(prompt_value "$(with_default "Where does the live site load media from? (its media/CDN URL)" "${PROD_MEDIA_URL:-}")" 0)"
        PROD_MEDIA_URL_CANDIDATE="${PROD_MEDIA_URL_INPUT:-${PROD_MEDIA_URL:-}}"
        PROD_MEDIA_URL_NORMALIZED="$(normalize_base_url "$PROD_MEDIA_URL_CANDIDATE")" && break
        ui_interactive || fail "PROD_MEDIA_URL '$PROD_MEDIA_URL_CANDIDATE' isn't a URL like https://example.com/media/ — set it in $ENV_NAME."
        log "'$PROD_MEDIA_URL_CANDIDATE' isn't a URL — e.g. https://example.com/media/ or https://cdn.example.com/media/"
    done
    PROD_MEDIA_URL="$PROD_MEDIA_URL_NORMALIZED"
    set_env_var "$ENV_FILE" PROD_MEDIA_URL "$PROD_MEDIA_URL"

    while true; do
        PROD_COOKIE_DOMAIN_INPUT="$(prompt_value "$(with_default "What cookie domain does the live site use? (e.g. .example.com)" "${PROD_COOKIE_DOMAIN:-}")" 0)"
        PROD_COOKIE_DOMAIN_CANDIDATE="${PROD_COOKIE_DOMAIN_INPUT:-${PROD_COOKIE_DOMAIN:-}}"
        valid_cookie_domain "$PROD_COOKIE_DOMAIN_CANDIDATE" && break
        ui_interactive || fail "PROD_COOKIE_DOMAIN '$PROD_COOKIE_DOMAIN_CANDIDATE' isn't a domain like .example.com — set it in $ENV_NAME."
        log "'$PROD_COOKIE_DOMAIN_CANDIDATE' isn't a domain — e.g. .example.com or www.example.com"
    done
    PROD_COOKIE_DOMAIN="$PROD_COOKIE_DOMAIN_CANDIDATE"
    set_env_var "$ENV_FILE" PROD_COOKIE_DOMAIN "$PROD_COOKIE_DOMAIN"
else
    log "✓ Production facts already set (PROD_BASE_URL=$PROD_BASE_URL)."
fi
