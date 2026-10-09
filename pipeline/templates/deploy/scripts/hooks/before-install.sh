#!/usr/bin/env bash
# @@HEADER@@
#
# BeforeInstall — runs from CodeDeploy's unpacked copy of the revision,
# before its files are copied to incoming/. Takes the deploy lock (held until
# validate.sh) and renders this release's settings, so a missing secret
# fails here, before anything on the box changes.

LOG_TAG=BeforeInstall
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RELEASE="$(cd "$HERE/../../.." && pwd)"
# shellcheck disable=SC1091
source "$HERE/../lib/common.sh"
# shellcheck disable=SC1091
source "$HERE/../lib/deploy-lock.sh"

log "Checking the box"
command -v docker >/dev/null || fail "docker isn't installed."
docker compose version >/dev/null 2>&1 || fail "the docker compose plugin isn't installed."
command -v aws >/dev/null || fail "the AWS CLI isn't installed."
command -v rsync >/dev/null || fail "rsync isn't installed."
id -nG | grep -qw docker || fail "$(id -un) isn't in the docker group."

mkdir -p "$MAGENTO_ROOT" "$DEPLOY_DIR" "$SHARED" "$APP_HOME/incoming"
deploy_lock_acquire

"$HERE/../render-env" "$RELEASE/deploy/targets/$ENV_NAME.env"
log "OK"
