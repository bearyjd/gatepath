# syntax=docker/dockerfile:1
#
# gatepath-client — Fedora image with the full desktop runtime + the Rust
# helper crate built and installed at the FHS paths the helper's own D-Bus
# service file and systemd unit expect. Designed to come up behind the
# captive-gateway and exercise the end-to-end engage/wait/disengage flow
# the way a user would on a real captive WiFi.
#
# Two unusual layers in here:
#  1. python-dbusmock provides org.freedesktop.NetworkManager on the system
#     bus. Both the Python NMCaptiveInterfaceLookup and the Rust helper's
#     NMCaptiveCheck talk to whoever owns that bus name; the dbusmock NM
#     reports a single wifi device (wlan0) flagged NM_CONNECTIVITY_PORTAL.
#  2. /usr/lib/gatepath/portal-webview-runner is replaced with a test-mode
#     wrapper that runs udhcpc on the moved interface BEFORE exec'ing the
#     real Python runner. Distros own this wrapper anyway (the helper's
#     PORTAL_RUNNER_PATH is compile-time hardcoded to the install path),
#     so substituting here is honest.
# Fedora 42 (vs 41) — needed for rustc ≥ 1.85 since the helper crate's
# Cargo.toml is edition 2024. Also picks up the post-X11-split package
# names without the umbrella xorg-x11-* meta-packages.
FROM fedora:42

# Runtime + build deps. Splitting the layer aggressively keeps cache wins
# per category if any single thing churns.
RUN dnf install -y --setopt=install_weak_deps=False \
        # GTK4 / WebKit / libadwaita stack
        gtk4 libadwaita webkit2gtk4.1 \
        # Python runtime + bindings (PyGObject, dasbus) + dbusmock + the
        # original `python3-dbus` low-level binding the dbusmock seed
        # script also touches
        python3 python3-pip python3-gobject python3-dasbus python3-dbusmock \
        python3-dbus \
        # System D-Bus + polkit (dbus pulls in dbus-daemon)
        dbus polkit polkit-libs \
        # Network plumbing — iproute2 for `ip netns/link`; setcap ships
        # with libcap which is already in the base image; busybox stays
        # around for a future real-DHCP wrapper if we want one.
        iproute busybox \
        # Headless display — Xvfb plus scrot for screen capture. We use
        # scrot rather than xwd because the post-X11-split Fedora packages
        # ship the screenshot tools separately and scrot is the smallest
        # one that writes PNG out of the box.
        xorg-x11-server-Xvfb xorg-x11-xauth scrot \
        # Misc test conveniences (ca-certificates and curl are in the base)
        jq procps-ng tini util-linux \
        # Rust toolchain + headers for building the helper crate
        cargo rust gcc pkgconf-pkg-config openssl-devel dbus-devel \
    && dnf clean all

# Build the helper crate.
#
# Cache strategy: stage 1 copies only Cargo.{toml,lock} + stubs at the
# exact paths the manifest declares (lib at src/lib.rs, bin at
# src/bin/gatepath-netns-helper.rs). Cargo downloads + compiles the full
# dependency graph during stage 1, then stage 2 OVERWRITES the stubs
# with the real source via a directed `COPY ./src ./src` — no
# `rm -rf src` step (that was the source of the previous breakage; if
# the stage-2 COPY pulled in a stale local `target/` from the host's
# dev cargo build, removing src and re-copying merged stale artifacts
# with fresh ones). The repo-root .dockerignore keeps `target/` out of
# every build context, so that hazard is closed for good.
WORKDIR /src/helper
COPY desktop/gatepath-netns-helper/Cargo.toml \
     desktop/gatepath-netns-helper/Cargo.lock \
     ./
RUN mkdir -p src/bin \
    && echo 'fn main() {}'      > src/bin/gatepath-netns-helper.rs \
    && echo '// dep-cache stub' > src/lib.rs \
    && cargo build --release --bin gatepath-netns-helper

# Real source on top of the stubs. Two correctness gotchas here:
#  1. Docker's COPY preserves the host's source mtimes, which can be
#     older than the dep-warmup's compiled artifacts — cargo then
#     fingerprints them as "unchanged" and skips the rebuild, leaving
#     us with the stub binary. `touch` forces fresh mtimes.
#  2. Docker's BUILD cache, in turn, can reuse the cached output of the
#     RUN that did the `touch` (the command text is constant). The
#     HELPER_BUILD_CACHEBUST ARG ties the layer's cache key to a
#     timestamp the host orchestrator can flip, so a forced rebuild
#     actually rebuilds. The default keeps day-to-day caching intact.
ARG HELPER_BUILD_CACHEBUST=2026-05-12-2
COPY desktop/gatepath-netns-helper/src/  ./src/
COPY desktop/gatepath-netns-helper/data/ ./data/
RUN echo "cachebust=$HELPER_BUILD_CACHEBUST" \
    && touch src/lib.rs src/bin/gatepath-netns-helper.rs \
    && rm -f target/release/gatepath-netns-helper \
              target/release/deps/gatepath_netns_helper* \
    && cargo build --release --bin gatepath-netns-helper \
    && ls -la /src/helper/target/release/gatepath-netns-helper

