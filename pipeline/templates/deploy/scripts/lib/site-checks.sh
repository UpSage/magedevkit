# @@HEADER@@
#
# Health checks used by the ValidateService hook and deploy/scripts/box
# status. Expects lib/common.sh (box_compose, MAGENTO_ROOT) and APP_DOMAIN.

# Every service with a Docker healthcheck reports healthy. $1 = seconds.
wait_for_healthy() {
    local timeout="${1:-180}" deadline unhealthy
    deadline=$((SECONDS + timeout))
    while true; do
        unhealthy="$(box_compose ps --format '{{.Health}}' | grep -vE '^(healthy|)$' || true)"
        [ -z "$unhealthy" ] && return 0
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "[checks] ERROR: services still unhealthy after ${timeout}s:" >&2
            box_compose ps >&2
            return 1
        fi
        sleep 5
    done
}

# GET https://$APP_DOMAIN$1 through this box's own Traefik until it answers
# 200. $2 = seconds. --insecure: right after the first deploy Traefik may
# still be fetching the Let's Encrypt certificate — this checks the app, not
# the certificate. A short retry window here rolled back real, successful
# deploys in the past, so poll with a generous deadline.
wait_for_https() {
    local path="${1:-/}" timeout="${2:-120}" deadline code
    deadline=$((SECONDS + timeout))
    while true; do
        code="$(curl --silent --insecure --output /dev/null --write-out '%{http_code}' --max-time 20 \
            --resolve "${APP_DOMAIN}:443:127.0.0.1" "https://${APP_DOMAIN}${path}" || true)"
        [ "$code" = 200 ] && return 0
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "[checks] ERROR: https://${APP_DOMAIN}${path} still answers ${code:-no response} after ${timeout}s." >&2
            return 1
        fi
        sleep 5
    done
}

# One real JavaScript file from the deployed pub/static is served whole, as
# JavaScript. Catches the "site loads, every script is broken" class of
# failure (Varnish ESI-scanning JS once corrupted every JS file).
check_static_asset() {
    local file rel ctype code
    file="$(find "$MAGENTO_ROOT/pub/static/frontend" -type f \( -name 'mixins.js' -o -name 'mixins.min.js' \) -path '*/mage/requirejs/*' 2>/dev/null | head -1)"
    if [ -z "$file" ]; then
        echo "[checks] no deployed static content yet — skipping the static asset check"
        return 0
    fi
    rel="${file#"$MAGENTO_ROOT/pub/static/"}"
    read -r code ctype < <(curl --silent --insecure --output /dev/null --write-out '%{http_code} %{content_type}' --max-time 20 \
        --resolve "${APP_DOMAIN}:443:127.0.0.1" "https://${APP_DOMAIN}/static/${rel}" || true)
    if [ "$code" != 200 ] || [[ "$ctype" != *javascript* ]]; then
        echo "[checks] ERROR: /static/${rel} answered ${code:-no response} (${ctype:-no content type}), expected 200 JavaScript." >&2
        return 1
    fi
    echo "[checks] static asset OK: /static/${rel} ($ctype)"
}
