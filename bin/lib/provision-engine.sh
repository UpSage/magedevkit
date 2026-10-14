# Provision wizard engine — sourced by bin/local/provision once it has
# resolved ENV_FILE. Never invoke this file directly.
#
# Creates or updates the AWS side of one pipeline-kit environment: the
# CloudFormation stack mdk-<app>-<env> from magento/deploy/infra/stack.json
# and <env>.params.json, the environment's secrets in SSM, then follows the
# first release and can load a database. With --destroy it removes them again.
#
# Safety, in order:
#   - only MageDevKit's own AWS connections (bin/lib/aws.sh), and before every
#     change the connection must reach the account the kit records;
#   - the kit must be current (not outdated, not edited by hand);
#   - secrets are made in bin/lib/provision.py and handed to the AWS CLI as a
#     0600 file, never on a command line or in the log; existing ones are
#     never overwritten;
#   - changes are previewed as a CloudFormation change set; replacing or
#     removing the box (and with it its database) needs the typed environment
#     name; a production stack gets termination protection;
#   - nothing is deleted except by --destroy, after the typed name.
#
# Non-interactive use: PRESET_PROVISION_<KEY> (ENV, CONNECTION, CRYPT_KEY,
# APPLY, CONFIRM=<env>, BUDGET, FOLLOW, DUMP, DELETE_SECRETS).
#
# Expects the caller to have already defined: log(), fail(), ui_* helpers,
# PLATFORM_DIR, ENV_FILE, MAGENTO_DIR, INSTANCE_DIR, PROVISION_DESTROY.

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/aws.sh"

PY="$PLATFORM_DIR/bin/lib/provision.py"
GIT=(git -C "$MAGENTO_DIR")
MANIFEST="$MAGENTO_DIR/deploy/pipeline.json"
FLOW=provision
[ "${PROVISION_DESTROY:-0}" = 1 ] && FLOW=unprovision
WORK="$(mktemp -d)"
chmod 700 "$WORK"
CHANGE_SET=""
CREATED_STACK=0

provision_exit() {
    local rc=$?
    # A change set that was never executed: remove it, and the empty stack a
    # CREATE change set leaves behind (REVIEW_IN_PROGRESS), so nothing lingers.
    if [ -n "$CHANGE_SET" ]; then
        pa cloudformation delete-change-set --stack-name "$STACK" --change-set-name "$CHANGE_SET" >/dev/null 2>&1 || true
        if [ "$CREATED_STACK" = 1 ] && [ "$(stack_status)" = REVIEW_IN_PROGRESS ]; then
            pa cloudformation delete-stack --stack-name "$STACK" >/dev/null 2>&1 || true
        fi
    fi
    rm -rf "$WORK"
    return "$rc"
}
trap provision_exit EXIT

# pv_preset KEY — PRESET_PROVISION_<KEY> ('' when unset).
pv_preset() { local v="PRESET_PROVISION_$1"; printf '%s' "${!v:-}"; }

# confirm_typed QUESTION VALUE PRESET_KEY — the person types VALUE (the page's
# typed confirmation, or the terminal); a scripted run must set
# PRESET_PROVISION_<KEY>=VALUE. Returns 1 when it doesn't match.
confirm_typed() {
    local q="$1" want="$2" got
    got="$(pv_preset "$3")"
    if [ -n "$got" ]; then [ "$got" = "$want" ]; return; fi
    if ui_web; then ui_confirm_destructive "$q" "$want"; return; fi
    ui_interactive || fail "$q — needs a typed confirmation: run it in a terminal, or set PRESET_PROVISION_$3=$want."
    log "$q" >&2
    got="$(prompt_value "Type $want to confirm" 0)"
    [ "$got" = "$want" ]
}

# The target's field from deploy/pipeline.json.
target_field() {
    python3 -c 'import json, sys
t = json.load(open(sys.argv[1])).get("targets", {}).get(sys.argv[2], {})
v = t.get(sys.argv[3], "") if isinstance(t, dict) else ""
print(v if isinstance(v, str) else "")' "$MANIFEST" "$ENV_SEL" "$1" 2>/dev/null
}

# pa ARGS… — the AWS CLI as the chosen connection, in the target's region.
pa() { MDK_AWS_TIMEOUT="${PA_TIMEOUT:-120}" mdk_aws "$CONNECTION" --region "$REGION" "$@"; }

stack_status() {
    pa cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].StackStatus' --output text 2>/dev/null || true
}

stack_output() {
    pa cloudformation describe-stacks --stack-name "$STACK" --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text 2>/dev/null || true
}

