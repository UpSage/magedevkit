# Setup wizard engine — sourced by bin/local/setup once it has set
# PLATFORM_DIR, the instance paths (bin/lib/paths.sh: ENV_FILE, MAGENTO_DIR,
# NGINX_CONF, CERTS_DIR, INSTANCE_DIR, …) and EXEC_USER. Never invoke this
# file directly.
#
# prompt_value() gracefully returns blank when there's no TTY instead of
# failing, and nearly every call site follows the
# `X_INPUT="$(prompt_value ...)"; X="${X_INPUT:-$CURRENT}"` pattern — so a
# non-interactive run silently keeps whatever value was already sourced
# from the env file.

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/stack.sh"
# clear_generated (09-install)
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/generated.sh"

# The steps live in bin/lib/setup/, one file per part, sourced in this order
# at the top level — they share variables (FRESH_INSTALL, DO_DB_REFRESH, …)
# exactly as one script would. Keep the order; later parts rely on earlier
# answers.
for SETUP_PART in \
    01-preflight \
    02-source \
    03-auth-and-data-plan \
    04-project \
    05-database \
    06-mode-and-stack \
    07-review \
    08-stack \
    09-install \
    10-storage \
    11-finish \
; do
    # shellcheck disable=SC1090
    source "$PLATFORM_DIR/bin/lib/setup/$SETUP_PART.sh"
done
unset SETUP_PART
