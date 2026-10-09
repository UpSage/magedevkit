# Defaults for the S3 media sync (bin/sync-media-to-s3), which sources this
# file. A REMOTE_STORAGE_BUCKET/REGION already exported by the caller wins;
# the bucket otherwise comes from the instance's env file (setup records it).

: "${REMOTE_STORAGE_BUCKET:=}"   # no shared default — set per machine (bin/local/setup asks)
: "${REMOTE_STORAGE_REGION:=us-east-2}"