# Prints new stack events until the stack settles; returns 0 for *_COMPLETE
# without ROLLBACK, 1 otherwise.
follow_stack() {
    local seen="$WORK/events" status
    : > "$seen"
    while true; do
        pa cloudformation describe-stack-events --stack-name "$STACK" --max-items 50 --output json 2>/dev/null \
            | python3 "$PY" events "$seen" | while IFS= read -r line; do log "  $line"; done
        status="$(stack_status)"
        case "$status" in
            *ROLLBACK_COMPLETE|*ROLLBACK_FAILED|*_FAILED) log "Stack $STACK: $status"; return 1 ;;
            *_COMPLETE) log "✓ Stack $STACK: $status"; return 0 ;;
            "") log "Stack $STACK is gone."; return 0 ;;
        esac
        sleep "${PROVISION_POLL:-10}"
    done
}

# ------------------------------------------------------------------
# 1. Target
# ------------------------------------------------------------------

ui_step "$FLOW" 1
[ -f "$MANIFEST" ] || fail "No pipeline kit in $MAGENTO_DIR/deploy — make the store pipeline-ready first: $(cmd_hint pipeline-wizard)"
mapfile -t TARGETS < <(python3 -c 'import json, sys; [print(k) for k in sorted(json.load(open(sys.argv[1])).get("targets", {}))]' "$MANIFEST")
[ "${#TARGETS[@]}" -gt 0 ] || fail "The kit has no targets yet — run $(cmd_hint pipeline-wizard)."
ENV_SEL="$(pv_preset ENV)"
ENV_SEL="${ENV_SEL:-${PROVISION_ENV:-}}"
if [ -z "$ENV_SEL" ]; then
    if [ "${#TARGETS[@]}" = 1 ]; then ENV_SEL="${TARGETS[0]}"; else
        i="$(prompt_menu "Which environment?" 1 "${TARGETS[@]}")"
        [ "$i" -ge 1 ] || fail "No environment chosen."
        ENV_SEL="${TARGETS[$((i - 1))]}"
    fi
fi
printf '%s\n' "${TARGETS[@]}" | grep -qxF -- "$ENV_SEL" || fail "The kit has no environment '$ENV_SEL' (it has: ${TARGETS[*]})."
APP="$(target_field app)"; ACCOUNT="$(target_field account)"; REGION="$(target_field region)"
DOMAIN="$(target_field domain)"; BRANCH="$(target_field branch)"; PRODUCTION="$(target_field production)"
REPO="$(target_field repo)"; INSTANCE_TYPE="$(target_field instanceType)"; DUMP_BUCKET="$(target_field dumpBucket)"
STACK="mdk-$APP-$ENV_SEL"
valid_account_id "$ACCOUNT" || fail "deploy/pipeline.json has no AWS account for $ENV_SEL — run $(cmd_hint pipeline-wizard) again."
valid_aws_region "$REGION" || fail "deploy/pipeline.json has no region for $ENV_SEL."
[[ "$APP" =~ ^[a-z][a-z0-9-]{1,23}$ ]] || fail "deploy/pipeline.json has no app name for $ENV_SEL."
TEMPLATE="$MAGENTO_DIR/deploy/infra/stack.json"
PARAMS="$MAGENTO_DIR/deploy/infra/$ENV_SEL.params.json"

if [ "$FLOW" = provision ]; then
    # The kit as the wizard wrote it, at this MageDevKit's version.
    KIT_STATUS="$(python3 - "$PLATFORM_DIR/bin/lib/web-wizard" "$INSTANCE_DIR" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import envs, pipeline
print(pipeline.info(envs.Environment(sys.argv[2]))["status"])
PY
)" || KIT_STATUS=unknown
    case "$KIT_STATUS" in
        current) log "✓ The pipeline kit is current" ;;
        outdated) fail "The pipeline kit is older than this MageDevKit — update it first: $(cmd_hint pipeline-wizard)" ;;
        edited) fail "Kit files were changed by hand since the pipeline wizard wrote them — the AWS side is created from the kit as generated. Run $(cmd_hint pipeline-wizard) (it reports them) first." ;;
        *) fail "The pipeline kit isn't usable ($KIT_STATUS) — run $(cmd_hint pipeline-wizard)." ;;
    esac
    [ -f "$TEMPLATE" ] && [ -f "$PARAMS" ] || fail "deploy/infra/stack.json or $ENV_SEL.params.json is missing — run $(cmd_hint pipeline-wizard)."
    python3 "$PLATFORM_DIR/bin/lib/cfn-check.py" "$TEMPLATE" "$PARAMS" || fail "deploy/infra didn't pass its checks (see above)."
    if [ -n "$("${GIT[@]}" status --porcelain -- deploy .platform 2>/dev/null)" ]; then
        log "⚠ The kit has uncommitted changes — the pipeline builds what is on $BRANCH, so commit and push them."
    fi
