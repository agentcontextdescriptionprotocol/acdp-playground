"""HMAC signature verify accepts good signatures and rejects bad ones."""

from __future__ import annotations

import hashlib
import hmac

import pytest
from fastapi import HTTPException

from playground.api.webhooks import _verify


def test_verify_accepts_correct_signature():
    secret = "s"
    body = b'{"hello":"world"}'
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    _verify(secret, body, sig)


def test_verify_rejects_wrong_signature():
    with pytest.raises(HTTPException) as exc:
        _verify("s", b"body", "sha256=deadbeef")
    assert exc.value.status_code == 401


def test_verify_rejects_missing_header():
    with pytest.raises(HTTPException) as exc:
        _verify("s", b"body", None)
    assert exc.value.status_code == 401


def test_verify_noop_when_secret_empty():
    _verify("", b"body", None)


def test_verify_rejects_unsupported_algorithm():
    with pytest.raises(HTTPException) as exc:
        _verify("s", b"body", "md5=abc")
    assert exc.value.status_code == 401
