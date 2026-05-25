"""Discovers scenario modules in playground/scenarios/catalog/.

Each catalog module must export ``SCENARIO`` (a :class:`ScenarioDef`)
and an async ``run(spec, events)`` function. The registry imports them
on first use and caches the resolved defs.
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Final

from playground.scenarios.models import ScenarioDef


def _load() -> dict[str, ScenarioDef]:
    catalog_pkg = importlib.import_module("playground.scenarios.catalog")
    out: dict[str, ScenarioDef] = {}
    for mod_info in pkgutil.iter_modules(catalog_pkg.__path__):
        mod = importlib.import_module(f"playground.scenarios.catalog.{mod_info.name}")
        scenario: ScenarioDef | None = getattr(mod, "SCENARIO", None)
        run = getattr(mod, "run", None)
        if scenario is None or run is None:
            continue
        scenario.run = run
        out[scenario.id] = scenario
    return out


scenario_registry: Final[dict[str, ScenarioDef]] = _load()


def list_scenarios() -> list[ScenarioDef]:
    return sorted(scenario_registry.values(), key=lambda s: s.id)


def get_scenario(scenario_id: str) -> ScenarioDef | None:
    return scenario_registry.get(scenario_id)
