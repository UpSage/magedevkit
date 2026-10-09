#!/usr/bin/env bash
# @@HEADER@@
#
# AfterInstall — syncs the revision CodeDeploy copied to incoming/ onto the
# live paths: the code into magento/, deploy/ into deploy/, the release's
# image tag into shared/release.env, and the rendered env.php into place.

LOG_TAG=AfterInstall
# shellcheck disable=SC1091
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../lib/common.sh"

INCOMING="$APP_HOME/incoming"
test -d "$INCOMING/deploy" || fail "$INCOMING has no deploy/ — not a release built by deploy/buildspec.yml."
test -f "$INCOMING/release.env" || fail "$INCOMING/release.env missing — not a release built by deploy/buildspec.yml."

# The site keeps serving during the sync, so no --delete: rsync --delete
# isn't atomic, and a request in that window hits a file that's already gone
# (it crashed real pages before). Every pattern is anchored with "/" so a
# vendor package's own generated/ or var/ isn't caught by it.
log "Syncing the code into $MAGENTO_ROOT"
rsync -a \
    --exclude='/.git/' \
    --exclude='/deploy/' \
    --exclude='/appspec.yml' \
    --exclude='/release.env' \
    --exclude='/app/etc/env.php*' \
    --exclude='/app/etc/platform/' \
    --exclude='/auth.json' \
    --exclude='/pub/media/' \
    --exclude='/var/' \
    "$INCOMING/" "$MAGENTO_ROOT/"

# Code is different: without --delete a module removed from the repo would
# stay registered. app/code and app/design are small, so an exact mirror is
# near-instant.
for d in app/code app/design; do
    [ -d "$INCOMING/$d" ] && rsync -a --delete "$INCOMING/$d/" "$MAGENTO_ROOT/$d/"
done

log "Syncing deploy/ (compose, config, scripts)"
rsync -a --delete "$INCOMING/deploy/" "$DEPLOY_DIR/"
chmod +x "$DEPLOY_DIR"/scripts/render-env "$DEPLOY_DIR"/scripts/deploy "$DEPLOY_DIR"/scripts/box "$DEPLOY_DIR"/scripts/hooks/*.sh

install -m 600 "$INCOMING/release.env" "$SHARED/release.env"
install -m 600 "$SHARED/env.php" "$MAGENTO_ROOT/app/etc/env.php"

mkdir -p "$MAGENTO_ROOT/var" "$MAGENTO_ROOT/generated" "$MAGENTO_ROOT/pub/static" "$MAGENTO_ROOT/pub/media"
chmod u+x "$MAGENTO_ROOT/bin/magento"

[ -n "$(ls -A "$MAGENTO_ROOT/pub/static/frontend" 2>/dev/null)" ] \
    || fail "pub/static/frontend is empty — the release wasn't built by deploy/scripts/build-release.sh (static content deploy)."
[ -n "$(ls -A "$MAGENTO_ROOT/generated/metadata" 2>/dev/null)" ] \
    || fail "generated/metadata is empty — the release wasn't built by deploy/scripts/build-release.sh (di:compile)."

log "OK"
