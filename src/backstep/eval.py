"""`backstep eval`: run the scripted recovery-benchmark scenarios and grade them.

For each scenario in `tests.scenarios.SCENARIOS`, this spins up a real
backstep proxy — the same interceptor, journal, and Postgres adapter code
that runs in production — against a throwaway Postgres, lets the scenario
damage it exactly as a misbehaving agent would, attempts to revert
everything that happened, and grades the outcome:

  full    — the database matches `expected()` exactly.
  partial — reverting changed something but not everything (e.g.
            cascading_delete: the parent restores, FK-cascaded children
            can't — a real limit of statement-level capture, not a bug).
  none    — reverting changed nothing (and it should have).
  refused — backstep correctly declined to revert (drift, capture_failed).
            This is the *designed* outcome for scenarios that set
            `expect_refusal` — a pass, not a failure.

This is the artifact that turns backstep from "a utility" into "a utility
with a recovery rate attached": `eval_results.json` plus the printed table
are meant to be pasted into a README, not just eyeballed once.

`tests/scenarios` and `testcontainers` are dev-only (the scenarios aren't
shipped in the installed package, and testcontainers isn't a runtime
dependency), so both are imported lazily inside `run_eval` — importing this
module itself stays cheap and doesn't require a repo checkout.
"""

from __future__ import annotations

import json
import os
import sys
import time
from contextlib import AsyncExitStack
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from urllib.parse import quote

import asyncpg
from mcp import Client

from backstep.adapters.base import AdapterRegistry
from backstep.adapters.postgres import PostgresAdapter
from backstep.config import BackstepConfig, ReversibilityClass, ToolPolicy, UpstreamServer
from backstep.journal import Journal
from backstep.proxy import InterceptorConfig, build_registry, build_server
from backstep.registry import ToolRegistry
from backstep.reverter import REFUSAL_OUTCOMES, Reverter, RevertResult

if TYPE_CHECKING:
    from tests.scenarios.base import Scenario

Recovery = Literal["full", "partial", "none", "refused"]

DEFAULT_RESULTS_PATH = "eval_results.json"

#: Points `backstep eval` (and the Postgres-backed test suite — see
#: tests/pg_testing.py) at an already-running Postgres instead of spinning
#: up a fresh testcontainers container. Each run/test still gets an isolated,
#: disposable schema on that server — this only changes where the server
#: itself comes from.
TEST_PG_DSN_ENV_VAR = "BACKSTEP_TEST_PG_DSN"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SQL_SERVER_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "sql_server.py"


def with_search_path(dsn: str, schema: str) -> str:
    """`dsn`, amended so new connections default to `schema`.

    asyncpg forwards unrecognized DSN query params straight into
    `server_settings`, which Postgres accepts as connection-startup
    parameters, so `?search_path=<schema>` just works without any `-c`
    trick or code that has to know about asyncpg's `server_settings` kwarg.
    """
    sep = "&" if "?" in dsn else "?"
    return f"{dsn}{sep}search_path={quote(schema)}"


@dataclass
class ScenarioResult:
    name: str
    description: str
    reversibility_class: str
    expect_refusal: bool
    recovered: Recovery
    time_to_recovery_ms: float
    notes: str


@dataclass
class EvalReport:
    generated_at: str
    results: list[ScenarioResult]

    @property
    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {"full": 0, "partial": 0, "none": 0, "refused": 0}
        for result in self.results:
            counts[result.recovered] += 1
        return counts

    def to_json(self) -> dict[str, object]:
        return {
            "generated_at": self.generated_at,
            "summary": self.summary,
            "scenarios": [asdict(r) for r in self.results],
        }


def _sql_server_config(postgres_dsn: str, reversibility: ReversibilityClass) -> BackstepConfig:
    server = UpstreamServer(
        name="sql",
        command=sys.executable,
        args=[str(_SQL_SERVER_FIXTURE)],
        env={"SQL_SERVER_DSN": postgres_dsn},
        tools=[ToolPolicy(name="execute_sql", reversibility=reversibility, adapter="postgres")],
    )
    return BackstepConfig(servers=[server])


def _display_refusal_reason(result: RevertResult) -> str:
    """The eval table's notes column for a correctly-refused action.

    `refused_no_pre_state`'s generic `RevertResult.detail` ("no pre_state was
    captured for this action") is accurate but unhelpful on its own — it
    doesn't say *why* nothing was captured. Since a capture-declined action
    only ever reaches `refused_no_pre_state` because `capture()` (see
    `backstep.adapters.postgres.PostgresAdapter.capture`) couldn't safely
    turn the statement into a pre-state snapshot, spell that out here for the
    eval row specifically; the journal's own stored note stays as-is for
    `backstep log`/`backstep show`.
    """
    if result.outcome == "refused_no_pre_state":
        return "capture declined: unparseable statement"
    return result.detail


def _grade(
    scenario: Scenario,
    reports: list[RevertResult],
    damaged_state: object,
    actual_state: object,
    expected_state: object,
) -> tuple[Recovery, str]:
    reverted_count = sum(1 for r in reports if r.outcome == "reverted")
    refused = [r for r in reports if r.outcome in REFUSAL_OUTCOMES]

    if scenario.expect_refusal:
        if reverted_count:
            return (
                "none",
                "SAFETY VIOLATION: backstep reverted an action that should have "
                "been refused (drift or an uncapturable statement)",
            )
        if refused:
            reasons = "; ".join(f"{r.tool_name}: {_display_refusal_reason(r)}" for r in refused)
            return "refused", f"correctly refused ({len(refused)} action(s)): {reasons}"
        return "none", "expected a refusal, but nothing was journaled to refuse in the first place"

    if actual_state == expected_state:
        return "full", f"reverted {reverted_count} action(s); state matches the pre-damage baseline"
    if actual_state == damaged_state:
        return "none", "revert attempt made no difference to the damaged state"
    return (
        "partial",
        f"reverted {reverted_count} action(s), but the result doesn't fully match "
        "the pre-damage baseline",
    )


