# RPM spec for the Gatepath privileged netns helper — the conventional,
# signable alternative to the systemd-sysext image for **traditional (non-atomic)
# Fedora/RHEL** (docs/DESKTOP_NETNS_DEPLOYMENT.md "Option A — Layered RPM").
#
# It installs every file to the SAME canonical /usr paths as packaging/build-sysext.sh
# (so the helper's hardcoded PORTAL_RUNNER_PATH etc. work with no source edits) and
# adds the one thing a sysext cannot: the logrotate config straight into /etc.
#
# Build: run packaging/build-rpm.sh (it stages the source tarball — the crate plus
# the repo-root LICENSE/README/deployment-doc, since this is a monorepo — and runs
# rpmbuild -ba). Needs rustc+cargo+rpmbuild. CI builds it in desktop.yml.
#
# For a real Fedora dist-git submission, regenerate crate BuildRequires with
# rust2rpm and switch %build/%install to the %cargo_* macros; this self-contained
# spec builds straight from the vendored Cargo.lock instead, so it works from the
# repo without that tooling.

# Rust release binaries don't produce a useful rpm debuginfo package without the
# %cargo_* machinery; skip the debuginfo subpackage rather than fail extraction.
%global debug_package %{nil}

Name:           gatepath-netns-helper
Version:        0.1.0
Release:        1%{?dist}
Summary:        Privileged helper for Gatepath's desktop network-namespace isolation

License:        GPL-3.0-or-later
URL:            https://github.com/bearyjd/gatepath
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  x86_64 aarch64

BuildRequires:  rust
BuildRequires:  cargo
BuildRequires:  systemd-rpm-macros

# Core captive-portal bring-up (SetupCaptive → the DESK-002 in-netns connectivity
# path) execs these; without them SetupCaptive fails at the connectivity step.
Requires:       iproute2
Requires:       iw
Requires:       wpa_supplicant
# A DHCP client is required to reacquire an address inside the netns; accept any
# of the common providers.
Requires:       (dhcp-client or dhcpcd or busybox)
# The portal WebView (portal-webview-runner → the Python GTK app) needs these,
# but the helper's netns function does not — so they are weak deps.
Recommends:     python3-gobject
Recommends:     (webkitgtk6.0 or webkit2gtk4.1)

%description
gatepath-netns-helper is the root-privileged D-Bus daemon behind Gatepath's
Linux desktop captive-portal isolation. When the unprivileged GTK app detects a
captive network, it asks this helper (over the system bus, PolicyKit-authorized)
to move the Wi-Fi interface into a dedicated network namespace, bring
connectivity up inside it, and launch the sign-in WebView confined to that
namespace — so the captive-portal negotiation cannot see or leak the user's
normal traffic or VPN.

This package installs the helper to the same canonical /usr paths as the
systemd-sysext image and is the conventional choice for traditional (non-atomic)
Fedora/RHEL. Only open (unsecured) captive networks are supported; see
%{_docdir}/%{name}/DESKTOP_NETNS_DEPLOYMENT.md.

%prep
%autosetup -n %{name}-%{version}

%build
# Build the release binary. --locked pins the committed Cargo.lock (the same one
# cargo-audit scans in CI); --offline is added by callers who pre-vendor.
cargo build --release --locked --manifest-path Cargo.toml

%install
# Mirror packaging/build-sysext.sh's staging exactly, but into %{buildroot} and
# with /etc handled natively (a sysext cannot write /etc).
install -Dm0755 target/release/%{name} \
  %{buildroot}%{_libexecdir}/%{name}
install -Dm0755 data/portal-webview-runner \
  %{buildroot}%{_prefix}/lib/gatepath/portal-webview-runner
install -Dm0644 data/%{name}.service \
  %{buildroot}%{_unitdir}/%{name}.service
install -Dm0644 data/com.ventouxlabs.Gatepath.NetNsHelper.conf \
  %{buildroot}%{_datadir}/dbus-1/system.d/com.ventouxlabs.Gatepath.NetNsHelper.conf
install -Dm0644 data/com.ventouxlabs.Gatepath.NetNsHelper.service \
  %{buildroot}%{_datadir}/dbus-1/system-services/com.ventouxlabs.Gatepath.NetNsHelper.service
install -Dm0644 data/com.ventouxlabs.Gatepath.NetNsHelper.policy \
  %{buildroot}%{_datadir}/polkit-1/actions/com.ventouxlabs.Gatepath.NetNsHelper.policy
install -Dm0644 packaging/tmpfiles.d/gatepath.conf \
  %{buildroot}%{_tmpfilesdir}/gatepath.conf

# Logrotate: install natively into /etc (RPM-owned, %config(noreplace)). Also
# ship the /usr/share/factory/ copy the sysext relies on, so the SAME shipped
# tmpfiles.d `C` line stays a harmless no-op here (its dest already exists) and
# recreates the /etc file if an operator deletes it — no forked tmpfiles file.
install -Dm0644 data/gatepath-helper-audit.logrotate \
  %{buildroot}%{_sysconfdir}/logrotate.d/%{name}
install -Dm0644 data/gatepath-helper-audit.logrotate \
  %{buildroot}%{_datadir}/factory/etc/logrotate.d/%{name}

%files
%license LICENSE
%doc README.md DESKTOP_NETNS_DEPLOYMENT.md
%{_libexecdir}/%{name}
%dir %{_prefix}/lib/gatepath
%{_prefix}/lib/gatepath/portal-webview-runner
%{_unitdir}/%{name}.service
%{_tmpfilesdir}/gatepath.conf
%{_datadir}/dbus-1/system.d/com.ventouxlabs.Gatepath.NetNsHelper.conf
%{_datadir}/dbus-1/system-services/com.ventouxlabs.Gatepath.NetNsHelper.service
%{_datadir}/polkit-1/actions/com.ventouxlabs.Gatepath.NetNsHelper.policy
%{_datadir}/factory/etc/logrotate.d/%{name}
%config(noreplace) %{_sysconfdir}/logrotate.d/%{name}

%post
# Register (not statically enable) the D-Bus-activated unit; create the
# state dir + /etc logrotate copy now, before first start.
%systemd_post %{name}.service
%tmpfiles_create %{_tmpfilesdir}/gatepath.conf

%preun
%systemd_preun %{name}.service

%postun
%systemd_postun_with_restart %{name}.service

%changelog
* Fri Jul 24 2026 Gatepath Contributors - 0.1.0-1
- Initial RPM packaging (ROADMAP P2.1): conventional/signable alternative to the
  systemd-sysext image for traditional Fedora/RHEL. Installs the helper, D-Bus
  activation + policy, PolicyKit action, systemd unit, tmpfiles state dir, and
  logrotate config to canonical /usr and /etc paths.