fi
log "Environment $ENV_SEL of $APP: account $ACCOUNT, $REGION, stack $STACK"

# ------------------------------------------------------------------
# 2. Account — one of MageDevKit's connections, and the right account
# ------------------------------------------------------------------

ui_step "$FLOW" 2
ensure_aws_cli
mapfile -t CONNECTIONS < <(mdk_aws_names 2>/dev/null || true)
[ "${#CONNECTIONS[@]}" -gt 0 ] || fail "MageDevKit has no AWS connections yet — add one with: mdk aws add (or mdk aws import)."
CONNECTION="$(pv_preset CONNECTION)"
[ -n "$CONNECTION" ] || CONNECTION="$(mdk_aws_for_account "$ACCOUNT")"
if [ -z "$CONNECTION" ]; then
    i="$(prompt_menu "Which of MageDevKit's AWS connections reaches account $ACCOUNT?" 1 "${CONNECTIONS[@]}")"
    [ "$i" -ge 1 ] || fail "No connection chosen."
    CONNECTION="${CONNECTIONS[$((i - 1))]}"
fi
mdk_aws_has "$CONNECTION" || fail "There's no MageDevKit connection called '$CONNECTION' (see: mdk aws list)."
mdk_aws_require_account "$CONNECTION" "$ACCOUNT"
mdk_aws_remember "$ACCOUNT" "$CONNECTION"
log "✓ Connection $CONNECTION reaches account $ACCOUNT"

STATUS="$(stack_status)"
if [ -n "$STATUS" ]; then
    TAGS="$(pa cloudformation describe-stacks --stack-name "$STACK" \
        --query "Stacks[0].Tags[?Key=='magedevkit:app' || Key=='magedevkit:env'].Value" --output text 2>/dev/null | tr '\t' ' ')"
    [[ " $TAGS " == *" $APP "* && " $TAGS " == *" $ENV_SEL "* ]] \
        || fail "A stack called $STACK exists but wasn't made by MageDevKit for $APP/$ENV_SEL — nothing was changed."
    log "Stack $STACK exists ($STATUS)"
fi

# ------------------------------------------------------------------
# Destroy (--destroy): its own short flow
# ------------------------------------------------------------------

if [ "$FLOW" = unprovision ]; then
    [ -n "$STATUS" ] || { log "There's no stack $STACK — nothing to remove."; ui_step "$FLOW" 5; exit 0; }
    ui_step "$FLOW" 3
    BUCKET="$(stack_output ArtifactBucket)"
    PROTECTED="$(pa cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].EnableTerminationProtection' --output text 2>/dev/null || true)"
    log "Removing $STACK deletes the box and everything on it (its database included), its DNS record, the pipeline, the images and the build artifacts."
    [ -n "$DUMP_BUCKET" ] && log "The dump bucket $DUMP_BUCKET is not touched."
    if [ "$PRODUCTION" = yes ] || [ "$PROTECTED" = True ]; then
        log "⚠ $ENV_SEL is a production environment."
        confirm_typed "Really remove the PRODUCTION environment $ENV_SEL? Type production-$ENV_SEL." "production-$ENV_SEL" CONFIRM_PRODUCTION \
            || cancel "Nothing was removed."
    fi
    confirm_typed "Remove environment $ENV_SEL ($STACK in account $ACCOUNT)?" "$ENV_SEL" CONFIRM \
        || cancel "Nothing was removed."
    mdk_aws_require_account "$CONNECTION" "$ACCOUNT"
    ui_step "$FLOW" 4
    if [ "$PROTECTED" = True ]; then
        ui_task "Turning off termination protection" pa cloudformation update-termination-protection \
            --stack-name "$STACK" --no-enable-termination-protection
    fi
    if [ -n "$BUCKET" ] && [ "$BUCKET" != None ]; then
        ui_task "Emptying the artifact bucket $BUCKET" pa s3 rm "s3://$BUCKET" --recursive --only-show-errors
    fi
    ui_task "Deleting stack $STACK" pa cloudformation delete-stack --stack-name "$STACK" || fail "CloudFormation refused to delete $STACK (see above)."
    follow_stack || fail "The stack didn't delete cleanly — see the events above (CloudFormation console → $STACK)."
    pa budgets delete-budget --account-id "$ACCOUNT" --budget-name "$STACK" >/dev/null 2>&1 && log "✓ Removed the budget alert $STACK"
    mapfile -t PARAMS_LEFT < <(pa ssm describe-parameters --parameter-filters "Key=Path,Values=/$APP/$ENV_SEL" \
        --query 'Parameters[].Name' --output text 2>/dev/null | tr '\t' '\n' | grep . || true)
    if [ "${#PARAMS_LEFT[@]}" -gt 0 ]; then
        DEL="$(pv_preset DELETE_SECRETS)"
        if [ -z "$DEL" ]; then
            prompt_yes_no "Also delete the environment's secrets in SSM (${PARAMS_LEFT[*]})? Keep them to rebuild it with the same keys." N && DEL=yes
        fi
        if [ "$DEL" = yes ]; then
            ui_task "Deleting the secrets under /$APP/$ENV_SEL" pa ssm delete-parameters --names "${PARAMS_LEFT[@]}"
        else
            log "Kept the secrets under /$APP/$ENV_SEL."
        fi
    fi
    ui_step "$FLOW" 5
    ui_result text "Removed" "$STACK (account $ACCOUNT, $REGION)"
    log "Environment $ENV_SEL removed."
    exit 0
