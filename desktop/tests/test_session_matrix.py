"""Phase × Phase transition matrix test.

Asserts every (from, to) combination in PortalPhase has the expected outcome
from `transition_or_none`. This guards against silent regression of the
state machine — e.g., accidentally allowing ACTIVE → ACTIVE.
"""

from __future__ import annotations

import pytest

from gatepath.portal_session import (
    PortalPhase,
    PortalSession,
)

# Authoritative table of valid (from, to) transitions.
# Source: gatepath/portal_session.py _VALID_TRANSITIONS.
_VALID = frozenset(
    {
        (PortalPhase.IDLE, PortalPhase.MONITORING),
        (PortalPhase.MONITORING, PortalPhase.DETECTED),
        (PortalPhase.MONITORING, PortalPhase.IDLE),
        (PortalPhase.DETECTED, PortalPhase.ACTIVE),
        (PortalPhase.DETECTED, PortalPhase.IDLE),
        (PortalPhase.ACTIVE, PortalPhase.COMPLETED),
        (PortalPhase.ACTIVE, PortalPhase.ERROR),
        (PortalPhase.IDLE, PortalPhase.ERROR),
        (PortalPhase.MONITORING, PortalPhase.ERROR),
        (PortalPhase.DETECTED, PortalPhase.ERROR),
    }
)

_ALL_PHASES = list(PortalPhase)
_ALL_PAIRS = [(a, b) for a in _ALL_PHASES for b in _ALL_PHASES]


@pytest.mark.parametrize("from_phase,to_phase", _ALL_PAIRS)
def test_transition_matrix(from_phase: PortalPhase, to_phase: PortalPhase) -> None:
    """For every cell of the Phase × Phase matrix, assert success or rejection
    matches the authoritative table.
    """
    session = PortalSession(phase=from_phase)
    result = session.transition_or_none(to_phase)
    expected_valid = (from_phase, to_phase) in _VALID

    if expected_valid:
        assert result is not None, (
            f"Expected ({from_phase.name} -> {to_phase.name}) to succeed; got None"
        )
        assert result.phase == to_phase
        # Original session must be unchanged (immutability).
        assert session.phase == from_phase
    else:
        assert result is None, (
            f"Expected ({from_phase.name} -> {to_phase.name}) to be rejected; "
            f"got {result!r}"
        )


def test_matrix_size_equals_phase_count_squared() -> None:
    """Sanity: the matrix covers every cell exactly once."""
    n = len(_ALL_PHASES)
    assert len(_ALL_PAIRS) == n * n


def test_valid_transitions_subset_of_full_matrix() -> None:
    """Sanity: every valid transition is also in the full matrix."""
    full = set(_ALL_PAIRS)
    assert _VALID.issubset(full)
