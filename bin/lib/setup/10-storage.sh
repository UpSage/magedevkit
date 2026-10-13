# Setup wizard, part 10 of 11 — Step 11: the media storage driver (local or S3).
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento Setup: Storage driver (AWS/S3 only entered from here on,
# and only if aws-s3 is actually chosen)
# ------------------------------------------------------------------

ui_step setup 11

# s3_storage_choose / s3_storage_save
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/aws-storage.sh"
if grep -q '^REMOTE_STORAGE_DRIVER=CHANGE_ME' "$ENV_FILE"; then
    log "Magento storage driver"
    RS_DRIVER_INPUT="$(prompt_menu 'Where should Magento store media files (pub/media)?' 1 \
        'Local filesystem — this machine only' \
        'Amazon S3 — a shared bucket other environments can also read')"
    case "$RS_DRIVER_INPUT" in
        1) RS_DRIVER="file" ;;
        2) RS_DRIVER="aws-s3" ;;
    esac

    if [ "$RS_DRIVER" = "file" ]; then
        s3_storage_save file
        log "✓ Using local filesystem storage"
        "$PLATFORM_DIR/bin/magento" setup:config:set --remote-storage-driver=file -n
    else
        # ---- aws-s3: AWS becomes relevant only from here on ----
        if ! command -v aws >/dev/null 2>&1; then
            prompt_yes_no 'AWS CLI is not installed. Install it now?' Y \
                || fail "S3 storage requires the AWS CLI — re-run bin/local/setup and choose 'file' storage instead, or install the AWS CLI yourself first."
            install_aws_cli
        else
            log "aws CLI already installed."
        fi

        command -v direnv >/dev/null 2>&1 || install_direnv
        # Profile, credentials, region, bucket and its keys — bin/lib/aws-storage.sh.
        s3_storage_choose "$INSTANCE_DIR/.envrc" "$(env_get "$ENV_FILE" REMOTE_STORAGE_BUCKET | sed 's/^CHANGE_ME$//')"
        direnv allow "$INSTANCE_DIR" >/dev/null
        s3_storage_save aws-s3 "$RS_BUCKET" "$RS_REGION" "$AWS_KEY_ID" "$AWS_SECRET"
        log "Wrote S3 remote storage config to $ENV_NAME (bucket=$RS_BUCKET, region=$RS_REGION)."

        log "Applying remote storage configuration to Magento ..."
        remote_storage_apply "$RS_BUCKET" "$RS_REGION" "$AWS_KEY_ID" "$AWS_SECRET"
    fi
else
    log "✓ Storage already configured (driver=$(env_get "$ENV_FILE" REMOTE_STORAGE_DRIVER))."
fi

# Re-source: the storage block above may have just written
# REMOTE_STORAGE_*/AWS_PROFILE_DEFAULT to the env file via set_env_var,
# which only updates the file, not this shell's already-sourced copies of
# those variables — refresh them so the Optional S3 Sync/Verification
# phases below see the real current values instead of stale ones from
# before this run's storage configuration.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Whatever the storage driver ended up being (just configured above, or
# already configured on a previous run) — resolve it once here, and make
# sure AWS_PROFILE is available in *this* run's shell either way, since
# the Optional S3 Sync and Verification phases below need it too.
REMOTE_STORAGE_DRIVER_NOW="$(env_get "$ENV_FILE" REMOTE_STORAGE_DRIVER)"
if [ "$REMOTE_STORAGE_DRIVER_NOW" = "aws-s3" ]; then
    ENVRC="$INSTANCE_DIR/.envrc"
    [ -f "$ENVRC" ] && { # shellcheck disable=SC1090
        source "$ENVRC"; }
    AWS_PROFILE="${AWS_PROFILE:-${AWS_PROFILE_DEFAULT:-default}}"
fi
