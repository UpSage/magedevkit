# S3 media storage — the part of "use Amazon S3 for pub/media" that setup
# (bin/lib/setup-engine.sh) and the DB refresh questions
# (bin/lib/sync-db-config-engine.sh) share. Sourced, never run.
#
# Expects bin/lib/ui.sh (log, fail, prompt_aws_profile, aws_configure_profile,
# prompt_s3_bucket, s3_bucket_region, ui_interactive), bin/lib/env.sh
# (set_env_var), ENV_FILE and ENV_NAME, and the AWS CLI on PATH.

# s3_storage_choose ENVRC CURRENT_BUCKET — asks for the AWS profile and the
# bucket, and checks both work. Sets AWS_PROFILE (exported), RS_REGION,
# RS_BUCKET, AWS_KEY_ID and AWS_SECRET. Writes only the profile *name* to
# ENVRC (for direnv) and AWS_PROFILE_DEFAULT to the env file — never keys.
s3_storage_choose() {
    local envrc="$1" current_bucket="$2" bucket_region fallback_region
    # shellcheck disable=SC1090
    [ -f "$envrc" ] && source "$envrc"
    # The profile decides which buckets are offered next — picked from the
    # profiles on this computer (or a new one), never assumed.
    AWS_PROFILE="$(prompt_aws_profile "${AWS_PROFILE:-${AWS_PROFILE_DEFAULT:-}}")"
    [ -n "$AWS_PROFILE" ] || fail "No AWS CLI profile chosen — pick one, or choose local file storage."
    export AWS_PROFILE
    log "Writing .envrc (profile name only — never real keys): AWS_PROFILE=$AWS_PROFILE"
    printf 'export AWS_PROFILE=%s\nexport AWS_S3_PROFILE=%s\n' "$AWS_PROFILE" "$AWS_PROFILE" > "$envrc"
    set_env_var "$ENV_FILE" AWS_PROFILE_DEFAULT "$AWS_PROFILE"

    # Only runs `aws configure` when this profile isn't set up yet, so a
    # repeat run doesn't ask for keys it already has.
    if ! grep -q "\[${AWS_PROFILE}\]" ~/.aws/credentials 2>/dev/null && ! grep -q "\[profile ${AWS_PROFILE}\]" ~/.aws/config 2>/dev/null; then
        log "Configuring AWS CLI profile '$AWS_PROFILE' ..."
        aws_configure_profile "$AWS_PROFILE"
    else
        log "AWS profile '$AWS_PROFILE' already configured."
    fi

    if ! aws sts get-caller-identity --profile "$AWS_PROFILE" >/dev/null 2>&1; then
        log "AWS credentials for profile '$AWS_PROFILE' could not be verified — let's try again."
        aws_configure_profile "$AWS_PROFILE"
        aws sts get-caller-identity --profile "$AWS_PROFILE" >/dev/null 2>&1 \
            || fail "AWS credentials for profile '$AWS_PROFILE' still couldn't be verified (aws sts get-caller-identity failed) — check the keys and try again."
    fi
    log "✓ AWS credentials verified (profile $AWS_PROFILE)"

    RS_REGION="$(aws configure get region --profile "$AWS_PROFILE" 2>/dev/null || true)"
    if [ -z "$RS_REGION" ]; then
        log "AWS profile '$AWS_PROFILE' has no default region set — let's set one."
        aws_configure_profile "$AWS_PROFILE"
        RS_REGION="$(aws configure get region --profile "$AWS_PROFILE" 2>/dev/null || true)"
    fi
    fallback_region="${REMOTE_STORAGE_REGION:-}"
    [ "$fallback_region" = CHANGE_ME ] && fallback_region=""
    [ -n "$RS_REGION" ] || RS_REGION="$fallback_region"
    [ -n "$RS_REGION" ] || fail "No AWS region available — set one on the AWS profile ('aws configure --profile $AWS_PROFILE'), or REMOTE_STORAGE_REGION in $ENV_NAME."
    log "✓ AWS region: $RS_REGION"

    # No default bucket on purpose — an explicit one is always chosen (from
    # the profile's buckets, or typed); the current one is offered.
    while true; do
        RS_BUCKET="$(prompt_s3_bucket "$AWS_PROFILE" "$current_bucket")"
        [ -n "$RS_BUCKET" ] && break
        ui_interactive || fail "S3 storage needs a bucket name — set REMOTE_STORAGE_BUCKET in $ENV_NAME, or choose local filesystem storage."
        log "S3 bucket can't be blank."
    done

    log "Verifying S3 access ..."
    aws s3api head-bucket --bucket "$RS_BUCKET" --profile "$AWS_PROFILE" 2>/dev/null \
        || fail "Could not access S3 bucket '$RS_BUCKET' with profile '$AWS_PROFILE' — check the bucket name and permissions."
    log "✓ S3 bucket accessible"
    bucket_region="$(s3_bucket_region "$RS_BUCKET" "$AWS_PROFILE")"
    if [ -n "$bucket_region" ] && [ "$bucket_region" != "$RS_REGION" ]; then
        log "Bucket $RS_BUCKET is in $bucket_region (the profile's default is $RS_REGION) — using $bucket_region."
        RS_REGION="$bucket_region"
    fi

    AWS_KEY_ID="$(aws configure get aws_access_key_id --profile "$AWS_PROFILE" 2>/dev/null || true)"
    AWS_SECRET="$(aws configure get aws_secret_access_key --profile "$AWS_PROFILE" 2>/dev/null || true)"
    if [ -z "$AWS_KEY_ID" ] || [ -z "$AWS_SECRET" ]; then
        if [ -n "$(aws configure get sso_session --profile "$AWS_PROFILE" 2>/dev/null)$(aws configure get sso_start_url --profile "$AWS_PROFILE" 2>/dev/null)$(aws configure get role_arn --profile "$AWS_PROFILE" 2>/dev/null)" ]; then
            fail "AWS profile '$AWS_PROFILE' signs in with SSO or a role, so it has no long-term keys Magento could keep for S3 media. Use a profile with an access key — ideally a dedicated IAM user that may only use this bucket."
        fi
        fail "Could not read keys back from AWS profile '$AWS_PROFILE' — run 'aws configure --profile $AWS_PROFILE' and try again."
    fi
    s3_profile_scope_check "$AWS_PROFILE"
}

