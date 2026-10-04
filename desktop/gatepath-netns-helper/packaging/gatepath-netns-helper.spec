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

# Version is supplied by build-rpm.sh (`--define "version <x>"`, read from the
# committed Cargo.toml) so it can't drift from the crate. Fallback keeps a direct
# `rpmbuild` invocation working; bump it if you build the spec by hand.
%{!?version: %global version 1.1.0}

Name:           gatepath-netns-helper
Version:        %{version}
Release:        2%{?dist}
Summary:        Gatepath desktop app with native network-namespace isolation

License:        GPL-3.0-or-later
URL:            https://github.com/bearyjd/gatepath
Source0:        %{name}-%{version}.tar.gz

ExclusiveArch:  x86_64 aarch64

BuildRequires:  rust
BuildRequires:  cargo
BuildRequires:  systemd-rpm-macros
BuildRequires:  python3-devel
BuildRequires:  python3-pip
BuildRequires:  python3-setuptools
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros

# Core captive-portal bring-up (SetupCaptive → the DESK-002 in-netns connectivity
# path) execs these; without them SetupCaptive fails at the connectivity step.
Requires:       iproute
Requires:       iw
Requires:       wpa_supplicant
# The shipped service uses the helper's default client, dhclient. Alternate
# providers do not satisfy that default without an explicit administrator override.
Requires:       dhcp-client
# Both the desktop UI and host portal runner require the GTK4 stack. WebKit2
# 4.1 uses GTK3 and cannot satisfy this app's GTK4 requirement.
Requires:       python3-gobject
Requires:       python3-dasbus >= 1.7
Requires:       gtk4
Requires:       libadwaita
Requires:       webkitgtk6.0
Requires:       NetworkManager
Requires:       systemd
Requires:       polkit
Requires:       logrotate

%description
gatepath-netns-helper is the root-privileged D-Bus daemon behind Gatepath's
Linux desktop captive-portal isolation. When the unprivileged GTK app detects a
captive network, it asks this helper (over the system bus, PolicyKit-authorized)
to move the Wi-Fi interface into a dedicated network namespace, bring
connectivity up inside it, and launch the sign-in WebView confined to that
namespace — so the captive-portal negotiation cannot see or leak the user's
normal traffic or VPN.

This package includes the desktop Python app, application launcher, and native
portal runner, together with their required GTK/WebKit dependencies. No separate
pip installation or Flatpak is needed. The historical package name is retained
so existing helper installations upgrade to the complete desktop installation.

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
pushd desktop-app
%pyproject_wheel
popd

%install
pushd desktop-app
%pyproject_install
popd
install -Dm0644 desktop-app/com.ventouxlabs.Gatepath.desktop \
  %{buildroot}%{_datadir}/applications/com.ventouxlabs.Gatepath.desktop
install -Dm0644 desktop-app/com.ventouxlabs.Gatepath.svg \
  %{buildroot}%{_datadir}/icons/hicolor/scalable/apps/com.ventouxlabs.Gatepath.svg
install -Dm0644 desktop-app/com.ventouxlabs.Gatepath.metainfo.xml \
  %{buildroot}%{_datadir}/metainfo/com.ventouxlabs.Gatepath.metainfo.xml
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
%{_bindir}/gatepath
%{python3_sitelib}/gatepath/
%{python3_sitelib}/gatepath-*.dist-info/
%{_datadir}/applications/com.ventouxlabs.Gatepath.desktop
%{_datadir}/icons/hicolor/scalable/apps/com.ventouxlabs.Gatepath.svg
%{_datadir}/metainfo/com.ventouxlabs.Gatepath.metainfo.xml
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
* Sun Oct 04 2026 Gatepath Contributors - 1.1.0-2
- Include the desktop app, nested UI modules, launcher, and native runner.
- Require the GTK4/WebKit6 runtime and correct the Fedora iproute dependency.

* Thu Oct 01 2026 Gatepath Contributors - 1.1.0-1
- Align helper package metadata with the Gatepath 1.1.0 release.

* Fri Jul 24 2026 Gatepath Contributors - 0.1.0-1
- Initial RPM packaging (ROADMAP P2.1): conventional/signable alternative to the
  systemd-sysext image for traditional Fedora/RHEL. Installs the helper, D-Bus
  activation + policy, PolicyKit action, systemd unit, tmpfiles state dir, and
  logrotate config to canonical /usr and /etc paths.
