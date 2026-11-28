"""Adversarial evidence validation: no hardware or system calls."""
import json

import pytest
from r17_row import Observation
from r17_row_events import decode, encode

STATE = (1, 0, 6, 96)
ROW = bytes.fromhex("f609ca099300f4011900")


def record():
    return json.loads(encode(Observation("complete", STATE, STATE, ROW, ROW),
                             1, 2, 1000000, 2000000, "fixture"))


@pytest.mark.parametrize("key,value", [
    ("schema", True), ("schema", 2), ("source", ""), ("source", 9),
    ("source", "a"*129), ("begin_tick", True), ("begin_tick", -1),
    ("end_tick", 2**63), ("end_tick", 0), ("end_ns", 0),
    ("begin_ns", 1.0), ("error", []), ("error", "a"*129),
    ("before", "01000660"), ("before", [1, 0, 6]),
    ("before", [True, 0, 6, 96]), ("before", [256, 0, 6, 96]),
    ("before", [-1, 0, 6, 96]), ("before", None),
    ("after", [1, 1, 6, 96]), ("row_a", []), ("row_a", "00"),
    ("row_a", "F609CA099300F4011900"), ("row_a", "zz"*10),
    ("row_a", None), ("row_b", None), ("error", "unexpected"),
])
def test_invalid_record_rejected(key, value):
    d = record()
    d[key] = value
    with pytest.raises(ValueError):
        decode(json.dumps(d).encode())


@pytest.mark.parametrize("state", [(1, 0, 6, 96), (1, 1, 6, 96), (128, 1, 6, 96)])
def test_admitted_complete_states_roundtrip(state):
    obs = Observation("complete", state, state, ROW, ROW)
    _, restored = decode(encode(obs, 1, 2, 1000000, 2000000, "fixture"))
    assert restored == obs


def test_reject_equal_but_unsupported_state():
    d = record()
    d["before"] = d["after"] = [1, 0, 5, 96]
    with pytest.raises(ValueError, match="incomplete success"):
        decode(json.dumps(d).encode())


@pytest.mark.parametrize("raw", [b"[]", b"null", b'{"schema":1,"schema":1}'])
def test_reject_nonobject_or_duplicate(raw):
    with pytest.raises(ValueError):
        decode(raw)


def test_missing_or_unknown_field():
    d = record()
    del d["error"]
    with pytest.raises(ValueError):
        decode(json.dumps(d).encode())
    d = record()
    d["extra"] = 1
    with pytest.raises(ValueError):
        decode(json.dumps(d).encode())


def test_failed_record_can_preserve_missing_evidence():
    obs = Observation("failed", error="Timeout")
    _, restored = decode(encode(obs, 1, 1, 1000000, 1000000, "fixture"))
    assert restored == obs
