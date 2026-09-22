# Confinement follow-ups, part 2: one owner for the process binding, one owner for incident evidence

Second of two PRs for issue #169 (part 1 is #170). Both problems have the
same root: process-global state (`bindProcessToNetwork`, the incident's
evidence record) is written by several callers with no owner, so the writers
race and the record can describe a different incident than the one on screen.

## Stage 1 — `ProcessBinding` (SDK-free, JVM-tested)

`network/NetworkBinder.kt`: `interface NetworkBinder { fun bind(network: Network?): Boolean; fun current(): Network? }`.
`network/ProcessBinding.kt`: the single owner of the slot.

- **Owners** hold a `Lease` (`acquire(network): Lease?` returns null when the
  bind is refused, e.g. EPERM under a secure VPN). Leases form a stack; the
  slot follows the top. `release(lease)` removes that lease wherever it sits
  and rebinds to the new top, or to null when empty. So the handoff activity
  releasing under `MainActivity`'s live WebView leaves the WebView's binding
  in place, and two owners on the same network no longer collide.
- **Borrowers** (the monitor's probe) use `suspend fun <T> borrow(network, block)`.
  Borrows serialise on a `Mutex`; the slot is the borrow's network for the
  block's duration and is then restored to the top of the owner stack, never
  to a value saved before the block ran. Owner changes during a borrow are
  applied when it ends.
- `releaseAll()` for the whole-app background watchdog and `onTerminate`.
- Tests with a fake binder recording every bind: the reviewers' interleavings
  (A/B concurrent borrows; owner releases mid-borrow; second owner acquires
  while first holds; refused bind yields no lease; releaseAll while borrowing).

## Stage 2 — route every writer through it (CI-compiled)

`network/AndroidNetworkBinder.kt` over `ConnectivityManager`; `AppModule`
provides a `@Singleton ProcessBinding`. `CaptivePortalMonitor` borrows for its
bound probe. `PortalScreen`/`GatepathWebView` take the `ProcessBinding` and
acquire/release in the `DisposableEffect`. `CaptivePortalActivity` acquires in
`onCreate` and releases its lease in `onDestroy` (replacing the compare added
in #168's review). `GatepathApplication`'s watchdog and `onTerminate` call
`releaseAll()`. `SECURITY_MODEL.md`'s binding section describes the owner
instead of four unbind sites.

## Stage 3 — `IncidentTracker` and the typed gaps (SDK-free, JVM-tested)

`session/IncidentTracker.kt`: owns everything scoped to one incident that
`MainViewModel` holds today (`confinement`, `evidence`, `diagnosis`,
`suspectedNetwork`, `lastDiagnostics`), exposed as `StateFlow`s.

- `begin(network, inputs, boundPath, diagnostics): Begun(id, confinement, evidence)`
  classifies and publishes; every later write goes through
  `updateEvidence(id) { }` / `setDiagnosis(id, …)` and is dropped when `id`
  is stale.
- `clearIf(network)` clears only when the network matches the incident's;
  `clear()` is unconditional.
- Adopting a default-route capture sets `probePath = DEFAULT_ROUTE`.
- `NetworkDiagnostics.defaultRouteBypassesCaptive` and
  `ProbeContext.defaultRouteBypassesCaptive` become `Boolean?`; the three
  probes that gate on it decline (inconclusive report) on null instead of
  guessing.
- `ConfinementState.Unknown` gains `reason: UnknownReason { BOUND_VALIDATED, PROBE_ERROR }`
  so the handoff screen can keep the sign-in carve-out for a bind that
  succeeded and returned 204 even under a VPN.
- `IncidentEvidence.portalHost: String?` (from `DnsStrict`, or the Confined
  portal URL's host) so session-less incidents carry the identifier the
  redaction harvest needs; rendered as `portal_host`, scrubbed under `--redact`;
  `IncidentEvidenceTest`'s field-set guard updated deliberately.

## Stage 4 — `MainViewModel` delegates (CI-compiled)

Replace the five fields with the tracker; hold the engine `Job` and cancel it
on a new incident or a rerun; gate `NetworkValidated` / `NetworkObservedNoPortal`
/ `CaptiveNetworkLost` clears on the event's network; key `onCertSummary` and
the engine's evidence writes on the incident id; pass the tri-state through
`rerunDiagnostics`; `CaptivePortalMonitor` emits `null` when the fallback
probe was skipped; `CaptivePortalActivity` keeps the carve-out for
`Unknown(BOUND_VALIDATED)` under a VPN.

## Deferred

Tying the WebView console capture (#160) to an incident id needs the capture
file format to change; separate small PR.
