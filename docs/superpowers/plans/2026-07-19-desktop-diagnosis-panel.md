# PR 4 — Desktop GTK Diagnosis Panel + wiring

**Date:** 2026-07-19
**Spec:** `docs/superpowers/specs/2026-07-18-diagnostics-expansion-design.md` (§Desktop/UI, PR sequencing #4)
**Stacks on:** #82 (desktop `diag/` package, merged `687396b`)
**Scope decision (owner, this session):** *Panel + manual run + VPN banner.*
Do **not** stand up the live NM monitor→`open_portal` auto-trigger — that gap
predates diagnostics and gets its own PR. This PR makes the battery + VPN
detection reachable and manually runnable, and updates the security doc.

## Why this PR exists (state of the desktop app today)

Three confirmed dead ends this PR closes:

1. `diag_context.default_engine()` — the 8-probe battery — has **zero**
   production callers.
2. `vpn_detector` has zero production callers; `window.py:121
   show_vpn_warning` is defined but never invoked.
3. `docs/SECURITY_MODEL.md`'s "What Gatepath itself sends" section says the
   diagnostic battery is *"Android only today; the desktop mirror is planned"* —
   which stops being true the moment this PR makes the desktop battery
   reachable by a real user (including its DoH query to `1.1.1.1`).

Out of scope (pre-existing, tracked separately): `app.py` never starts a
monitor and never calls `window.open_portal`; `start_nm_monitor()` has no
caller and its NM path is an explicit MVP stub. The panel is therefore wired
to be **manually runnable at any time** and to render on `open_portal` *when
that path eventually fires* — it does not itself create the portal-suspected
signal.

## Deliberate divergences from the design spec (do not "fix" silently)

- **No `.blp` file.** The spec names `ui/diagnosis_panel.blp` +
  `diagnosis_panel.py`. But `gatepath/ui/*.blp` are **vestigial** — nothing in
  the codebase loads them (no `Gtk.Builder`, no `Template`, no gresource in the
  build). `window.py._build_ui` constructs every widget programmatically. The
  panel follows that real pattern (programmatic GTK), not the spec's letter.
  Record this in the panel module docstring.
- **Threading, not asyncio.** Worker thread + `GLib.idle_add`, matching
  `window.py:192-206` (`_wait_for_subprocess_thread` → `idle_add`). No asyncio
  anywhere in desktop.

## Interface-name source for a manual run

`diag_context.build_probe_context(interface_name, …)` needs an interface name.
Every desktop probe uses the *system default route* (system `getaddrinfo`,
unbound `http_fetcher.fetch`); `interface_name` is a **label only** (it lands
in `VpnBlocking.interface_name` and the context), not a bind target. So a
best-effort label is correct and sufficient:

- Prefer `captive_interface_lookup.get_captive_interface()` when the window was
  built with one and it yields a name.
- Else fall back to a stable placeholder label (e.g. `"(default route)"`).
  Do **not** invent NM/route-table probing here — out of scope, and the label
  is cosmetic.

## Tasks (each = one implementer subagent, then an independent reviewer on its diff)

### Task 1 — `gatepath/ui/diagnosis_panel.py`: render a `DiagnosisResult`
Pure presentation. Input: a `DiagnosisResult` (from
`gatepath.diag.engine`). Output: a GTK widget tree, built programmatically.
- "Most likely cause" headline derived from `result.top.cause` (human label
  per cause) + `result.recommended.instruction` when present.
- Collapsible "All checks" section: one row per `result.checks` entry
  (`probe_name`, pass/fail/inconclusive status derived from
  `check.report.cause`, a one-line detail). **Render in the engine's given
  order — never re-rank** (engine `_RANK` is the sole ranker; the panel must
  not reorder `checks`).
- Use `Adw.PreferencesGroup` + `Adw.ExpanderRow`/`Adw.ActionRow` (libadwaita
  is already the UI toolkit here).
- Guarded GTK import + non-GTK stub, mirroring `window.py`'s
  `try/except (ImportError, ValueError, AttributeError)` tail so the module
  stays importable without PyGObject.
- **Never crash on a report shape:** an `Inconclusive` row shows its
  `probe_errors`; an unknown/`Healthy` top shows a benign "no problem found"
  headline. All-green ⇒ healthy headline, no recommended action.

### Task 2 — `gatepath/diagnosis_runner.py`: run the battery off the main loop
A small threading seam, outside `gatepath.diag` (it performs I/O + touches
GTK), mirroring `diag_context`'s "I/O lives outside the pure package" rule.
- `run_diagnostics_async(interface_name, on_result, *, engine_factory=default_engine, context_builder=build_probe_context)`:
  spawn a daemon worker that builds the context, runs the engine, and bounces
  the `DiagnosisResult` back via `GLib.idle_add(on_result, result)`.
- Any exception in the worker becomes a synthetic `DiagnosisResult` whose top
  is `Inconclusive` (never a crash, never a silent drop) — surfaced on the GTK
  thread the same way.
- Injectable `engine_factory`/`context_builder` so it's unit-testable without
  GTK or real network (the `GLib.idle_add` call itself is the only GTK touch;
  isolate it behind an injectable dispatcher defaulting to `GLib.idle_add`).

### Task 3 — wire panel + button + VPN banner into `window.py`
- Add a "Run diagnostics" button to the monitoring `StatusPage` (always
  available). Clicking it: disable the button, call
  `run_diagnostics_async(...)`, and on result re-enable + (re)render the
  diagnosis panel in the window.
- Implement `show_vpn_warning` for real: reveal an `Adw.Banner` with the VPN
  labels (today it only logs). VPN enumeration (`detect_vpn_details`) does
  network I/O (Tailscale localapi) → run it on the worker too, not the main
  loop; or fold VPN status into the diagnosis result (VpnProbe already covers
  it). Prefer folding: the battery's `VpnProbe` already emits `VpnBlocking`, so
  the banner can be driven from the `DiagnosisResult` rather than a second
  independent VPN call. Keep `show_vpn_warning`'s signature but source its
  input from the result path.
- Resolve `interface_name` per the "Interface-name source" section above.

### Task 4 — `docs/SECURITY_MODEL.md`: cover desktop outbound diagnostics
- Update the "What Gatepath itself sends" section: the desktop diagnostic
  battery is now reachable by a real user. Document its outbound traffic —
  the **DoH query to `1.1.1.1`** (IP literal is mandatory; do not soften the
  rationale already recorded there), the HTTP/HTTPS connectivity probes, and
  the bounded redirect follow.
- Correct the "Android only today; desktop mirror is planned" sentence.
- Add/adjust the threat-table row(s) for desktop's outbound diagnostic
  traffic, matching how Android's rows are phrased.
- **Fact-check every prose claim against the code** (this section falsified
  its own code twice already — see handoff blind-spots). No claim ships
  unverified.

### Task 5 — tests
- Panel: render each cause into the widget tree and assert headline/rows
  (skip cleanly if headless GTK is unavailable in the sandbox — check how/if
  any existing GTK test runs; there are currently **no** window unit tests, so
  prefer testing Task 2's pure logic heavily and Task 1 via a thin GTK-present
  guard/`pytest.importorskip("gi")`).
- Runner (Task 2): the injectable seams let this run with no GTK/network —
  assert success path, exception→Inconclusive path, and that the dispatcher is
  invoked with the result.
- `python -m pytest tests/` stays green; respect the `diag/` purity CI job
  (new I/O belongs in `diagnosis_runner.py`/`diag_context.py`, never in
  `diag/`).

## Review gates
Per-task: independent reviewer subagent on that task's diff, fix loop until
clean. Then a **whole-branch** review before opening the PR, and — new, per
handoff blind-spot — an explicit **diff of the change against
`SECURITY_MODEL.md`** (does anything alter what the app sends/binds that the
doc doesn't cover?) and a **prose fact-check** of the Task 4 doc edit against
the sources.

## Definition of done
- Panel renders all 9 cause shapes without crashing; manual "Run diagnostics"
  works off the main loop; VPN banner reveals from the result path.
- `default_engine()` and `vpn_detector` are no longer dead code.
- `SECURITY_MODEL.md` truthfully covers desktop outbound diagnostics.
- `pytest tests/` green; `diag/` purity job green.
- Lands as a reviewed PR (no direct push to `main`).
