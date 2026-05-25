"""Catalog discovers all expected scenarios."""

from __future__ import annotations

from playground.scenarios import list_scenarios

EXPECTED = {
    "s1_single_publish",
    "s2_producer_consumer",
    "s3_fanout",
    "s4_chain",
    "s5_cross_registry",
    "s6_restricted",
    "s7_supersession",
    "s8_cross_org",
}


def test_all_scenarios_load():
    got = {s.id for s in list_scenarios()}
    assert EXPECTED.issubset(got), f"missing: {EXPECTED - got}"


def test_every_scenario_has_runner():
    for s in list_scenarios():
        assert s.run is not None, f"{s.id} missing run()"
