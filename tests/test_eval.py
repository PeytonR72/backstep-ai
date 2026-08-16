"""End-to-end smoke test for `backstep eval` (see backstep.eval, tests/scenarios).

Runs the real recovery-benchmark suite against Postgres — BACKSTEP_TEST_PG_DSN
if set, else a throwaway testcontainers container (see tests/pg_testing.py).
Skipped (not failed) when neither is available.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")

from tests.pg_testing import requires_postgres

pytestmark = requires_postgres


async def test_eval_runs_every_scenario_and_never_silently_reverts_a_refusal(
    tmp_path: Path,
) -> None:
    from backstep.eval import run_eval

    report = await run_eval(journal_dir=tmp_path)

    from tests.scenarios import SCENARIOS

    assert {r.name for r in report.results} == {cls.name for cls in SCENARIOS}

    for result in report.results:
        if result.expect_refusal:
            # The whole point of these scenarios: backstep must refuse, never
            # partially or fully "recover" by silently overwriting something
            # it shouldn't have (a drifted row, an uncapturable statement).
            assert result.recovered == "refused", (
                f"{result.name} should have refused but reported "
                f"{result.recovered!r}: {result.notes}"
            )
        else:
            assert result.recovered in ("full", "partial", "none")

    summary = report.summary
    assert sum(summary.values()) == len(report.results) == len(SCENARIOS)