fi

# ------------------------------------------------------------------
# 3. Checks — read-only, before anything is created
# ------------------------------------------------------------------

ui_step provision 3
param_value() {
    python3 -c 'import json, sys
for p in json.load(open(sys.argv[1])):
    if p.get("ParameterKey") == sys.argv[2]:
        print(p.get("ParameterValue", ""))' "$PARAMS" "$1"
}
ZONE_ID="$(param_value HostedZoneId)"; CONN_ARN="$(param_value GithubConnectionArn)"
IMAGE_ID="$(param_value ImageId)"; VOLUME_GB="$(param_value VolumeGb)"; OFF_HOURS="$(param_value OffHours)"
PROBLEMS=0
problem() { log "✗ $1"; PROBLEMS=$((PROBLEMS + 1)); }

ZONE_NAME="$(pa route53 get-hosted-zone --id "$ZONE_ID" --query 'HostedZone.Name' --output text 2>/dev/null || true)"
if [ -z "$ZONE_NAME" ] || [ "$ZONE_NAME" = None ]; then problem "Hosted zone $ZONE_ID isn't in account $ACCOUNT."
else
    ZONE_NAME="${ZONE_NAME%.}"
    case "$DOMAIN" in *"$ZONE_NAME") log "✓ Hosted zone $ZONE_NAME ($ZONE_ID)" ;; *) problem "$DOMAIN isn't inside the hosted zone $ZONE_NAME." ;; esac
fi

while true; do
    CONN_STATUS="$(pa codestar-connections get-connection --connection-arn "$CONN_ARN" --query 'Connection.ConnectionStatus' --output text 2>/dev/null || true)"
    [ "$CONN_STATUS" = AVAILABLE ] && { log "✓ GitHub connection is available"; break; }
    log "⚠ The GitHub connection is ${CONN_STATUS:-not found}: approve it in the AWS console → Developer Tools → Settings → Connections ($REGION)."
    prompt_yes_no "Check the GitHub connection again?" Y || { problem "The GitHub connection isn't available."; break; }
done

OFFERED="$(pa ec2 describe-instance-type-offerings --location-type region --filters "Name=instance-type,Values=$INSTANCE_TYPE" \
    --query 'InstanceTypeOfferings[0].InstanceType' --output text 2>/dev/null || true)"
[ "$OFFERED" = "$INSTANCE_TYPE" ] && log "✓ $INSTANCE_TYPE is offered in $REGION" || problem "$INSTANCE_TYPE isn't offered in $REGION."

IMAGE_OK="$(pa ec2 describe-images --image-ids "$IMAGE_ID" --query 'Images[0].ImageId' --output text 2>/dev/null || true)"
[ "$IMAGE_OK" = "$IMAGE_ID" ] && log "✓ Image $IMAGE_ID exists in $REGION" || problem "Image $IMAGE_ID doesn't exist in $REGION — run the pipeline wizard again to look up the current one."

