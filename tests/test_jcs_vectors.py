"""Validate the JCS numeric reference against the RFC's can-011 vectors.

The fixtures live in the sibling RFC repo. The test loads them from
``ACDP_RFC_DIR`` (default ``../agentcontextdescriptionprotocol``) and
skips cleanly when the repo is not checked out alongside the playground.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from acdp_client.jcs_numbers import canonicalize, ecma_number_to_string

_RFC_DIR = Path(os.environ.get("ACDP_RFC_DIR", "../agentcontextdescriptionprotocol"))
_VECTORS = _RFC_DIR / "schemas" / "conformance" / "can-011-jcs-numeric-vectors.json"


def _load_vectors():
    if not _VECTORS.exists():
        pytest.skip(f"RFC conformance vectors not found at {_VECTORS}")
    return json.loads(_VECTORS.read_text())["vectors"]


def test_can011_vectors_match():
    for vec in _load_vectors():
        got = canonicalize(vec["input"])
        want = vec["expected"]["canonical_form"]
        assert got == want, f"{vec['name']}: {got!r} != {want!r}"
        digest = hashlib.sha256(got.encode("utf-8")).hexdigest()
        assert digest == vec["expected"]["sha256_hex"], vec["name"]


def test_negative_zero_normalizes():
    assert ecma_number_to_string(-0.0) == "0"
    assert ecma_number_to_string(0.0) == "0"


def test_exponential_bands():
    assert ecma_number_to_string(1e21) == "1e+21"
    assert ecma_number_to_string(1e-7) == "1e-7"
    assert ecma_number_to_string(1e-6) == "0.000001"  # decimal band edge
    assert ecma_number_to_string(5e-9) == "5e-9"


def test_integer_exactness():
    assert ecma_number_to_string(2**53) == "9007199254740992"
    assert ecma_number_to_string(100) == "100"
    assert ecma_number_to_string(-7) == "-7"


def test_trailing_zero_dropped():
    assert ecma_number_to_string(1.10) == "1.1"
    assert ecma_number_to_string(1.50) == "1.5"


def test_rejects_nan_inf():
    with pytest.raises(ValueError):
        ecma_number_to_string(float("nan"))
    with pytest.raises(ValueError):
        ecma_number_to_string(float("inf"))
