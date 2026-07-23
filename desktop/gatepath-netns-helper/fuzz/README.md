# Fuzz targets — privileged-boundary validators (ROADMAP P1.2)

Coverage-guided [libFuzzer](https://llvm.org/docs/LibFuzzer.html) targets, via
[`cargo-fuzz`](https://github.com/rust-fuzz/cargo-fuzz), for the five input
validators that make up the helper's privileged trust boundary:

| Target | Validator | Boundary it guards |
|---|---|---|
| `validate_interface_name` | `validation::validate_interface_name` | no VPN/tunnel/bridge/loopback iface is moved into the netns (WiFi-only) |
| `validate_portal_url` | `spawn::validate_portal_url` | only `http(s)` URLs reach the portal WebView runner |
| `validate_wayland_display` | `spawn::validate_wayland_display` | `WAYLAND_DISPLAY` charset/length |
| `validate_display` | `spawn::validate_display` | `DISPLAY` charset/length + must contain `:` |
| `validate_xauthority` | `spawn::validate_xauthority` | `XAUTHORITY` absolute, no `..`, charset/length |

Each target asserts two properties over arbitrary input: the validator **never
panics** (libFuzzer flags any panic/abort as a crash), and **anything it accepts
is provably safe** (the acceptance oracle in each target). These complement — do
not replace — the exhaustive `proptest` suites that live in-crate next to each
validator (`validation::proptests`, `spawn::validator_proptests`), which run in
the normal `cargo test` CI. Fuzzing adds coverage-guided exploration (notably of
`url::Url::parse` in the portal-URL target) that regex-based generators reach
less deeply.

## Why this is not in CI

`cargo-fuzz` needs a **nightly** toolchain and a meaningful run needs wall-clock
time, so — per ROADMAP P1.2 — this is a deliberate **out-of-CI** tool, not a PR
gate. The in-CI guard for these validators is the `proptest` suite. This crate is
its own workspace (see the empty `[workspace]` in `Cargo.toml`), so the parent's
`cargo build` / `test` / `clippy` / `fmt` never descend into it.

## Running

```bash
cd desktop/gatepath-netns-helper

# List targets
cargo +nightly fuzz list

# Build all targets (sanity: they compile + link libFuzzer)
cargo +nightly fuzz build

# Fuzz one target (Ctrl-C to stop; runs until a crash or forever)
cargo +nightly fuzz run validate_portal_url

# Time-boxed run (e.g. a 5-minute soak per target in a nightly job)
cargo +nightly fuzz run validate_portal_url -- -max_total_time=300
```

A crash writes a reproducer to `fuzz/artifacts/<target>/`; replay it with
`cargo +nightly fuzz run <target> fuzz/artifacts/<target>/<crash-file>`. The
`corpus/`, `artifacts/`, and `target/` directories are gitignored.