if [ -z "$STATUS" ]; then
    EIP_QUOTA="$(pa service-quotas get-service-quota --service-code ec2 --quota-code L-0263D0A3 --query 'Quota.Value' --output text 2>/dev/null || true)"
    EIP_USED="$(pa ec2 describe-addresses --query 'length(Addresses)' --output text 2>/dev/null || true)"
    if [[ "$EIP_QUOTA" =~ ^[0-9.]+$ ]] && [[ "$EIP_USED" =~ ^[0-9]+$ ]]; then
        [ "$EIP_USED" -lt "${EIP_QUOTA%.*}" ] && log "✓ Elastic IPs: $EIP_USED of ${EIP_QUOTA%.*} used" \
            || problem "All ${EIP_QUOTA%.*} Elastic IPs of $REGION are in use — release one or ask AWS for more."
    fi
    VPC_USED="$(pa ec2 describe-vpcs --query 'length(Vpcs)' --output text 2>/dev/null || true)"
    VPC_QUOTA="$(pa service-quotas get-service-quota --service-code vpc --quota-code L-F678F1CE --query 'Quota.Value' --output text 2>/dev/null || true)"
    if [[ "$VPC_QUOTA" =~ ^[0-9.]+$ ]] && [[ "$VPC_USED" =~ ^[0-9]+$ ]]; then
        [ "$VPC_USED" -lt "${VPC_QUOTA%.*}" ] && log "✓ VPCs: $VPC_USED of ${VPC_QUOTA%.*} used" \
            || problem "All ${VPC_QUOTA%.*} VPCs of $REGION are in use."
    fi
fi

if [ -n "$DUMP_BUCKET" ]; then
    pa s3api head-bucket --bucket "$DUMP_BUCKET" >/dev/null 2>&1 && log "✓ Dump bucket $DUMP_BUCKET is readable" \
        || problem "Can't read the dump bucket $DUMP_BUCKET with connection $CONNECTION."
fi
[ "$PROBLEMS" = 0 ] || fail "$PROBLEMS check(s) failed — nothing was created. Fix them and run the wizard again."

# ------------------------------------------------------------------
# 4. Secrets — SSM SecureStrings, made here, never overwritten
# ------------------------------------------------------------------

ui_step provision 4
TAGS_JSON="$(python3 -c 'import json, sys; print(json.dumps([{"Key": "magedevkit:app", "Value": sys.argv[1]}, {"Key": "magedevkit:env", "Value": sys.argv[2]}]))' "$APP" "$ENV_SEL")"
mapfile -t HAVE < <(pa ssm describe-parameters --parameter-filters "Key=Path,Values=/$APP/$ENV_SEL" \
    --query 'Parameters[].Name' --output text 2>/dev/null | tr '\t' '\n' | grep . || true)
have_secret() { printf '%s\n' "${HAVE[@]}" | grep -qxF -- "/$APP/$ENV_SEL/$1"; }

# put_secret NAME KIND [SOURCE] — one SecureString; the value never leaves provision.py's file.
put_secret() {
    local file="$WORK/secret.json"
    python3 "$PY" secret "$2" "/$APP/$ENV_SEL/$1" "$file" "${3:-}" "$TAGS_JSON" || fail "Couldn't make the secret $1."
    pa ssm put-parameter --cli-input-json "file://$file" >/dev/null || { rm -f "$file"; fail "Couldn't store /$APP/$ENV_SEL/$1 in SSM."; }
    rm -f "$file"
    log "✓ Stored /$APP/$ENV_SEL/$1 (SecureString)"
}

for s in db_password db_root_password rabbitmq_password; do
    have_secret "$s" && { log "✓ /$APP/$ENV_SEL/$s exists — kept"; continue; }
    put_secret "$s" password
done

if have_secret crypt_key; then log "✓ /$APP/$ENV_SEL/crypt_key exists — kept"
else
    CRYPT="$(pv_preset CRYPT_KEY)"
    if [ -z "$CRYPT" ]; then
        i="$(prompt_menu "Magento's crypt key for $ENV_SEL (it decrypts saved payment and integration settings in the database)" 1 \
            'new — a fresh key (stored secrets from another database won'\''t decrypt here)' \
            'this instance — the key in this instance'\''s app/etc/env.php (right when the dumps come from the same store)')"
        CRYPT="$([ "$i" = 2 ] && echo instance || echo new)"
    fi
    case "$CRYPT" in
        new) put_secret crypt_key cryptkey ;;
        instance) put_secret crypt_key envphp "$MAGENTO_DIR/app/etc/env.php" ;;
        *) fail "PRESET_PROVISION_CRYPT_KEY must be new or instance." ;;
    esac
fi

if have_secret composer_auth; then log "✓ /$APP/$ENV_SEL/composer_auth exists — kept"
else
    [ -f "$MAGENTO_DIR/auth.json" ] || fail "magento/auth.json is missing — the build needs your repo.magento.com key. Add it (as for a local composer install), then run the wizard again."
    put_secret composer_auth authjson "$MAGENTO_DIR/auth.json"
fi

# ------------------------------------------------------------------
# 5. Review — a change set: what would change, before anything does
# ------------------------------------------------------------------

