"""backstep's scripted "agent goes wrong" recovery-benchmark scenarios.

`backstep eval` (see `backstep.eval`) runs every scenario in `SCENARIOS`
against a throwaway Postgres and grades how well backstep recovers from
each. See `tests.scenarios.base.Scenario` for what a scenario is.
"""

from __future__ import annotations

from tests.scenarios.base import Scenario
from tests.scenarios.cascading_delete import CascadingDeleteScenario
from tests.scenarios.mixed_session import MixedSessionScenario
from tests.scenarios.partial_drift import PartialDriftScenario
from tests.scenarios.runaway_insert_loop import RunawayInsertLoopScenario
from tests.scenarios.unparseable_sql import UnparseableSqlScenario
from tests.scenarios.wrong_where_update import WrongWhereUpdateScenario

SCENARIOS: list[type[Scenario]] = [
    WrongWhereUpdateScenario,
    CascadingDeleteScenario,
    RunawayInsertLoopScenario,
    MixedSessionScenario,
    PartialDriftScenario,
    UnparseableSqlScenario,
]

__all__ = ["SCENARIOS", "Scenario"]
