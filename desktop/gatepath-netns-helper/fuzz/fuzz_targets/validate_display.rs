#![no_main]
//! Fuzz `validate_display` (DESK-004 display-env boundary, `DISPLAY`).
//!
//! Properties: never panics; anything accepted is empty (= unset) or contains a
//! `:`, is within the length bound, control-byte-free, and drawn only from the
//! `DISPLAY` charset `[alnum . : _ -]`. Bound + charset mirror the validator's
//! documented contract; the exhaustive check is the in-crate proptest.

use libfuzzer_sys::fuzz_target;

use gatepath_netns_helper::spawn::validate_display;

/// Mirrors the validator's private `MAX_DISPLAY_ENV_LEN`.
const MAX_DISPLAY_ENV_LEN: usize = 256;

fuzz_target!(|data: &[u8]| {
    let raw = String::from_utf8_lossy(data);

    if validate_display(&raw).is_ok() {
        assert!(
            raw.is_empty()
                || (raw.contains(':')
                    && raw.len() <= MAX_DISPLAY_ENV_LEN
                    && !raw.bytes().any(|b| b < 0x20 || b == 0x7F)
                    && raw
                        .chars()
                        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | ':' | '_' | '-'))),
            "accepted a malformed DISPLAY: {raw:?}"
        );
    }
});