ui_step provision 5
COMPACT="$WORK/stack.json"
python3 -c 'import json, sys; json.dump(json.load(open(sys.argv[1])), open(sys.argv[2], "w"), separators=(",", ":"))' "$TEMPLATE" "$COMPACT"
KIT_VERSION="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("templateVersion", ""))' "$MANIFEST")"
CS_TYPE=UPDATE
[ -z "$STATUS" ] && { CS_TYPE=CREATE; CREATED_STACK=1; }
case "$STATUS" in
    ROLLBACK_COMPLETE|ROLLBACK_FAILED|CREATE_FAILED)
        fail "Stack $STACK failed to create earlier ($STATUS) and must be removed first: $(cmd_hint provision --destroy "$ENV_SEL")" ;;
    *_IN_PROGRESS) [ "$STATUS" = REVIEW_IN_PROGRESS ] || fail "Stack $STACK is busy ($STATUS) — wait for it to finish, then run the wizard again." ;;
esac
[ "$STATUS" = REVIEW_IN_PROGRESS ] && CS_TYPE=CREATE
CHANGE_SET="mdk-$(date -u +%Y%m%d%H%M%S)"
ui_task "Asking CloudFormation what would change" pa cloudformation create-change-set --stack-name "$STACK" \
    --change-set-name "$CHANGE_SET" --change-set-type "$CS_TYPE" --template-body "file://$COMPACT" \
    --parameters "file://$PARAMS" --capabilities CAPABILITY_NAMED_IAM \
    --tags "Key=magedevkit:app,Value=$APP" "Key=magedevkit:env,Value=$ENV_SEL" "Key=magedevkit:kit,Value=$KIT_VERSION" \
    --query Id --output text || fail "CloudFormation refused the change set (see the AWS message above)."
if ! PA_TIMEOUT=900 pa cloudformation wait change-set-create-complete --stack-name "$STACK" --change-set-name "$CHANGE_SET" 2>/dev/null; then
    REASON="$(pa cloudformation describe-change-set --stack-name "$STACK" --change-set-name "$CHANGE_SET" --query StatusReason --output text 2>/dev/null || true)"
    case "$REASON" in
        *"didn't contain changes"*|*"No updates are to be performed"*)
            log "✓ The stack already matches the kit — nothing to change."
            NO_CHANGES=1 ;;
        *) fail "CloudFormation can't make this change: ${REASON:-unknown reason}" ;;
    esac
fi

if [ "${NO_CHANGES:-0}" != 1 ]; then
    CHANGES="$(pa cloudformation describe-change-set --stack-name "$STACK" --change-set-name "$CHANGE_SET" --output json \
        | python3 "$PY" changes)" || fail "Couldn't read the change set."
    DANGER="$(printf '%s\n' "$CHANGES" | sed -n 's/^danger\t//p')"
    ADD="$(printf '%s\n' "$CHANGES" | grep -c $'^Add\t' || true)"
    MOD="$(printf '%s\n' "$CHANGES" | grep -c $'^Modify\t' || true)"
    REM="$(printf '%s\n' "$CHANGES" | grep -c $'^Remove\t' || true)"
    printf '%s\n' "$CHANGES" | grep -v '^danger' | while IFS=$'\t' read -r a l t r; do
        log "  $a $l ($t)$([ "$r" = True ] && printf ' — REPLACED')"
    done
    COST="$(python3 "$PY" cost "$INSTANCE_TYPE" "${VOLUME_GB:-50}" "${OFF_HOURS:-no}")"
    ui_review "Ready to $([ "$CS_TYPE" = CREATE ] && echo create || echo update) environment $ENV_SEL" \
        "Stack" "$STACK in account $ACCOUNT, $REGION" \
        "Store" "https://$DOMAIN/ (from $REPO @ $BRANCH)" \
        "Changes" "$ADD to add, $MOD to change, $REM to remove" \
        "Box" "$INSTANCE_TYPE, ${VOLUME_GB} GB$([ "$OFF_HOURS" = yes ] && printf ', stopped evenings and weekends')" \
        "Cost" "${COST:+about \$$COST a month (estimate: box, disk, IP, logs and builds)}" \
        "Production" "$PRODUCTION$([ "$PRODUCTION" = yes ] && printf ' — protected from deletion')" \
        || cancel "Stopped at the review — nothing was changed in AWS."
    if [ -n "$DANGER" ]; then
        log "⚠ This change removes or replaces: $DANGER — the box and the database on its disk would be lost."
        confirm_typed "Replace or remove the box of $ENV_SEL (its database is lost)? Type $ENV_SEL." "$ENV_SEL" CONFIRM \
            || cancel "Nothing was changed in AWS."
    fi
    [ "$(pv_preset APPLY)" = no ] && cancel "PRESET_PROVISION_APPLY=no — stopped after the review; nothing was changed."