# s3_profile_scope_check PROFILE — warns when PROFILE may do far more than use
# a bucket: its keys are copied into this store's .env and env.php, where
# Magento and every module in it can read them. Asks IAM's policy simulator
# about a few admin actions; when the profile may not run the simulator (or
# isn't an IAM user), nothing is said. A yes is remembered per profile
# (S3_WIDE_PROFILE_OK in the env file); a scripted run says yes with
# PRESET_S3_WIDE_PROFILE=Y.
s3_profile_scope_check() {
    local arn wide
    [ "$(env_get "$ENV_FILE" S3_WIDE_PROFILE_OK)" = "$1" ] && return 0
    arn="$(aws sts get-caller-identity --profile "$1" --query Arn --output text 2>/dev/null)" || return 0
    [[ "$arn" == arn:aws*:iam::*:user/* ]] || return 0
    wide="$(timeout 20 aws iam simulate-principal-policy --profile "$1" --policy-source-arn "$arn" \
        --action-names iam:CreateUser ec2:RunInstances cloudformation:CreateStack s3:DeleteBucket \
        --query 'EvaluationResults[?EvalDecision==`allowed`].EvalActionName' --output text 2>/dev/null)" || return 0
    wide="$(printf '%s' "$wide" | tr '\t\n' '  ' | sed 's/None//g; s/  */ /g; s/^ //; s/ $//')"
    [ -n "$wide" ] || return 0
    log "⚠ AWS profile '$1' may also do: $wide."
    log "  Its keys are copied into this store's $ENV_NAME and env.php, where Magento and every module can read them."
    log "  Safer: a dedicated IAM user that may only use the media bucket (s3:ListBucket, GetObject, PutObject, DeleteObject)."
    if [ "${PRESET_S3_WIDE_PROFILE:-}" != Y ]; then
        prompt_yes_no "Use profile '$1' for media anyway?" N \
            || cancel "Choose a profile limited to the media bucket, or local file storage."
    fi
    set_env_var "$ENV_FILE" S3_WIDE_PROFILE_OK "$1"
}

# s3_storage_save DRIVER [BUCKET REGION KEY SECRET] — the REMOTE_STORAGE_*
# lines in the env file (blank for the local "file" driver).
s3_storage_save() {
    set_env_var "$ENV_FILE" REMOTE_STORAGE_DRIVER "$1"
    set_env_var "$ENV_FILE" REMOTE_STORAGE_BUCKET "${2:-}"
    set_env_var "$ENV_FILE" REMOTE_STORAGE_REGION "${3:-}"
    set_env_var "$ENV_FILE" REMOTE_STORAGE_KEY "${4:-}"
    set_env_var "$ENV_FILE" REMOTE_STORAGE_SECRET "${5:-}"
}

# Magento's own config writer, run inside the php container by
# remote_storage_apply: env.php's remote_storage from the environment.
RS_APPLY_PHP='require "app/bootstrap.php";
$om = \Magento\Framework\App\Bootstrap::create(BP, $_SERVER)->getObjectManager();
$cfg = ["driver" => "aws-s3", "config" => ["bucket" => getenv("RS_APPLY_BUCKET"), "region" => getenv("RS_APPLY_REGION")]];
if (getenv("RS_APPLY_KEY") !== "" && getenv("RS_APPLY_SECRET") !== "") {
    $cfg["config"]["credentials"] = ["key" => getenv("RS_APPLY_KEY"), "secret" => getenv("RS_APPLY_SECRET")];
}
$om->get(\Magento\Framework\App\DeploymentConfig\Writer::class)->saveConfig(
    [\Magento\Framework\Config\File\ConfigFilePool::APP_ENV => ["remote_storage" => $cfg]], true);
echo "remote_storage: aws-s3, bucket ", $cfg["config"]["bucket"], ", region ", $cfg["config"]["region"], PHP_EOL;'

# remote_storage_apply BUCKET REGION KEY SECRET — points Magento's media at
# the S3 bucket (env.php remote_storage, the same thing
# `setup:config:set --remote-storage-driver=aws-s3 …` writes). The key and
# secret travel in the php container's environment (docker compose exec -e
# NAME takes the value from here), never on a command line, where
# `--remote-storage-secret=…` would show them in the process list.
remote_storage_apply() {
    if [ -z "${EXEC_USER:-}" ]; then
        # shellcheck disable=SC1091
        source "$PLATFORM_DIR/bin/lib/identity.sh"
    fi
    RS_APPLY_BUCKET="$1" RS_APPLY_REGION="$2" RS_APPLY_KEY="$3" RS_APPLY_SECRET="$4" \
        "$PLATFORM_DIR/bin/platform" exec -T \
        -e RS_APPLY_BUCKET -e RS_APPLY_REGION -e RS_APPLY_KEY -e RS_APPLY_SECRET \
        --user "$EXEC_USER" php php -r "$RS_APPLY_PHP"
}
