#!/usr/bin/env bash
# tests/e2e-hwsim/build-helper.sh
#
# Build the gatepath-netns-helper binary for the mac80211_hwsim harness.
#
# Run this as your NORMAL user (NOT root) — on an immutable / read-only-/usr
# host the toolchain lives in your $HOME (~/.cargo via rustup), and building as
# root would put it under /root instead. run.sh then launches the binary as
# root; only the build is unprivileged.
#
#   bash tests/e2e-hwsim/build-helper.sh
#
# What it does (idempotent):
#   1. Ensure a Rust toolchain is available (rustup into ~/.cargo if missing).
#   2. Build with GATEPATH_PORTAL_RUNNER_PATH baked to the harness install path
#      (RUNNER_INSTALL_PATH in lib.sh) so the helper looks for the runner under
#      /var/lib, not the read-only /usr default.
#   3. Print the resulting binary path for run.sh to consume.

set -u
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

hdr "build-helper — gatepath-netns-helper for the hwsim harness"

if [ "$(id -u)" -eq 0 ]; then
  warn "Running as root. rustup/cargo will install under /root and the build"
  warn "artifacts will be root-owned. Prefer running this as your normal user."
fi

# ── 1. Toolchain ─────────────────────────────────────────────────────────
# Pull in a rustup-managed cargo if one is on PATH or already in ~/.cargo.
if ! have cargo && [ -f "$HOME/.cargo/env" ]; then
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

if ! have cargo; then
  warn "no cargo on PATH and none in ~/.cargo"
  if ! have curl; then
    die "curl is required to bootstrap rustup but is not installed"
  fi
  log "installing a minimal Rust toolchain via rustup into ~/.cargo ..."
  log "(userspace only — nothing is written to the read-only system image)"
  if ! curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs \
       | sh -s -- -y --profile minimal --default-toolchain stable; then
    die "rustup bootstrap failed"
  fi
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
fi

have cargo || die "cargo still not available after toolchain setup"
ok "cargo: $(command -v cargo) ($(cargo --version 2>/dev/null))"

# ── 2. Build ─────────────────────────────────────────────────────────────
log "building release helper with runner path baked to:"
log "  $RUNNER_INSTALL_PATH"
log "(default-preserving: a normal packaging build omits this env var)"

# The crate's tests pin the DEFAULT path, so we only override the compile-time
# constant for THIS build; the source is unchanged. See spawn.rs PORTAL_RUNNER_PATH.
if ! ( cd "$CRATE_DIR" && \
       GATEPATH_PORTAL_RUNNER_PATH="$RUNNER_INSTALL_PATH" \
       cargo build --release --bin gatepath-netns-helper ); then
  die "cargo build failed"
fi

[ -x "$HELPER_BIN" ] || die "build reported success but $HELPER_BIN is missing"

ok "helper built: $HELPER_BIN"
hdr "next"
cat <<EOF
  Run the harness as root:

    sudo bash tests/e2e-hwsim/run.sh            # default: static DHCP, headless
    sudo bash tests/e2e-hwsim/run.sh --help     # all flags

  The binary embeds the runner path $RUNNER_INSTALL_PATH; run.sh installs the
  runner there before launching the helper, so they must agree. If you edit
  lib.sh's RUNNER_INSTALL_PATH, re-run this script.
EOF