# ------------------------------------------------------------------
# 6. Apply
# ------------------------------------------------------------------

    ui_step provision 6
    mdk_aws_require_account "$CONNECTION" "$ACCOUNT"
    ui_task "Starting the change" pa cloudformation execute-change-set --stack-name "$STACK" --change-set-name "$CHANGE_SET" \
        || fail "CloudFormation didn't start the change (see above)."
    CHANGE_SET=""   # executed: nothing to clean up any more
    log "Following the stack (a new environment takes about 5–10 minutes) ..."
    follow_stack || fail "The change failed and was rolled back — see the events above."
else
    ui_step_skip provision 6
    # The empty change set ("no changes") isn't needed any more.
    pa cloudformation delete-change-set --stack-name "$STACK" --change-set-name "$CHANGE_SET" >/dev/null 2>&1 || true
    CHANGE_SET=""
fi

if [ "$PRODUCTION" = yes ]; then
    pa cloudformation update-termination-protection --stack-name "$STACK" --enable-termination-protection >/dev/null \
        && log "✓ Termination protection is on (production)"
fi

# A cost alert (AWS Budgets, free): offered once per environment, never required.
BUDGET="$(pv_preset BUDGET)"
COST="${COST:-$(python3 "$PY" cost "$INSTANCE_TYPE" "${VOLUME_GB:-50}" "${OFF_HOURS:-no}")}"
EMAIL="$(sed -n 's/^ACME_EMAIL=//p' "$MAGENTO_DIR/deploy/targets/$ENV_SEL.env" 2>/dev/null | head -1)"
ALERT=""
[ -n "$COST" ] && ALERT=$(( (COST * 12 + 9) / 10 ))
if [ -n "$ALERT" ] && [ -n "$EMAIL" ] && [ "$BUDGET" != no ] \
    && ! pa budgets describe-budget --account-id "$ACCOUNT" --budget-name "$STACK" >/dev/null 2>&1; then
    [ -n "$BUDGET" ] || { prompt_yes_no "Email $EMAIL when this AWS account's monthly costs pass \$$ALERT (an AWS Budgets alert, free)?" Y && BUDGET=yes; }
else
    BUDGET=no
fi
if [ "$BUDGET" = yes ]; then
    python3 -c 'import json, sys
name, amount, email, out = sys.argv[1:5]
json.dump({"BudgetName": name, "BudgetLimit": {"Amount": amount, "Unit": "USD"}, "TimeUnit": "MONTHLY",
           "BudgetType": "COST"}, open(out + "/budget.json", "w"))
json.dump([{"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN", "Threshold": 100,
                             "ThresholdType": "PERCENTAGE"},
            "Subscribers": [{"SubscriptionType": "EMAIL", "Address": email}]}], open(out + "/notify.json", "w"))' \
        "$STACK" "$ALERT" "$EMAIL" "$WORK"
    pa budgets create-budget --account-id "$ACCOUNT" --budget "file://$WORK/budget.json" \
        --notifications-with-subscribers "file://$WORK/notify.json" >/dev/null 2>&1 \
        && log "✓ Budget alert $STACK: $EMAIL above \$$ALERT a month" \
        || log "⚠ Couldn't create the budget alert (the connection may not allow AWS Budgets) — nothing else depends on it."
fi

# ------------------------------------------------------------------
# 7. First release — the pipeline starts by itself when it's created
# ------------------------------------------------------------------

ui_step provision 7
PIPELINE="$(stack_output PipelineName)"
INSTANCE_ID="$(stack_output InstanceId)"
FOLLOW="$(pv_preset FOLLOW)"
[ -n "$FOLLOW" ] || { prompt_yes_no "Follow the pipeline until the release is on the box (10–30 minutes)?" Y && FOLLOW=yes || FOLLOW=no; }
RELEASED=0
if [ "$FOLLOW" = yes ] && [ -n "$PIPELINE" ]; then
    deadline=$((SECONDS + ${PROVISION_PIPELINE_TIMEOUT:-3600}))
    last=""
    while [ "$SECONDS" -lt "$deadline" ]; do
        state="$(pa codepipeline get-pipeline-state --name "$PIPELINE" \
            --query 'stageStates[].[stageName,latestExecution.status]' --output text 2>/dev/null | tr '\t\n' ': ' || true)"
        [ "$state" != "$last" ] && log "  $state"
        last="$state"
        case "$state" in
            *Deploy:Succeeded*) RELEASED=1; break ;;
            *Source:Failed*) log "⚠ The pipeline couldn't read $REPO @ $BRANCH — push the kit to $BRANCH, then: Release change (AWS console or MageDevKit)."; break ;;
            *Failed*) log "⚠ The pipeline failed — its build and deploy logs are in the AWS console (CodePipeline → $PIPELINE)."; break ;;
        esac
        sleep "${PROVISION_POLL:-15}"
    done
    [ "$RELEASED" = 1 ] && log "✓ The first release is on the box"
