"""Gating + fixtures for the live conformance suite.

These tests drive the real registry / control-plane binaries (``make up-full``).
They are skipped entirely unless ``ACDP_LIVE_STACK`` is set, so the default
offline ``pytest`` run never touches the network. The collection hook below tags
every test in this package with ``@pytest.mark.live`` (so ``pytest -m live``
selects exactly this suite) and applies the skip when the stack is absent — a
``pytestmark`` in a conftest does *not* propagate to sibling test modules, so the
gate has to be installed here at collection time.
"""

from __future__ import annotations

import os

import httpx
import pytest
import pytest_asyncio

from playground.conformance import LiveConfig

_HERE = os.path.dirname(__file__)


def pytest_collection_modifyitems(config, items):
    have_stack = bool(os.environ.get("ACDP_LIVE_STACK"))
    skip_stack = pytest.mark.skip(
        reason="live stack not running — set ACDP_LIVE_STACK=1 after `make up-full`"
    )
    for item in items:
        if str(item.fspath).startswith(_HERE):
            item.add_marker(pytest.mark.live)
            if not have_stack:
                item.add_marker(skip_stack)


@pytest.fixture(scope="session")
def live_config() -> LiveConfig:
    return LiveConfig.from_settings()


@pytest_asyncio.fixture
async def live_client() -> httpx.AsyncClient:
    async with httpx.AsyncClient(timeout=15.0) as client:
        yield client
