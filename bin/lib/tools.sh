# Command-line tools the platform installs for you into ~/.local/bin, only
# when a flow needs them: mkcert (setup's TLS step), the AWS CLI and direnv
# (S3 media storage). Sourced, never run. The installers use log/fail from
# bin/lib/ui.sh; machine_arch and local_bin_on_path need nothing.

# amd64 or arm64 for this machine (blank if it's neither).
machine_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
    esac
}

# Makes ~/.local/bin exist and be on PATH — now, and in new shells (~/.bashrc).
local_bin_on_path() {
    mkdir -p "$HOME/.local/bin"
    # shellcheck disable=SC2016  # written to .bashrc unexpanded on purpose
    grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null \
        || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
}

# verify_sha256 FILE SHA256 — the download is exactly the release file we
# pinned; otherwise it is deleted and the install stops.
verify_sha256() {
    local got
    got="$(sha256sum "$1" 2>/dev/null | cut -d' ' -f1)"
    [ -n "$got" ] && [ "$got" = "$2" ] && return 0
    rm -f "$1"
    fail "$(basename "$1") doesn't match its pinned checksum — not installing it. (Download damaged, or the release changed.)"
}

# Pinned releases: the version and the SHA-256 of each architecture's binary
# (mkcert: hashed from its GitHub release; direnv: GitHub's published asset
# digests). Bump the version and both hashes together.
MKCERT_VERSION=v1.4.4
MKCERT_SHA256_amd64=6d31c65b03972c6dc4a14ab429f2928300518b26503f58723e532d1b0a3bbb52
MKCERT_SHA256_arm64=b98f2cc69fd9147fe4d405d859c57504571adec0d3611c3eefd04107c7ac00d0
DIRENV_VERSION=v2.38.1
DIRENV_SHA256_amd64=a19470b6d93afeb226b89b1dabf95a5e559533905122be235ed35408e9983b3c
DIRENV_SHA256_arm64=a5deedd4165c94e629a4f3faf74fc0425d3c34a2fcee438a95d628a5f41f5bd8

# The AWS CLI team's signing key (bin/lib/keys/aws-cli.asc). The CLI itself is
# always "latest", so its signature is checked instead of a pinned hash.
AWS_CLI_KEY_FINGERPRINT=FB5DB77FD5C118B80511ADA8A6310ACC4672475C

# require_installed CMD — fails when an install finished but CMD still isn't found.
require_installed() {
    command -v "$1" >/dev/null 2>&1 \
        || fail "$1 install finished but it's still not on PATH — check ~/.local/bin/$1 exists and ~/.local/bin is in PATH."
}

install_mkcert() {
    local arch tmp
    arch="$(machine_arch)"
    [ -n "$arch" ] || fail "No mkcert build for $(uname -m) — install mkcert yourself (https://github.com/FiloSottile/mkcert)."
    log "'mkcert' not found — installing it into ~/.local/bin ..."
    local_bin_on_path
    tmp="$HOME/.local/bin/mkcert.tmp"
    curl -fsSL "https://github.com/FiloSottile/mkcert/releases/download/$MKCERT_VERSION/mkcert-$MKCERT_VERSION-linux-$arch" -o "$tmp" \
        || { rm -f "$tmp"; fail "Couldn't download mkcert."; }
    local want="MKCERT_SHA256_$arch"
    verify_sha256 "$tmp" "${!want}"
    install -m 755 "$tmp" "$HOME/.local/bin/mkcert"
    rm -f "$tmp"
    require_installed mkcert
    log "Installed mkcert ($(mkcert -version 2>&1))."
}

# Only ever called from the aws-s3 storage branch: S3 is an optional Magento
# storage capability, not a prerequisite for setup in general.
install_aws_cli() {
    local machine tmp gnupg zip
    case "$(machine_arch)" in
        amd64) machine=x86_64 ;;
        arm64) machine=aarch64 ;;
        *) fail "No AWS CLI build for $(uname -m) — install it yourself (https://aws.amazon.com/cli/)." ;;
    esac
    command -v gpg >/dev/null 2>&1 \
        || fail "Installing the AWS CLI needs gpg to check AWS's signature (sudo apt install gnupg) — or install the AWS CLI yourself."
    log "'aws' not found — installing AWS CLI v2 into ~/.local ..."
    tmp="$(mktemp -d)"
    zip="$tmp/awscliv2.zip"
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$machine.zip" -o "$zip" \
        && curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$machine.zip.sig" -o "$zip.sig" \
        || { rm -rf "$tmp"; fail "Couldn't download the AWS CLI."; }
    # Signed by the AWS CLI team's key (bin/lib/keys/aws-cli.asc, as published
    # in AWS's install guide); checked in a throwaway keyring.
    gnupg="$tmp/gnupg"
    mkdir -m 700 "$gnupg"
    gpg --homedir "$gnupg" --batch --quiet --import "$(dirname "${BASH_SOURCE[0]}")/keys/aws-cli.asc" 2>/dev/null \
        && gpg --homedir "$gnupg" --batch --status-fd 1 --verify "$zip.sig" "$zip" 2>/dev/null \
            | grep "^\[GNUPG:\] VALIDSIG $AWS_CLI_KEY_FINGERPRINT " >/dev/null \
        || { rm -rf "$tmp"; fail "The AWS CLI download isn't signed by AWS's key — not installing it."; }
    python3 -m zipfile -e "$zip" "$tmp"
    chmod +x "$tmp/aws/install" "$tmp/aws/dist/aws" "$tmp/aws/dist/aws_completer"
    "$tmp/aws/install" --install-dir "$HOME/.local/aws-cli" --bin-dir "$HOME/.local/bin" --update
    rm -rf "$tmp"
    local_bin_on_path
    require_installed aws
    log "Installed $(aws --version 2>&1)."
}

# Only called when the aws-s3 branch creates .envrc for the first time
# (direnv/.envrc exist purely to export AWS_PROFILE for later manual runs).
install_direnv() {
    local arch kernel url tmp want
    arch="$(machine_arch)"
    [ -n "$arch" ] || fail "Unsupported architecture for direnv auto-install ($(uname -m)) — install it manually, see https://direnv.net/docs/installation.html."
    kernel="$(uname -s | tr '[:upper:]' '[:lower:]')"
    log "'direnv' not found — installing it into ~/.local/bin ..."
    local_bin_on_path
    [ "$kernel" = linux ] || fail "direnv auto-install only covers Linux — install it yourself, see https://direnv.net/docs/installation.html."
    url="https://github.com/direnv/direnv/releases/download/$DIRENV_VERSION/direnv.linux-$arch"
    tmp="$HOME/.local/bin/direnv.tmp"
    curl -fsSL -o "$tmp" "$url" || { rm -f "$tmp"; fail "Couldn't download direnv."; }
    want="DIRENV_SHA256_$arch"
    verify_sha256 "$tmp" "${!want}"
    install -m 755 "$tmp" "$HOME/.local/bin/direnv"
    rm -f "$tmp"
    # shellcheck disable=SC2016
    grep -qxF 'eval "$(direnv hook bash)"' "$HOME/.bashrc" 2>/dev/null \
        || echo 'eval "$(direnv hook bash)"' >> "$HOME/.bashrc"
    require_installed direnv
    log "Installed direnv ($(direnv version 2>&1))."
}
