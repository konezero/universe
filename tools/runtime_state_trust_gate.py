"""Programmable Runtime State Trust Gate helpers.

Source: `.ai/core/RUNTIME_STATE_TRUST_GATE.md`

Active `*_ING` is an operation-state class under the Current Anchor
currentness key. It is not authority and not Mode Current `CURRENT`.
"""

from __future__ import annotations

from typing import Any

CONTRACT_REF = ".ai/core/RUNTIME_STATE_TRUST_GATE.md"
ACTIVE_ING_SUFFIX = "ING"

# Declared active operation states from the gate. Extend this set when the
# contract adds a name; listing code stays on is_active_ing_state().
DECLARED_ACTIVE_ING_STATES = frozenset(
    {
        "BOOTING",
        "INDEXING",
        "INSTALLING",
        "VALIDATING",
        "SYNCING",
        "CHECKPOINTING",
        "REBUILDING",
        "EXECUTING",
    }
)

# Tokens that end in ING but are not operation states.
NON_OPERATION_ING_STATES = frozenset(
    {
        "MISSING",
        "NOTHING",
    }
)


def normalize_runtime_state(value: Any) -> str:
    return str(value or "").strip().upper()


def is_active_ing_state(value: Any) -> bool:
    """Return True when value is an active `*_ING` operation state.

    The programmable class is the suffix `ING`. Membership in the declared
    gate vocabulary is required so labels such as CONNECTING or LISTENING
    are not treated as Current Anchor operation states.
    """

    token = normalize_runtime_state(value)
    if not token.endswith(ACTIVE_ING_SUFFIX):
        return False
    if token in NON_OPERATION_ING_STATES:
        return False
    return token in DECLARED_ACTIVE_ING_STATES
