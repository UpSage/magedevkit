# Prompt + progress helpers shared by every interactive script (bin/local/setup,
# bin/local/sync-db, bin/local/refresh, and the engines they source) — the one
# place a question or a progress line is produced. Sourced, never run.
#
# Two front ends, one code path:
#   terminal  (default) — the original read-based prompts, unchanged.
#   web       (SETUP_UI=web, set by bin/lib/web-wizard/server.py) — each
#             question is sent as one JSON line to $SETUP_UI_DIR/events and
#             its answer read back from $SETUP_UI_DIR/answer, so the browser
#             asks exactly the same questions, in the same order, with the
#             same defaults and validation.
#
# Expects nothing but bash. set_env_var, env_get & co. live in bin/lib/env.sh
# (bin/lib/bootstrap.sh loads both).

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1"
}

# Errors go to stderr: fail is often reached inside $(…) (prompts, ask),
# where a stdout message would be captured into a variable and lost.
fail() {
    log "Error: $1" >&2
    [ -n "${UI_TASK_LABEL:-}" ] && ui_task_fail
    ui_emit "{\"t\":\"error\",\"message\":$(ui_json "$1")}"
    exit 1
}

# ------------------------------------------------------------------
# Web bridge plumbing
# ------------------------------------------------------------------

# script_help FILE — prints FILE's header comment as its --help text: the
# first comment block after the shebang (skipping shellcheck and bootstrap
# lines). Never a fixed line range, so editing the header can't cut it short.
script_help() {
    awk 'NR == 1 { next }
         /^# shellcheck/ || /^# PLATFORM_DIR, MAGENTO and the shared helpers/ { next }
         /^#/ { started = 1; sub(/^# ?/, ""); print; next }
         started { exit }' "$1"
}

ui_web() {
    [ "${SETUP_UI:-}" = web ] && [ -n "${SETUP_UI_DIR:-}" ]
}

# Whether a question can be asked at all — a TTY, or the web page.
ui_interactive() {
    [ -t 0 ] || ui_web
}

# $1 -> JSON string literal (quotes included).
ui_json() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    s="${s//$'\n'/\\n}"
    s="${s//$'\r'/\\r}"
    s="${s//$'\t'/\\t}"
    s="${s//[$'\x01'-$'\x1f']/}"
    printf '"%s"' "$s"
}

# Args -> JSON array of strings.
ui_json_array() {
    local out='[' sep='' a
    for a in "$@"; do
        out+="$sep$(ui_json "$a")"
        sep=','
    done
    printf '%s]' "$out"
}

# One event line to the server (no-op in the terminal). Each write is a
# single short line, so concurrent writers never interleave (PIPE_BUF).
ui_emit() {
    ui_web || return 0
    printf '%s\n' "$1" > "$SETUP_UI_DIR/events"
}

# cancel MESSAGE — the user said no to a question the run can't go on
# without. Logs MESSAGE and stops with 130, which the wizard page shows as
# "Cancelled" (not as a failure) and the terminal treats like Ctrl+C.
UI_CANCEL_EXIT=130
cancel() {
    log "$1"
    exit "$UI_CANCEL_EXIT"
}

# Cancel requested from the page ("Stop after this task") — honored at the
# next safe point: a finished task, a new step, or a new question.
ui_check_cancel() {
    ui_web || return 0
    if [ -f "$SETUP_UI_DIR/cancel" ]; then
        cancel "Cancelled from the wizard page — stopping here. Re-running continues from what's already done."
    fi
}

# ui_ask TYPE LABEL [DEFAULT] [OPTIONS...] -> prints the answer.
#   TYPE: text | password | menu | yesno | file | review | confirm_typed
# For menu, DEFAULT is the default option number and OPTIONS the labels.
ui_ask() {
    local type="$1" label="$2" default="${3:-}" answer
    shift 3 2>/dev/null || shift $#
    ui_check_cancel
    ui_emit "{\"t\":\"ask\",\"type\":$(ui_json "$type"),\"label\":$(ui_json "$label"),\"default\":$(ui_json "$default"),\"options\":$(ui_json_array "$@")}"
    IFS= read -r answer < "$SETUP_UI_DIR/answer" || answer=''
    printf '%s' "$answer"
}

