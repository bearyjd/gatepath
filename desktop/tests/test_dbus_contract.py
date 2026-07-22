"""Python side of the D-Bus contract drift guard (roadmap P1.1).

The privileged Rust helper and this Python client speak one wire protocol.
`docs/netns_helper_dbus_contract.json` is the single checked-in source of
truth for that surface (bus identifiers, method arities + return codes, the
signal payload); the Rust helper is guarded against the SAME artifact in
`cargo test`, and CI runs both via `.github/workflows/dbus-contract-parity.yml`.
This mirrors the audit-log `schema-parity.yml` precedent.

These tests are headless — no bus, no dasbus — so they run in the normal
pytest suite. They pin the STATIC contract (names/arities/shape); live wire
round-trips still need the `#[ignore]`d Rust integration tests. Error NAMES
stay guarded by :py:mod:`test_netns_client` — only the prefix is pinned here.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from gatepath import netns_client
from gatepath.netns_client import HelperProxy, SubprocessExit

# ── contract artifact location ───────────────────────────────────────────
#
# This test lives at `desktop/tests/test_dbus_contract.py`, so parents[2] is
# the repo root (tests → desktop → repo). Mirror `test_netns_client.py`'s
# `Path(__file__).resolve().parents[…]` idiom rather than hard-coding a path.
_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "docs" / "netns_helper_dbus_contract.json"
)

# D-Bus type codes this contract uses: s=string, u=uint32, i=int32. A void
# method return is the empty string. Anything outside this set is a typo or an
# unhandled type the Python client can't carry — fail loudly rather than pass.
_ALLOWED_SIG_CODES = frozenset({"s", "u", "i"})

# The three proxy wrappers the client exposes, keyed by their D-Bus method name.
# Kept explicit (not reflected) so a wrapper silently vanishing from the proxy
# trips the method-set drift test below rather than quietly shrinking coverage.
_CLIENT_METHOD_WRAPPERS = {
    "SetupCaptive": HelperProxy.SetupCaptive,
    "TeardownCaptive": HelperProxy.TeardownCaptive,
    "LaunchPortal": HelperProxy.LaunchPortal,
}


def _load_contract() -> dict:
    assert _CONTRACT_PATH.exists(), (
        f"shared D-Bus contract artifact missing at {_CONTRACT_PATH}. "
        "Both the Rust helper and this Python client are drift-guarded against "
        "it; restore docs/netns_helper_dbus_contract.json."
    )
    return json.loads(_CONTRACT_PATH.read_text(encoding="utf-8"))


_CONTRACT = _load_contract()


def _wrapper_param_names(func) -> list[str]:
    """Positional parameter names of a proxy wrapper, excluding ``self``."""
    params = list(inspect.signature(func).parameters.values())
    return [p.name for p in params if p.name != "self"]


# ── 1. contract is well-formed ───────────────────────────────────────────


def test_contract_has_required_top_level_keys() -> None:
    required = {
        "bus_name",
        "object_path",
        "interface",
        "error_prefix",
        "methods",
        "signals",
    }
    missing = required - _CONTRACT.keys()
    assert not missing, (
        f"D-Bus contract {_CONTRACT_PATH.name} is missing required key(s): "
        f"{sorted(missing)}."
    )
    for key in ("bus_name", "object_path", "interface", "error_prefix"):
        assert isinstance(_CONTRACT[key], str) and _CONTRACT[key], (
            f"contract key {key!r} must be a non-empty string, got {_CONTRACT[key]!r}."
        )
    assert isinstance(_CONTRACT["methods"], dict), "contract 'methods' must be an object."
    assert isinstance(_CONTRACT["signals"], dict), "contract 'signals' must be an object."


def test_contract_methods_are_well_formed() -> None:
    for name, spec in _CONTRACT["methods"].items():
        assert isinstance(spec, dict), f"method {name!r} spec must be an object."
        assert "in" in spec and isinstance(spec["in"], list), (
            f"method {name!r} must declare an 'in' list of arg sig codes."
        )
        assert "out" in spec and isinstance(spec["out"], str), (
            f"method {name!r} must declare an 'out' sig code string."
        )
        for code in spec["in"]:
            assert code in _ALLOWED_SIG_CODES, (
                f"method {name!r} input sig code {code!r} is not one of "
                f"{sorted(_ALLOWED_SIG_CODES)}."
            )
        assert spec["out"] == "" or spec["out"] in _ALLOWED_SIG_CODES, (
            f"method {name!r} output sig code {spec['out']!r} must be '' (void) or "
            f"one of {sorted(_ALLOWED_SIG_CODES)}."
        )


def test_contract_signals_are_well_formed() -> None:
    for name, spec in _CONTRACT["signals"].items():
        assert isinstance(spec, dict), f"signal {name!r} spec must be an object."
        assert "args" in spec and isinstance(spec["args"], list), (
            f"signal {name!r} must declare an 'args' list of sig codes."
        )
        for code in spec["args"]:
            assert code in _ALLOWED_SIG_CODES, (
                f"signal {name!r} arg sig code {code!r} is not one of "
                f"{sorted(_ALLOWED_SIG_CODES)}."
            )


# ── 2. client bus identifiers match the contract ─────────────────────────


@pytest.mark.parametrize(
    ("const_name", "contract_key"),
    [
        ("BUS_NAME", "bus_name"),
        ("OBJECT_PATH", "object_path"),
        ("INTERFACE", "interface"),
        ("ERROR_PREFIX", "error_prefix"),
    ],
)
def test_client_constants_match_contract(const_name: str, contract_key: str) -> None:
    client_value = getattr(netns_client, const_name)
    assert client_value == _CONTRACT[contract_key], (
        f"netns_client.{const_name} == {client_value!r} but contract "
        f"{contract_key!r} == {_CONTRACT[contract_key]!r}. The Python client and "
        f"the shared D-Bus contract have drifted — update whichever is wrong."
    )


# ── 3. method arities (and str typing) match ─────────────────────────────


def test_method_arities_match_contract() -> None:
    for name, spec in _CONTRACT["methods"].items():
        assert name in _CLIENT_METHOD_WRAPPERS, (
            f"contract declares method {name!r} but the Python client exposes no "
            f"proxy wrapper for it (known wrappers: "
            f"{sorted(_CLIENT_METHOD_WRAPPERS)})."
        )
        params = _wrapper_param_names(_CLIENT_METHOD_WRAPPERS[name])
        expected = len(spec["in"])
        assert len(params) == expected, (
            f"method {name!r} arity drift: contract declares {expected} arg(s) "
            f"{spec['in']} but the client wrapper takes {len(params)} "
            f"({params}, excluding self)."
        )


def test_string_method_params_are_annotated_str() -> None:
    """Every contract ``s`` arg maps to a client param annotated ``str``.

    Python doesn't carry D-Bus type codes, so the primary pin is arity above.
    This adds a cheap type-intent check: the whole surface is strings-in, so a
    param whose annotation isn't ``str`` where the contract says ``s`` is a
    drift signal worth catching.
    """
    for name, spec in _CONTRACT["methods"].items():
        wrapper = _CLIENT_METHOD_WRAPPERS[name]
        # netns_client uses `from __future__ import annotations`, so raw
        # annotations are strings; eval_str=True resolves them to real types.
        annotations = inspect.get_annotations(wrapper, eval_str=True)
        params = _wrapper_param_names(wrapper)
        for param_name, code in zip(params, spec["in"], strict=True):
            if code == "s":
                annotation = annotations.get(param_name)
                assert annotation is str, (
                    f"method {name!r} param {param_name!r} is contract sig 's' "
                    f"(string) but its annotation is {annotation!r}, not str."
                )


# ── 4. signal payload matches ────────────────────────────────────────────


def test_subprocess_exit_matches_signal_contract() -> None:
    signal_name = "PortalSubprocessExited"
    assert signal_name in _CONTRACT["signals"], (
        f"contract is missing the {signal_name!r} signal that SubprocessExit "
        "represents."
    )
    expected_args = _CONTRACT["signals"][signal_name]["args"]
    fields = dataclasses.fields(SubprocessExit)
    field_names = tuple(f.name for f in fields)

    assert len(fields) == len(expected_args), (
        f"{signal_name} payload drift: contract declares {len(expected_args)} "
        f"arg(s) {expected_args} but SubprocessExit has {len(fields)} field(s) "
        f"{field_names}."
    )
    # Wire arg order is (pid, exit_code, signal_num) — pin it so a reordered
    # dataclass (which would silently mis-decode the signal) fails here.
    assert field_names == ("pid", "exit_code", "signal_num"), (
        f"SubprocessExit field order {field_names} does not match the "
        f"{signal_name} wire arg order ('pid', 'exit_code', 'signal_num')."
    )


# ── 5. no method-set drift between contract and client ───────────────────


def test_contract_and_client_method_sets_match() -> None:
    contract_methods = set(_CONTRACT["methods"])
    client_methods = set(_CLIENT_METHOD_WRAPPERS)
    assert contract_methods == client_methods, (
        "D-Bus method set drift between the contract and the Python client.\n"
        f"  only in contract: {sorted(contract_methods - client_methods)}\n"
        f"  only in client:   {sorted(client_methods - contract_methods)}\n"
        "Add the method to both sides (contract JSON + the HelperProxy wrapper)."
    )


def test_contract_signal_set_matches_client() -> None:
    """The client models exactly one signal — SubprocessExit → PortalSubprocessExited.

    If the helper grows a second signal, the contract and this client's
    single dataclass must both learn about it; pin the one-signal assumption
    so that addition can't land on only one side.
    """
    assert set(_CONTRACT["signals"]) == {"PortalSubprocessExited"}, (
        f"contract signals {sorted(_CONTRACT['signals'])} drifted from the single "
        "PortalSubprocessExited signal the Python client models (via SubprocessExit). "
        "Add the new signal's payload dataclass and update this guard."
    )