# Install the helper at the paths the service file + systemd unit hardcode.
RUN install -Dm0755 /src/helper/target/release/gatepath-netns-helper \
        /usr/libexec/gatepath-netns-helper \
    && install -Dm0644 /src/helper/data/com.ventouxlabs.Gatepath.NetNsHelper.conf \
        /etc/dbus-1/system.d/com.ventouxlabs.Gatepath.NetNsHelper.conf \
    && install -Dm0644 /src/helper/data/com.ventouxlabs.Gatepath.NetNsHelper.service \
        /usr/share/dbus-1/system-services/com.ventouxlabs.Gatepath.NetNsHelper.service \
    && install -Dm0644 /src/helper/data/com.ventouxlabs.Gatepath.NetNsHelper.policy \
        /usr/share/polkit-1/actions/com.ventouxlabs.Gatepath.NetNsHelper.policy \
    # State dir for the helper's audit log (StateDirectory= equivalent —
    # systemd would mkdir this; we do it manually since we run the helper
    # via D-Bus activation, not systemd, inside the container).
    && install -d -m 0750 /var/lib/gatepath \
    && install -d -m 0755 /var/run/netns \
    && rm -rf /src/helper

# Polkit override — auto-YES for both helper actions. The shipped .policy
# defaults to auth_admin_keep (real-user-prompt UX); for E2E we want no
# prompt at all. This file is dropped at install time as a test-only
# override — production systems never see it.
COPY tests/e2e-docker/client/polkit-test.rules \
        /etc/polkit-1/rules.d/50-gatepath-e2e.rules

# Test-mode portal-webview-runner wrapper. Replaces the shipped wrapper at
# the EXACT path the helper's PORTAL_RUNNER_PATH compile-time constant
# points at (/usr/lib/gatepath/portal-webview-runner). After setns +
# setresuid drops us into the gatepath netns as the caller UID with no IP
# configured on wlan0, this wrapper assigns a static lease then exec's the
# real Python runner.
COPY tests/e2e-docker/client/portal-webview-runner.test \
        /usr/lib/gatepath/portal-webview-runner
RUN chmod 0755 /usr/lib/gatepath/portal-webview-runner \
    # File caps so the unprivileged caller UID (post-setresuid) can still
    # bring the moved interface up and assign an address. Equivalent to
    # the suid-bit trick distros use on dhclient/udhcpc, but capability-
    # bounded to net_admin only.
    && setcap cap_net_admin+ep /usr/sbin/ip

# Allow root (i.e. dbus-daemon's child processes) to OWN
# org.freedesktop.NetworkManager on the system bus. Stock dbus policy
# doesn't list this name, so dbusmock can't claim it without an explicit
# rule. Test-scope only — not installed on real systems.
COPY tests/e2e-docker/client/dbusmock-nm.conf \
        /etc/dbus-1/system.d/dbusmock-nm.conf

# Install the desktop Python package editable so any source change picked
# up by the bind mount in compose.yml takes effect without a rebuild.
COPY desktop /app/desktop
RUN pip3 install --no-cache-dir -e '/app/desktop[gui]'

# dbusmock NetworkManager harness + entrypoint + driver hooks.
COPY tests/e2e-docker/client/dbusmock_nm.py     /opt/e2e/dbusmock_nm.py
COPY tests/e2e-docker/client/entrypoint.sh      /entrypoint.sh
COPY tests/e2e-docker/client/run-scenario.py    /opt/e2e/run-scenario.py
RUN chmod +x /entrypoint.sh /opt/e2e/run-scenario.py

# A non-root caller UID for the desktop app. We map host UID via podman's
# userns-remap; inside the container the app runs as 'tester' (UID 1000)
# and the helper runs as root via D-Bus activation.
RUN useradd --uid 1000 --create-home --shell /bin/bash tester \
    && install -d -m 0750 -o tester -g tester /home/tester/.config /home/tester/.local/share

ENV PYTHONUNBUFFERED=1 \
    HOME=/home/tester \
    XDG_RUNTIME_DIR=/run/user/1000

ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
CMD ["wait"]