# ------------------------------------------------------------------
# Prompt helpers (moved here from bin/local/setup, bin/lib/setup-engine.sh
# and bin/local/sync-db — terminal code unchanged, plus the web branch)
# ------------------------------------------------------------------

# $1=label $2=hidden(0/1) -> prints the entered value to stdout, or blank
# when there's no TTY (so a non-interactive invocation never blocks — nearly
# every call site falls back to its own current value via
# `${X_INPUT:-$CURRENT}`).
prompt_value() {
    local label="$1" hidden="$2" value sep=': '
    if ui_web; then
        if [ "$hidden" = 1 ]; then ui_ask password "$label" ''; else ui_ask text "$label" ''; fi
        return
    fi
    if [ ! -t 0 ]; then
        printf ''
        return
    fi
    # A bare question reads better as "Which branch? " than "Which branch?: ".
    [[ "$label" == *'?' ]] && sep=' '
    if [ "$hidden" = 1 ]; then
        read -rsp "$label$sep" value >&2
        echo >&2
    else
        read -rp "$label$sep" value >&2
    fi
    printf '%s' "$value"
}

# sync-db's prompt: $1=label $2=default -> prints the chosen value (default
# if not a TTY, so a non-interactive invocation never hangs on a prompt).
prompt() {
    local label="$1" default="$2" value display_default="$2"
    if ui_web; then
        case "$default" in
            Y|N)
                value="$(ui_ask yesno "$label" "$default")"
                case "${value,,}" in y|yes) printf 'Y' ;; *) printf 'N' ;; esac
                ;;
            *)
                value="$(ui_ask text "$label" "$default")"
                printf '%s' "${value:-$default}"
                ;;
        esac
        return
    fi
    # Bare Y/N defaults render as the same [Y/n]/[y/N] toggle notation
    # bin/local/setup's prompts already use, instead of a bare [Y]/[N] —
    # free-text defaults (URLs, paths) are untouched.
    case "$default" in
        Y) display_default="Y/n" ;;
        N) display_default="y/N" ;;
    esac
    if [ ! -t 0 ]; then
        printf '%s' "$default"
        return
    fi
    if [ -n "$default" ]; then
        read -rp "$label [$display_default]: " value
    else
        read -rp "$label: " value
    fi
    printf '%s' "${value:-$default}"
}

# "$1 [$2]", or just "$1" when there's no default — never an empty "[]".
with_default() {
    if [ -n "$2" ]; then printf '%s [%s]' "$1" "$2"; else printf '%s' "$1"; fi
}

# Numbered menu. $1=question $2=default number, then one argument per option
# (numbered from 1). Prints the chosen number. The default option is tagged
# "(default)" unless its label already carries a tag ending in "default)",
# e.g. "(latest, default)". Enter, or no TTY, picks the default; anything
# else is asked again. Menu text goes to stderr so $(...) gets just the number.
# prompt_menu QUESTION DEFAULT OPTION… — prints the chosen number (1-based).
# DEFAULT 0 = nothing preselected: the person has to pick one (without a
# TTY it prints 0, so the caller decides what "no answer" means).
prompt_menu() {
    local question="$1" default="$2" choice i label
    shift 2
    if ui_web; then
        while true; do
            choice="$(ui_ask menu "$question" "$default" "$@")"
            choice="${choice:-$default}"
            if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $# ]; then
                printf '%s' "$choice"
                return
            fi
        done
    fi
    if [ ! -t 0 ]; then
        printf '%s' "$default"
        return
    fi
    printf '\n%s\n' "$question" >&2
    for ((i = 1; i <= $#; i++)); do
        label="${!i}"
        [ "$i" = "$default" ] && [[ "$label" != *"default)" ]] && label+=" (default)"
        printf '  %s) %s\n' "$i" "$label" >&2
    done
    while true; do
        read -rp "> " choice
        choice="${choice//[[:space:]]/}"
        choice="${choice:-$default}"
        if [[ "$choice" =~ ^[0-9]+$ ]] && [ "$choice" -ge 1 ] && [ "$choice" -le $# ]; then
            printf '%s' "$choice"
            return
        fi
        echo "Please enter a number from 1 to $#." >&2
    done
}

# Yes/no question. $1=question $2=default (Y or N). Returns 0 for yes, 1 for
# no; Enter, or no TTY, gives the default; anything else is asked again.
# prompt_yes_no QUESTION DEFAULT [LABEL VALUE …] — the optional pairs are
# details shown with the question on the web page (a small table, like the
# review step); terminal callers print their own details first.
prompt_yes_no() {
    local question="$1" default="$2" hint='[y/N]' answer
    if ui_web; then
        answer="$(ui_ask yesno "$question" "$default" "${@:3}")"
        case "${answer:-$default}" in y|Y|yes|Yes) return 0 ;; *) return 1 ;; esac
    fi
    [ "$default" = Y ] && hint='[Y/n]'
    answer="$default"
    while true; do
        if [ -t 0 ]; then
            read -rp "$question $hint: " answer
            answer="${answer//[[:space:]]/}"
            answer="${answer:-$default}"
        fi
        case "${answer,,}" in
            y|yes) return 0 ;;
            n|no) return 1 ;;
        esac
        echo "Please answer y or n." >&2
    done
}

