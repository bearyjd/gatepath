#![no_main]
//! Fuzz `validate_portal_url` — the scheme-allowlist boundary on the URL the
//! helper hands to the portal WebView runner.
//!
//! This is the highest-value target: the validator delegates to `url::Url::parse`
//! (a third-party RFC 3986 parser with a large state space), so coverage-guided
//! fuzzing explores parser paths the regex-based proptest generators reach less
//! deeply. Properties:
//!   1. Never panics.
//!   2. Anything accepted is ≤ 4096 bytes, control-byte-free, parses, and is
//!      `http`/`https` only — all re-checkable through the public `url` crate,
//!      so no private constants are duplicated.

use libfuzzer_sys::fuzz_target;

use gatepath_netns_helper::spawn::validate_portal_url;

fuzz_target!(|data: &[u8]| {
    let raw = String::from_utf8_lossy(data);

    if validate_portal_url(&raw).is_ok() {
        assert!(raw.len() <= 4096, "accepted an over-length URL");
        assert!(
            !raw.bytes().any(|b| b < 0x20 || b == 0x7F),
            "accepted a URL with a raw control byte: {raw:?}"
        );
        let parsed = url::Url::parse(&raw).expect("accepted a URL that does not parse");
        assert!(
            matches!(parsed.scheme(), "http" | "https"),
            "accepted a non-http(s) scheme: {:?}",
            parsed.scheme()
        );
    }
});
