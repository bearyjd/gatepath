#![no_main]
//! Fuzz `validate_xauthority` (DESK-004 display-env boundary, `XAUTHORITY`).
//!
//! Properties: never panics; anything accepted is empty (= unset) or is an
//! absolute path with no `..` segment, within the length bound, control-byte-
//! free, and drawn only from the path charset `[alnum . _ - /]`. Bound + charset
//! mirror the validator's documented contract; the exhaustive per-rule check
//! (incl. the `..`-traversal rejection) is the in-crate proptest — this target
//! adds coverage-guided panic discovery and a stable acceptance oracle.

use libfuzzer_sys::fuzz_target;

use gatepath_netns_helper::spawn::validate_xauthority;

/// Mirrors the validator's private `MAX_DISPLAY_ENV_LEN`.
const MAX_DISPLAY_ENV_LEN: usize = 256;

fuzz_target!(|data: &[u8]| {
    let raw = String::from_utf8_lossy(data);

    if validate_xauthority(&raw).is_ok() {
        assert!(
            raw.is_empty()
                || (raw.starts_with('/')
                    && !raw.split('/').any(|seg| seg == "..")
                    && raw.len() <= MAX_DISPLAY_ENV_LEN
                    && !raw.bytes().any(|b| b < 0x20 || b == 0x7F)
                    && raw
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '/'))),
            "accepted a malformed XAUTHORITY: {raw:?}"
        );
    }
});
