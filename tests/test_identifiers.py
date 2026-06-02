"""Tests for origin_registry / authority identifier hygiene."""

from __future__ import annotations

import pytest

from acdp_client.identifiers import is_valid_authority, validate_origin_registry


@pytest.mark.parametrize(
    "host",
    [
        "registry-a.playground.local",
        "registry.example.com",
        "a.b.c.d.example",
        "x1.example",
    ],
)
def test_valid_authorities(host):
    assert is_valid_authority(host) is True
    validate_origin_registry(host)  # no raise


@pytest.mark.parametrize(
    "host",
    [
        "did:web:registry.example.com",  # DID form
        "registry.example.com:8443",  # port
        "https://registry.example.com",  # scheme
        "Registry.Example.Com",  # uppercase
        "registry.example.com.",  # trailing dot
        "registry..example",  # empty label
        "-bad.example",  # leading hyphen
        "bad-.example",  # trailing hyphen
        "",  # empty
    ],
)
def test_invalid_authorities(host):
    assert is_valid_authority(host) is False
    with pytest.raises(ValueError):
        validate_origin_registry(host)