# DB credential prompts. Character sets are restricted so a value can never
# break set_env_var's sed (`|` delimiter), the single-quoted SQL in
# CREATE USER/IDENTIFIED BY, or the unquoted `source` of the env file.
# Messages go to stderr — these are called inside $(...). Blank keeps the
# current value; with no TTY an invalid current value fails instead of
# looping forever.
DB_IDENT_RE='^[A-Za-z0-9_]+$'
DB_SECRET_RE='^[A-Za-z0-9._@%+=-]+$'

prompt_db_ident() {  # $1=question $2=current $3=name (for messages) -> prints the chosen value
    local question="$1" current="$2" name="$3" value
    while true; do
        value="$(prompt_value "$(with_default "$question" "$current")" 0)"
        value="${value:-$current}"
        [[ "$value" =~ $DB_IDENT_RE ]] && { printf '%s' "$value"; return; }
        log "'$value' isn't a valid $name — use letters, digits and _ only." >&2
        ui_interactive || exit 1
    done
}

# The current value is shown only while it's still the example default
# (magento), so a real password is never echoed.
prompt_db_secret() {  # $1=question $2=current $3=name (for messages) -> prints the chosen value
    local question="$1 (typing is hidden)" current="$2" name="$3" value confirm
    case "$current" in
        '') ;;
        magento) question+=" [magento]" ;;
        *) question+=" [Enter = keep your current one]" ;;
    esac
    while true; do
        value="$(prompt_value "$question" 1)"
        if [ -z "$value" ]; then
            value="$current"
        else
            confirm="$(prompt_value "Please type it again to confirm" 1)"
            [ "$value" = "$confirm" ] || { log "Those didn't match — let's try again." >&2; continue; }
        fi
        [[ "$value" =~ $DB_SECRET_RE ]] && { printf '%s' "$value"; return; }
        log "That $name won't work — use only letters, digits and . _ @ % + = -" >&2
        ui_interactive || exit 1
    done
}

# $1=label $2=current $3=regex $4=hint -> prints the chosen value (blank keeps current).
prompt_matching() {
    local value
    while true; do
        value="$(prompt_value "$(with_default "$1" "$2")" 0)"
        value="${value:-$2}"
        [[ "$value" =~ $3 ]] && { printf '%s' "$value"; return; }
        log "'$value' won't work — $4" >&2
        ui_interactive || exit 1
    done
}

# Magento's own admin password rule: 7+ characters, letters and digits.
# Hidden, confirmed, never written to disk. $1=default (kept on blank) —
# only ever the built-in placeholder, so it's safe to show.
prompt_admin_password() {
    local value confirm
    while true; do
        value="$(prompt_value "$(with_default "What should the admin password be? (7+ characters with letters and digits; typing is hidden)" "$1")" 1)"
        if [ -z "$value" ]; then
            value="$1"
        else
            confirm="$(prompt_value "Please type it again to confirm" 1)"
            [ "$value" = "$confirm" ] || { log "Those didn't match — let's try again." >&2; continue; }
        fi
        if [ "${#value}" -ge 7 ] && [[ "$value" =~ [A-Za-z] ]] && [[ "$value" =~ [0-9] ]]; then
            printf '%s' "$value"; return
        fi
        log "That admin password won't work — it needs at least 7 characters, including letters and digits." >&2
        ui_interactive || exit 1
    done
}

