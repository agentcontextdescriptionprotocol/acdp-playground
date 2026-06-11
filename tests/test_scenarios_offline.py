"""Offline assertions for the auth-dependent scenarios (S9–S14, S18, S21).

These scenarios can't complete token issuance against a stock registry (the
playground's ``*.playground.local`` DIDs aren't web-hosted and keys rotate per
run), so the docs' contract is: they **degrade gracefully** — completing with
``degraded: true`` rather than failing — while their deterministic crypto/window
cores still run. This suite pins both halves of that contract offline by
pointing the registries (and CP) at an unreachable port, mirroring the S16/S17/
S19/S20 offline tests.

S15 is intentionally absent: it hard-fails without a live registry (no graceful
degrade path), so it is covered by the live suite only.
"""

from __future__ import annotations

import asyncio

import pytest

from playground.config import get_settings
from playground.scenarios import get_scenario
from playground.scenarios.models import RunSpec


@pytest.fixture()
def offline_stack(monkeypatch):
    """Point every backend at an unreachable port so live calls fail fast and
    scenarios take their deterministic + degrade-gracefully paths."""
    monkeypatch.setenv("REGISTRY_A_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("REGISTRY_B_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("CONTROL_PLANE_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", "mock")  # no langchain_openai in CI
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


async def _run(scenario_id: str):
    scenario = get_scenario(scenario_id)
    assert scenario is not None
    q: asyncio.Queue = asyncio.Queue()
    return await scenario.run(RunSpec(run_id=f"r-{scenario_id}", scenario_id=scenario_id), q)


# ── deterministic crypto / window cores (complete, not degraded) ─────────


async def test_s9_p256_publish_crypto_core(offline_stack):
    res = await _run("s9_p256_publish")
    assert res.status == "complete"
    s = res.summary
    assert s["algorithm"] == "ecdsa-p256"
    assert s["local_signature_verified"] is True
    assert s["crypto_ok"] is True


async def test_s12_key_rotation_window_core(offline_stack):
    res = await _run("s12_key_rotation")
    assert res.status == "complete"
    s = res.summary
    assert s["window_ok"] is True
    # Rotation overlap: one key before, both during the overlap, one after.
    assert s["active_before"] == 1
    assert s["active_overlap"] == 2
    assert s["active_after"] == 1


async def test_s21_capabilities_p256_core(offline_stack):
    res = await _run("s21_capabilities_p256")
    assert res.status == "complete"
    s = res.summary
    assert s["algorithm"] == "ecdsa-p256"
    assert s["signature_verified"] is True
    assert s["cp_acceptable"] is True
    assert s["signing_input"].startswith("acdp-cap:v1:")


# ── graceful degradation contract (complete + degraded: true) ────────────


@pytest.mark.parametrize(
    "scenario_id",
    [
        "s10_tenant_isolation",
        "s11_revocation",
        "s13_policy_deny",
        "s14_domain_pack",
        "s18_idempotency",
    ],
)
async def test_auth_scenario_degrades_gracefully(offline_stack, scenario_id):
    res = await _run(scenario_id)
    # The documented contract: degrade, don't hard-fail.
    assert res.status == "complete"
    assert res.summary.get("degraded") is True
    assert res.error is None
