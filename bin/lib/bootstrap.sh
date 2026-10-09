# The common start of every bin/local/* entry point: sourced right after the
# script's own `set` line —
#
#   # shellcheck disable=SC1091
#   source "$(dirname "${BASH_SOURCE[0]}")/../lib/bootstrap.sh"
#
# Sets PLATFORM_DIR (this platform repo) and MAGENTO (its bin/magento
# wrapper) and loads the shared helpers:
#   ui.sh    log, fail, prompts, step/task progress, wizard_lock, run log
#   env.sh   env_get, set_env_var, sed/hostname helpers
#   util.sh  version_lt/version_ge, file_sha256, composer_check
#   paths.sh ENV_FILE, MAGENTO_DIR, LOGS_DIR, … for this instance (or a
#            legacy checkout), require_env_file, cmd_hint
#
# What comes next stays in each script, because the order differs: setup
# runs before there is an env file, rebuild and build parse options first,
# and only some need identity.sh or the lock.

PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MAGENTO="$PLATFORM_DIR/bin/magento"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/ui.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/env.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/util.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/paths.sh"
