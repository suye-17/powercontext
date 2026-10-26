"""Strict validation at the public source lifecycle event boundary."""

import pytest

from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("node_id", ""),
        ("node_id", " \t\n"),
        ("node_id", 42),
        ("node_id", None),
        ("reason", ""),
        ("reason", " \t\n"),
        ("reason", 42),
        ("reason", None),
        ("revision", 0),
        ("revision", -1),
        ("revision", True),
        ("revision", 3.0),
        ("revision", "3"),
        ("revision", None),
        ("sequence", 0),
        ("sequence", -1),
        ("sequence", False),
        ("sequence", 1.0),
        ("sequence", "1"),
        ("sequence", None),
        ("available", 0),
        ("available", 1),
        ("available", "false"),
        ("available", None),
        ("unexpected", "extra field"),
    ],
)
def test_event_rejects_invalid_fields(field, value) -> None:
    payload = {"node_id": "source-a", "reason": "withdrawn", "revision": 3, "sequence": 1, "available": False}
    payload[field] = value

    with pytest.raises(ValueError):
        SourceLifecycleEvent.model_validate(payload)


@pytest.mark.parametrize("field", ["node_id", "reason", "revision", "sequence", "available"])
def test_event_requires_every_field(field) -> None:
    payload = {"node_id": "source-a", "reason": "withdrawn", "revision": 3, "sequence": 1, "available": False}
    del payload[field]

    with pytest.raises(ValueError):
        SourceLifecycleEvent.model_validate(payload)


@pytest.mark.parametrize("available", [False, True])
def test_event_preserves_exact_valid_identity_and_reason(available) -> None:
    payload = {
        "node_id": "  来源甲  ",
        "reason": "  调用方声明的状态变化  ",
        "revision": 3,
        "sequence": 17,
        "available": available,
    }

    assert SourceLifecycleEvent.model_validate(payload).model_dump() == payload
