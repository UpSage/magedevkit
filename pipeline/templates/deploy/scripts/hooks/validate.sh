#!/usr/bin/env bash
# @@HEADER@@
#
# ValidateService — the deploy only counts once the site really serves. A
# failure here makes CodeDeploy roll back to the previous release. On
# success it records the release (shared/release.json, shared/deploys.jsonl)
# and releases the deploy lock.

LOG_TAG=ValidateService
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh"
# shellcheck disable=SC1091
source "$DEPLOY_DIR/scripts/lib/deploy-lock.sh"
# shellcheck disable=SC1091
source "$DEPLOY_DIR/scripts/lib/site-checks.sh"
load_box_env

wait_for_healthy 300 || fail "services aren't healthy."
log "✓ Services healthy"
box_magento --version
wait_for_https / 180 || fail "the store doesn't answer."
log "✓ https://${APP_DOMAIN}/ answers 200"
check_static_asset || fail "static assets aren't served correctly."

now="$(date -u +%FT%TZ)"
cat > "$SHARED/release.json" <<EOF
{"commit": "${COMMIT:-}", "imageTag": "${IMAGE_TAG:-}", "builtAt": "${BUILT_AT:-}", "deployedAt": "$now", "deploymentId": "${DEPLOYMENT_ID:-}"}
EOF
printf '{"commit": "%s", "imageTag": "%s", "deployedAt": "%s", "deploymentId": "%s", "result": "live"}\n' \
    "${COMMIT:-}" "${IMAGE_TAG:-}" "$now" "${DEPLOYMENT_ID:-}" >> "$SHARED/deploys.jsonl"

deploy_lock_release
log "Release ${COMMIT:-?} is live"