# Database dump source (file path or URL). The web page offers an upload, a
# path on this computer, or a URL; the terminal asks for a path or URL.
# $1=label. Prints the (normalized) answer, or blank.
prompt_db_source() {
    prompt_file_source "$1" sqlgz
}

# prompt_file_source LABEL KIND — a file the user supplies: on the web page
# an upload (checked as KIND: sqlgz = gzipped SQL dump, json = e.g.
# auth.json), a path on this computer or a URL; in the terminal a path or
# URL. Prints the path/URL (an upload's path is under db/db-refresh/uploads).
prompt_file_source() {
    local value
    if ui_web; then
        value="$(ui_ask file "$1" '' "kind=${2:-sqlgz}")"
    else
        value="$(prompt_value "$1" 0)"
    fi
    ui_normalize_path "$value"
}

# A Windows path (C:\Users\...) -> its WSL path, when wslpath is available;
# anything else is printed unchanged.
ui_normalize_path() {
    local p="$1"
    # Surrounding blanks and quotes, e.g. from Windows Explorer's "Copy as path"
    # ("C:\Users\me\auth.json") or a quoted shell paste.
    p="${p#"${p%%[![:space:]]*}"}"
    p="${p%"${p##*[![:space:]]}"}"
    if [[ "$p" =~ ^\"(.*)\"$ ]] || [[ "$p" =~ ^\'(.*)\'$ ]]; then
        p="${BASH_REMATCH[1]}"
    fi
    if [[ "$p" =~ ^[A-Za-z]:[\\/] ]] && command -v wslpath >/dev/null 2>&1; then
        wslpath -u "$p" 2>/dev/null || printf '%s' "$p"
    else
        printf '%s' "$p"
    fi
}

# Destructive confirmation: on the web, the user types $2 to enable the
# button; returns 0 when confirmed. Terminal callers keep their own Y/n.
ui_confirm_destructive() {
    local answer
    answer="$(ui_ask confirm_typed "$1" "$2")"
    [ "$answer" = restart ] && exit "$UI_CHANGE_ANSWERS_EXIT"
    [ "$answer" = "$2" ]
}

# ------------------------------------------------------------------
# Progress: steps, tasks, review, results
# ------------------------------------------------------------------

# Step lists per flow — the single source for the terminal headings and the
# page's step rail.
UI_STEPS_setup=(
    "Welcome & checks"
    "Magento source"
    "Store data"
    "Project"
    "Database login"
    "Mode & versions"
    "Review & start"
    "Containers"
    "Dependencies"
    "Magento install"
    "Media & data"
    "Finish"
)
UI_STEPS_refresh=(
    "Stack check"
    "Choose the dump"
    "About this dump"
    "Options"
    "Confirm"
    "Refreshing"
    "Done"
)
UI_STEPS_ownership=(
    "Unlock"
    "Apply ownership"
    "Restart PHP"
    "Done"
)
UI_STEPS_version=(
    "Stack check"
    "Current version"
    "Choose action"
    "Choose target"
    "Review & confirm"
    "Snapshot"
    "Switching"
    "Done"
)
UI_STEPS_rebuild=(
    "Stack check"
    "Composer"
    "Rebuild"
    "Done"
)
UI_STEPS_account=(
    "Sign-in method"
    "Details"
    "Check"
    "Done"
)
UI_STEPS_provision=(
    "Target"
    "Account"
    "Checks"
    "Secrets"
    "Review"
    "Apply"
    "First release"
    "Database"
    "Done"
)
UI_STEPS_unprovision=(
    "Target"
    "Account"
    "Confirm"
    "Remove"
    "Done"
)
UI_STEPS_pipeline=(
    "Store check"
    "Target"
    "Review"
    "Write the kit"
    "Validate"
    "Commit"
    "Done"
)

# Declares which flow owns the step rail for this whole run. The first
# top-level script sets it; nested scripts (bin/refresh run by setup) inherit
# it, so their own ui_step calls don't hijack the parent's rail.
ui_flow_begin() {
    local flow="$1" arr
    [ -n "${UI_FLOW:-}" ] && return 0
    export UI_FLOW="$flow"
    arr="UI_STEPS_${flow}[@]"
    ui_emit "{\"t\":\"flow\",\"flow\":$(ui_json "$flow"),\"steps\":$(ui_json_array "${!arr}")}"
}

# ui_step FLOW N — announce step N of FLOW (ignored when another flow owns
# the run).
ui_step() {
    local flow="$1" n="$2" arr total title
    [ "${UI_FLOW:-}" = "$flow" ] || return 0
    ui_check_cancel
    arr="UI_STEPS_${flow}[@]"
    local -a steps=("${!arr}")
    total="${#steps[@]}"
    title="${steps[$((n - 1))]}"
    if ui_web; then
        ui_emit "{\"t\":\"step\",\"n\":$n}"
    else
        printf '\n── Step %s/%s · %s ──\n' "$n" "$total" "$title"
    fi
}

ui_step_skip() {
    local flow="$1" n="$2" arr
    [ "${UI_FLOW:-}" = "$flow" ] || return 0
    arr="UI_STEPS_${flow}[@]"
    local -a steps=("${!arr}")
    if ui_web; then
        ui_emit "{\"t\":\"step_skip\",\"n\":$n}"
    else
        printf '\n── Step %s/%s · %s — skipped ──\n' "$n" "${#steps[@]}" "${steps[$((n - 1))]}"
    fi
}

ui_task_start() {
    UI_TASK_LABEL="$1"
    UI_TASK_T0="$SECONDS"
    if ui_web; then
        ui_emit "{\"t\":\"task_start\",\"label\":$(ui_json "$1")}"
    else
        printf '→ %s …\n' "$1"
    fi
}

ui_task_done() {
    local secs=$((SECONDS - ${UI_TASK_T0:-$SECONDS})) label="${UI_TASK_LABEL:-}"
    UI_TASK_LABEL=''
    if ui_web; then
        ui_emit "{\"t\":\"task_done\",\"label\":$(ui_json "$label"),\"secs\":$secs}"
    else
        printf '✓ %s (%s)\n' "$label" "$(ui_duration "$secs")"
    fi
    ui_check_cancel
}

ui_task_fail() {
    local label="${UI_TASK_LABEL:-}"
    UI_TASK_LABEL=''
    if ui_web; then
        ui_emit "{\"t\":\"task_fail\",\"label\":$(ui_json "$label")}"
    else
        printf '✗ %s\n' "$label"
    fi
}

# ui_task LABEL CMD [ARGS...] — run one command as a task. Returns its status.
# The command runs outside any `if`/`&&`: bash ignores `set -e` there, even in
# a `( set -e … )` subshell, so a failing step inside perm_* would otherwise
# be reported as done.
ui_task() {
    local label="$1" rc had_e=
    shift
    ui_task_start "$label"
    case $- in *e*) had_e=1; set +e ;; esac
    "$@"
    rc=$?
    [ -n "$had_e" ] && set -e
    if [ "$rc" -eq 0 ]; then
        ui_task_done
    else
        ui_task_fail
        return "$rc"
    fi
}

ui_duration() {
    local s="$1"
    if [ "$s" -ge 60 ]; then printf '%dm %02ds' $((s / 60)) $((s % 60)); else printf '%ds' "$s"; fi
}

# ui_review TITLE KEY VALUE [KEY VALUE ...] — the "check your answers"
# checkpoint before heavy work. Returns 0 to go ahead, 1 to stop.
# "Change answers" on the page (web only): the script exits with this code
# and the wizard server starts the same flow again on the same page, with the
# answers given so far coming back as the defaults. Only offered at the
# review / typed-confirmation checkpoints, before anything heavy has run.
UI_CHANGE_ANSWERS_EXIT=75

ui_review() {
    local title="$1" answer i
    shift
    if ui_web; then
        answer="$(ui_ask review "$title" '' "$@")"
        [ "$answer" = restart ] && exit "$UI_CHANGE_ANSWERS_EXIT"
        [ "$answer" = start ]
        return
    fi
    printf '\n%s\n' "$title"
    for ((i = 1; i < $#; i += 2)); do
        local k="${!i}" j=$((i + 1))
        printf '  %-22s %s\n' "$k" "${!j}"
    done
    prompt_yes_no 'Start with these settings?' Y
}

# ui_result KIND LABEL VALUE — one item for the page's finish screen
# (no-op in the terminal, which prints its own summary).
#   KIND: link | text | copy | domain | hosts | command | path
ui_result() {
    ui_emit "{\"t\":\"result\",\"kind\":$(ui_json "$1"),\"label\":$(ui_json "$2"),\"value\":$(ui_json "$3")}"
}

# ------------------------------------------------------------------
# Terminal-handover replacements
# ------------------------------------------------------------------

# `aws configure --profile $1` — on the web, the same four answers are asked
# on the page and written with `aws configure set`.
# prompt_s3_bucket PROFILE [CURRENT] — the S3 bucket for media, pulled from
# what PROFILE can list: a menu of those buckets (nothing preselected —
# CURRENT is only marked), plus "another bucket" to type a name; falls back
# to typing when the profile may not list buckets (s3:ListAllMyBuckets).
# Prints the bucket name; blank if none was given. Without a TTY: CURRENT.
prompt_s3_bucket() {
    local profile="$1" current="${2:-}" buckets=() labels=() b choice i
    mapfile -t buckets < <(aws s3api list-buckets --profile "$profile" --query 'Buckets[].Name' --output text 2>/dev/null         | tr '\t' '\n' | sed '/^[[:space:]]*$/d;/^None$/d' | sort)
    if [ "${#buckets[@]}" -gt 0 ]; then
        for i in "${!buckets[@]}"; do
            b="${buckets[$i]}"
            if [ "$b" = "$current" ]; then
                labels+=("$b (current)")
            else
                labels+=("$b")
            fi
        done
        choice="$(prompt_menu "Which S3 bucket should media be stored in? (the buckets profile '$profile' can see)" 0 \
            "${labels[@]}" "Another bucket — type its name")"
        if [ "$choice" = 0 ]; then          # nobody to ask
            printf '%s' "$current"
            return
        fi
        if [ "$choice" -le "${#buckets[@]}" ]; then
            printf '%s' "${buckets[$((choice - 1))]}"
            return
        fi
    else
        log "Couldn't list S3 buckets with profile '$profile' (it may not be allowed s3:ListAllMyBuckets) — type the bucket name instead." >&2
    fi
    prompt 'S3 bucket name' "$current"
}

# prompt_aws_profile [CURRENT] — the AWS CLI profile to use for S3: a menu of
# the profiles on this computer (`aws configure list-profiles`; nothing
# preselected — CURRENT is only marked), plus "set up a new profile", which
# asks for its name (its keys are asked right after, by the caller's
# aws_configure_profile). Prints the profile name. Without a TTY: CURRENT.
prompt_aws_profile() {
    local current="${1:-}" profiles=() labels=() p choice name
    mapfile -t profiles < <(aws configure list-profiles 2>/dev/null | sed '/^[[:space:]]*$/d' | sort -u)
    if [ "${#profiles[@]}" -gt 0 ]; then
        for p in "${profiles[@]}"; do
            if [ "$p" = "$current" ]; then labels+=("$p (current)"); else labels+=("$p"); fi
        done
        choice="$(prompt_menu "Which AWS CLI profile should be used for S3? (the profiles on this computer)" 0 \
            "${labels[@]}" "Set up a new profile — enter its name and access keys")"
        if [ "$choice" = 0 ]; then
            printf '%s' "$current"
            return
        fi
        if [ "$choice" -le "${#profiles[@]}" ]; then
            printf '%s' "${profiles[$((choice - 1))]}"
            return
        fi
    fi
    while true; do
        name="$(prompt_value 'Name for the new AWS CLI profile (letters, digits, . _ -)' 0)"
        if [[ "$name" =~ ^[A-Za-z0-9._-]+$ ]]; then
            printf '%s' "$name"
            return
        fi
        ui_interactive || { printf '%s' "$current"; return; }
        log "That isn't a valid profile name." >&2
    done
}

# s3_bucket_region BUCKET PROFILE — the bucket's own region ("" if unknown).
s3_bucket_region() {
    local r
    r="$(aws s3api get-bucket-location --bucket "$1" --profile "$2" --query LocationConstraint --output text 2>/dev/null)" || return 0
    case "$r" in
        None|null|"") printf 'us-east-1' ;;   # S3 reports us-east-1 as an empty constraint
        EU) printf 'eu-west-1' ;;
        *) printf '%s' "$r" ;;
    esac
}

aws_configure_profile() {
    local profile="$1" key secret region output
    if ! ui_web; then
        aws configure --profile "$profile"
        return
    fi
    key="$(ui_ask text "AWS access key ID for profile '$profile'" "$(aws configure get aws_access_key_id --profile "$profile" 2>/dev/null || true)")"
    secret="$(ui_ask password "AWS secret access key for profile '$profile' (typing is hidden) [Enter = keep the current one]" '')"
    region="$(ui_ask text "Default AWS region (e.g. us-east-1)" "$(aws configure get region --profile "$profile" 2>/dev/null || true)")"
    output="$(ui_ask text "Default output format" "$(aws configure get output --profile "$profile" 2>/dev/null || echo json)")"
    # The keys go in with `aws configure import` from a private temp file:
    # `aws configure set aws_secret_access_key …` would put the secret on a
    # command line, where the process list shows it.
    [ -n "$secret" ] || secret="$(aws configure get aws_secret_access_key --profile "$profile" 2>/dev/null || true)"
    if [ -n "$key" ] && [ -n "$secret" ]; then
        [[ "$profile" =~ ^[A-Za-z0-9._-]+$ ]] || fail "'$profile' isn't a profile name (letters, digits, . _ -)."
        local csv
        csv="$(mktemp)"
        chmod 600 "$csv"
        printf 'User Name,Access key ID,Secret access key\n%s,%s,%s\n' "$profile" "$key" "$secret" > "$csv"
        aws configure import --csv "file://$csv" >/dev/null || { rm -f "$csv"; fail "The AWS CLI couldn't save the keys for profile '$profile'."; }
        rm -f "$csv"
    fi
    unset secret
    [ -n "$region" ] && aws configure set region "$region" --profile "$profile"
    [ -n "$output" ] && aws configure set output "$output" --profile "$profile"
    return 0
}

# sudo asks for its password on the page (via bin/lib/web-wizard/askpass)
# instead of a terminal that isn't there.
sudo() {
    if ui_web; then
        SUDO_ASKPASS="${UI_PLATFORM_DIR:-$PLATFORM_DIR}/bin/lib/web-wizard/askpass" command sudo -A "$@"
    else
        command sudo "$@"
    fi
}

# ------------------------------------------------------------------
# Run log
# ------------------------------------------------------------------

# Copies this run's output to platform/logs/<name>-<timestamp>.log (terminal
# runs; the web wizard's server writes its own, redacted, copy). Top-level
# only — nested scripts inherit UI_LOG_FILE and keep writing into it.
ui_log_to_file() {
    [ -n "${UI_LOG_FILE:-}" ] && return 0
    ui_web && return 0
    mkdir -p "${LOGS_DIR:-$PLATFORM_DIR/logs}"
    export UI_LOG_FILE="${LOGS_DIR:-$PLATFORM_DIR/logs}/$1-$(date '+%Y%m%d-%H%M%S').log"
    ( umask 077; : >> "$UI_LOG_FILE" )  # run output can hold links and paths only you should see
    exec > >(tee -a "$UI_LOG_FILE") 2> >(tee -a "$UI_LOG_FILE" >&2)
    log "Logging this run to ${UI_LOG_FILE#"${INSTANCE_DIR:-$PLATFORM_DIR}/"}"
}

# ------------------------------------------------------------------
# One setup/refresh/version switch at a time per checkout
# ------------------------------------------------------------------

# Takes platform/.wizard.lock for this process tree (inherited by nested
# scripts via WIZARD_LOCK_HELD, which then skip it). Fails, naming the holder,
# if another setup/refresh is already running here.
wizard_lock() {
    local lock="${LOCK_FILE:-$PLATFORM_DIR/.wizard.lock}"
    [ -n "${WIZARD_LOCK_HELD:-}" ] && return 0
    command -v flock >/dev/null 2>&1 || return 0
    exec 9<>"$lock"
    if ! flock -n 9; then
        fail "Another wizard (setup, refresh, version switch, pipeline …) is already running here ($(cat "$lock" 2>/dev/null || echo 'unknown')). Wait for it to finish, or stop it first."
    fi
    printf 'PID %s, started %s\n' "$$" "$(date '+%Y-%m-%d %H:%M:%S')" > "$lock"
    export WIZARD_LOCK_HELD=1
}