else
    ui_step_skip provision 7
fi

# ------------------------------------------------------------------
# 8. Database
# ------------------------------------------------------------------

ui_step provision 8
BOX_STATE="$(pa ssm get-parameter --name "/$APP/$ENV_SEL/status" --query Parameter.Value --output text 2>/dev/null \
    | python3 -c 'import json, sys
try:
    print(json.loads(sys.stdin.read()).get("state", ""))
except ValueError:
    print("")' || true)"
DUMP_URI=""
if [ "$RELEASED" = 1 ] && [ "$BOX_STATE" = awaiting-database ]; then
    DUMP_URI="$(pv_preset DUMP)"
    if [ -z "$DUMP_URI" ] && [ -n "$DUMP_BUCKET" ]; then
        mapfile -t DUMPS < <(pa s3api list-objects-v2 --bucket "$DUMP_BUCKET" \
            --query 'reverse(sort_by(Contents[?ends_with(Key, `.sql.gz`)], &LastModified))[:10].Key' --output text 2>/dev/null \
            | tr '\t' '\n' | grep . || true)
        if [ "${#DUMPS[@]}" -gt 0 ]; then
            i="$(prompt_menu "The store waits for a database. Load which dump from $DUMP_BUCKET?" 1 "${DUMPS[@]}" 'Not now')"
            [ "$i" -ge 1 ] && [ "$i" -le "${#DUMPS[@]}" ] && DUMP_URI="s3://$DUMP_BUCKET/${DUMPS[$((i - 1))]}"
        fi
    fi
    if [ -n "$DUMP_URI" ]; then
        valid_s3_uri "$DUMP_URI" || fail "'$DUMP_URI' isn't an s3://bucket/key."
        confirm_typed "Load $DUMP_URI into $ENV_SEL?" "$ENV_SEL" CONFIRM || cancel "No database loaded."
        CMD_ID="$(pa ssm send-command --document-name "$APP-$ENV_SEL-box" --instance-ids "$INSTANCE_ID" \
            --parameters "$(python3 -c 'import json, sys; print(json.dumps({"command": ["refresh"], "arg": [sys.argv[1]]}))' "$DUMP_URI")" \
            --cloud-watch-output-config "CloudWatchOutputEnabled=true,CloudWatchLogGroupName=/$APP/$ENV_SEL/commands" \
            --query Command.CommandId --output text)" || fail "Couldn't start the refresh on the box."
        log "Loading the database on the box (command $CMD_ID) ..."
        while true; do
            st="$(pa ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" --query Status --output text 2>/dev/null || true)"
            case "$st" in
                Success) log "✓ Database loaded — the store is live"; break ;;
                Failed|Cancelled|TimedOut|Cancelling)
                    pa ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
                        --query StandardErrorContent --output text 2>/dev/null | tail -20 | while IFS= read -r l; do log "  $l"; done
                    fail "Loading the database $st — the log is in CloudWatch /$APP/$ENV_SEL/commands." ;;
            esac
            sleep "${PROVISION_POLL:-15}"
        done
    fi
fi

# ------------------------------------------------------------------
# 9. Done
# ------------------------------------------------------------------

ui_step provision 9
ui_result link "Store" "https://$DOMAIN/"
ui_result text "Stack" "$STACK — account $ACCOUNT, $REGION"
OPERATOR="$(stack_output OperatorPolicyArn)"
[ -n "$OPERATOR" ] && ui_result copy "Operator policy for teammates (attach it instead of admin access)" "$OPERATOR"
if [ "$RELEASED" != 1 ]; then
    ui_result text "Next" "Push the kit to $BRANCH; the pipeline builds and deploys it."
elif [ -z "$DUMP_URI" ] && [ "$BOX_STATE" = awaiting-database ]; then
    ui_result text "Next" "The store waits for a database: run this wizard again, or on the box: box refresh s3://<bucket>/<dump>.sql.gz"
fi
ui_result command "Run this wizard again (after a kit update, or to load a database)" "$(cmd_hint provision-wizard)"
log "Environment $ENV_SEL is provisioned."