async def _run_one_scenario(
    scenario_cls: type[Scenario],
    postgres_dsn: str,
    adapter: PostgresAdapter,
    journal: Journal,
) -> ScenarioResult:
    scenario = scenario_cls()
    db = await asyncpg.create_pool(dsn=postgres_dsn)
    assert db is not None
    adapter_registry = AdapterRegistry()
    adapter_registry.register(adapter)
    config = _sql_server_config(postgres_dsn, scenario.reversibility_class)

    try:
        session_id = await journal.start_session(client_name=f"eval:{scenario.name}")
        async with AsyncExitStack() as exit_stack:
            proxy_registry = await build_registry(config, exit_stack)
            interceptor = InterceptorConfig(
                tool_registry=ToolRegistry(config),
                journal=journal,
                adapter_registry=adapter_registry,
                session_id=session_id,
            )
            server = build_server(proxy_registry, interceptor)
            async with Client(server) as proxy_client:
                await scenario.setup(db)
                await scenario.damage(proxy_client)

        damaged_state = await scenario.actual(db)

        reverter = Reverter(journal, adapter_registry)
        start = time.perf_counter()
        session_report = await reverter.revert_session(session_id)
        elapsed_ms = (time.perf_counter() - start) * 1000
        reports = session_report.reverted + session_report.skipped_with_reason

        await journal.end_session(session_id)

        actual_state = await scenario.actual(db)
        expected_state = await scenario.expected(db)

        recovered, notes = _grade(scenario, reports, damaged_state, actual_state, expected_state)
        return ScenarioResult(
            name=scenario.name,
            description=scenario.description,
            reversibility_class=scenario.reversibility_class,
            expect_refusal=scenario.expect_refusal,
            recovered=recovered,
            time_to_recovery_ms=round(elapsed_ms, 2),
            notes=notes,
        )
    finally:
        await db.close()


async def _run_scenarios(
    postgres_dsn: str, journal: Journal, scenario_classes: list[type[Scenario]]
) -> list[ScenarioResult]:
    adapter = await PostgresAdapter.connect(postgres_dsn)
    try:
        return [
            await _run_one_scenario(scenario_cls, postgres_dsn, adapter, journal)
            for scenario_cls in scenario_classes
        ]
    finally:
        await adapter.close()


async def _run_on_external_postgres(
    base_dsn: str, journal: Journal, scenario_classes: list[type[Scenario]]
) -> list[ScenarioResult]:
    """Run against `base_dsn` inside a disposable schema, dropped afterwards."""
    schema = f"backstep_eval_{int(time.time())}"
    admin_pool = await asyncpg.create_pool(dsn=base_dsn)
    assert admin_pool is not None
    try:
        async with admin_pool.acquire() as conn:
            await conn.execute(f'CREATE SCHEMA "{schema}"')
    finally:
        await admin_pool.close()

    try:
        return await _run_scenarios(with_search_path(base_dsn, schema), journal, scenario_classes)
    finally:
        drop_pool = await asyncpg.create_pool(dsn=base_dsn)
        assert drop_pool is not None
        try:
            async with drop_pool.acquire() as conn:
                await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await drop_pool.close()


async def _run_on_testcontainers_postgres(
    image: str, journal: Journal, scenario_classes: list[type[Scenario]]
) -> list[ScenarioResult]:
    os.environ.setdefault("TESTCONTAINERS_RYUK_DISABLED", "true")
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(image, driver=None) as container:
        return await _run_scenarios(container.get_connection_url(), journal, scenario_classes)


async def run_eval(
    *, image: str = "postgres:16-alpine", journal_dir: str | Path | None = None
) -> EvalReport:
    """Run every scenario in `tests.scenarios.SCENARIOS` and return a graded report.

    Uses an already-running Postgres (a disposable schema, dropped
    afterwards) when `BACKSTEP_TEST_PG_DSN` is set; otherwise spins up a
    throwaway testcontainers-managed Postgres, same as before.
    """
    import tempfile

    if str(_REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(_REPO_ROOT))
    from tests.scenarios import SCENARIOS

    with tempfile.TemporaryDirectory(prefix="backstep-eval-") as tmp_dir:
        journal_path = Path(journal_dir or tmp_dir) / "journal.db"
        journal = Journal()
        await journal.open(journal_path)

        try:
            external_dsn = os.environ.get(TEST_PG_DSN_ENV_VAR)
            if external_dsn:
                results = await _run_on_external_postgres(external_dsn, journal, SCENARIOS)
            else:
                results = await _run_on_testcontainers_postgres(image, journal, SCENARIOS)
        finally:
            await journal.close()

    return EvalReport(generated_at=datetime.now(UTC).isoformat(), results=results)


def write_report(report: EvalReport, path: str | Path = DEFAULT_RESULTS_PATH) -> Path:
    out_path = Path(path)
    out_path.write_text(json.dumps(report.to_json(), indent=2) + "\n", encoding="utf-8")
    return out_path
