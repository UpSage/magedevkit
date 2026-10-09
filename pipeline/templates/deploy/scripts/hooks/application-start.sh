#!/usr/bin/env bash
# @@HEADER@@
#
# ApplicationStart — runs the synced release's deploy/scripts/deploy.

set -euo pipefail
# shellcheck disable=SC1091
source /etc/m2deploy.env
exec "${APP_HOME:-/opt/$APP}/deploy/scripts/deploy"
