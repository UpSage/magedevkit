# Terraform for the pipeline and provision wizards — sourced, never run.
#
# Uses `terraform` from PATH when it's new enough, otherwise downloads a
# pinned release once into ~/.cache/platform-terraform/<version>/ (checked
# against HashiCorp's SHA256SUMS), the way bin/lib/web-wizard/build-css
# fetches Tailwind. Providers are cached in ~/.cache/platform-terraform/plugins
# so each `terraform init` doesn't download the AWS provider again.
#
# Expects the caller to have already defined: log(), fail().

# machine_arch
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/tools.sh"

TERRAFORM_VERSION="${TERRAFORM_VERSION:-1.16.5}"
TERRAFORM_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/platform-terraform"

# Sets TERRAFORM_BIN to a usable terraform binary (downloading it if needed).
# Not meant for $(…): it reports progress and failures through log/fail.
ensure_terraform() {
    local have arch os dir zip base
    if command -v terraform >/dev/null 2>&1; then
        have="$(terraform version -json 2>/dev/null | sed -n 's/.*"terraform_version": *"\([^"]*\)".*/\1/p')"
        if [ -n "$have" ] && version_ge "$have" 1.6.0; then
            TERRAFORM_BIN="$(command -v terraform)"
            return
        fi
    fi
    dir="$TERRAFORM_CACHE/$TERRAFORM_VERSION"
    if [ ! -x "$dir/terraform" ]; then
        arch="$(machine_arch)"
        [ -n "$arch" ] || fail "No Terraform build for $(uname -m) — install terraform yourself."
        os="$(uname -s | tr '[:upper:]' '[:lower:]')"
        zip="terraform_${TERRAFORM_VERSION}_${os}_${arch}.zip"
        base="https://releases.hashicorp.com/terraform/$TERRAFORM_VERSION"
        mkdir -p "$dir"
        log "Downloading Terraform $TERRAFORM_VERSION ..."
        curl -fsSL -o "$dir/$zip" "$base/$zip" || fail "Couldn't download $base/$zip."
        curl -fsSL -o "$dir/SHA256SUMS" "$base/terraform_${TERRAFORM_VERSION}_SHA256SUMS" || fail "Couldn't download Terraform's checksums."
        (cd "$dir" && grep " $zip\$" SHA256SUMS | sha256sum -c --quiet -) || { rm -f "$dir/$zip"; fail "Terraform download didn't match its checksum."; }
        python3 -c 'import sys, zipfile; zipfile.ZipFile(sys.argv[1]).extract("terraform", sys.argv[2])' "$dir/$zip" "$dir" \
            || fail "Couldn't unpack $zip."
        chmod +x "$dir/terraform"
        rm -f "$dir/$zip"
    fi
    TERRAFORM_BIN="$dir/terraform"
}

# terraform with the shared provider cache.
tf() {
    mkdir -p "$TERRAFORM_CACHE/plugins"
    TF_PLUGIN_CACHE_DIR="$TERRAFORM_CACHE/plugins" TF_IN_AUTOMATION=1 "$TERRAFORM_BIN" "$@"
}
