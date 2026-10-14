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

wait_for_healthy 300 || { publish_status failed; fail "services aren't healthy."; }
log "✓ Services healthy"

write_release() {
    cat > "$SHARED/release.json" <<EOF
{"commit": "${COMMIT:-}", "imageTag": "${IMAGE_TAG:-}", "builtAt": "${BUILT_AT:-}", "deployedAt": "$(date -u +%FT%TZ)", "deploymentId": "${DEPLOYMENT_ID:-}", "state": "$1"}
EOF
    record_event "$2"
    publish_status "$1"
}

# The first release of a new environment, before any database: installed, in
# maintenance, waiting — scripts/deploy said so. Nothing more to check yet.
if [ -f "$SHARED/awaiting-database" ]; then
    write_release awaiting-database awaiting-database
    deploy_lock_release
    log "Release ${COMMIT:-?} is installed and waits for a database (box refresh s3://…)."
    exit 0
fi

box_magento --version
wait_for_https / 180 || { publish_status failed; fail "the store doesn't answer."; }
log "✓ https://${APP_DOMAIN}/ answers 200"
check_static_asset || { publish_status failed; fail "static assets aren't served correctly."; }

write_release ok live
deploy_lock_release
log "Release ${COMMIT:-?} is live"
