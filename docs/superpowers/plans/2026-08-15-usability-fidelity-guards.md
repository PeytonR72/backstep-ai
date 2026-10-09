# Usability, Type Fidelity & Mutation Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Unblock first-run usability (`doctor`/`eval`/example config), replace the lossy `json.dumps(default=str)` journal codec with a reversible one, and add regression tests that prove the refusal/drift/journal-state-machine machinery actually works rather than merely asserting it does.

**Architecture:** Five independently testable increments against the existing `backstep-ai` codebase (`src/backstep/`): (1) CLI/config/eval usability fixes, (2) a typed, reversible journal codec used identically on the write and read paths plus in Postgres fingerprinting, (3) monkeypatch-based "mutation guard" tests that disable one safety mechanism at a time and assert the eval grade flips to a failure, (4) a multi-transition journal state-machine test suite, (5) a full verification pass reporting real pass/skip counts and the `backstep eval` table. Task 2 changes the on-disk fingerprint representation, so it must land — and any behavioral fallout must be understood — before Tasks 3/4 are written, per the user's explicit ordering constraint.

**Tech Stack:** Python 3.11, pydantic v2, Typer, Rich, aiosqlite, asyncpg, sqlglot, pytest + pytest-asyncio (`asyncio_mode = "auto"`), testcontainers (Postgres), ruff, mypy (`disallow_untyped_defs = true`).

**Spec:** The user's 5-task message (reproduced in full in this conversation) — no separate spec file; this plan's Global Constraints section transcribes every hard requirement verbatim so it travels with the plan.

## Global Constraints

- Every Postgres-backed test in this plan MUST actually execute (not skip) — `BACKSTEP_TEST_PG_DSN` is live. Do not add new skip conditions; use the existing `requires_postgres` marker / `postgres_dsn` fixture from `tests/pg_testing.py` / `tests/conftest.py`, which already run for real when the env DSN is set.
- Do not reorder Task 2 relative to Tasks 3/4 — Task 2 changes fingerprint canonicalization, and Tasks 3/4 write tests against that canonicalization.
- `backstep eval` must still grade **3 full / 1 partial / 2 refused** after all changes. If Task 2 shifts any grade, STOP, report the change, and do NOT silently update `README.md`'s results table — flag it for human review instead.
- Task 2b requirement (i): if the fingerprint-stability-under-no-change assertion fails for any column type, stop and fix before continuing — that is a live false-positive drift bug, not a test bug to work around.
- New pytest marker `mutation` must be registered in `pyproject.toml` (`[tool.pytest.ini_options] markers = [...]`) and documented in README's testing section.
- `uv run ruff check .` and `uv run mypy .` must both pass clean at the end (Task 5) — `disallow_untyped_defs = true` is already on, so all new functions need full type annotations.
- Never claim a command succeeded without having actually run it and observed the output (Task 5 explicit instruction, but applies throughout — this codebase runs on Windows/PowerShell per the environment, but `uv run pytest` etc. work the same via the Bash tool's Git Bash).
- Existing behavioral doc comments (e.g. `journal.py`'s docstring on why `default=str` was used, `reverter.py`'s comment about drift refusal not calling `mark_unrevertable`) must be updated/removed if the plan's changes make them inaccurate — stale comments are worse than no comments.

---

## Reference: Codebase Map (from exploration — do not re-derive)

- `src/backstep/cli.py` (629 lines): Typer app. `doctor` cmd `cli.py:595-621` → `_print_doctor_report` same range calls into `doctor.py`. `eval` cmd `cli.py:119-136`, table rendering `_print_eval_table` `cli.py:139-178`. No `--verbose` flag exists yet on `eval`. `_VALID_STATUSES` duplicated at `cli.py:37` (also in `journal.ActionStatus`).
- `src/backstep/config.py` (57 lines): `BackstepConfig`, `UpstreamServer`, `ToolPolicy(name, reversibility, adapter, require_confirmation, blocked)`, `PostgresAdapterConfig(dsn, max_captured_rows)`, `AdaptersConfig(postgres)`, `UnknownToolBehavior = Literal["deny","warn","allow"]`. `load_config(path)` at `config.py:54-57` does `Path(path).read_text(...)` — raises uncaught `FileNotFoundError` when missing.
- `src/backstep/doctor.py` (159 lines): `DoctorCheck(name, ok, detail)` frozen dataclass, `UnclassifiedTool(server_name, tool_name)`, `DoctorReport(checks, unclassified_tools)` with `.ok` property (`all(c.ok for c in checks)`). `run_doctor(config_path)` `doctor.py:134-159` is an imperative sequence: config load (try/except → single `DoctorCheck("config", ...)`), then per-server reachability `_check_server` `doctor.py:62-71`, then `_check_postgres_adapter` `doctor.py:74-87`, then `_check_journal` `doctor.py:90-110`, then `_eval_postgres_mode_check` `doctor.py:113-131` (always ok=True, informational).
- `src/backstep/eval.py` (295 lines): `Recovery = Literal["full","partial","none","refused"]` `eval.py:55`. `ScenarioResult` dataclass `eval.py:82-90` has `notes: str`. `_grade` `eval.py:124-154` uses `REFUSAL_OUTCOMES` from `backstep.reverter`. `TEST_PG_DSN_ENV_VAR = "BACKSTEP_TEST_PG_DSN"` `eval.py:64`. Scenarios imported lazily from `tests.scenarios` (`eval.py:271-273`) since that package is dev-only, not shipped.
- `tests/scenarios/__init__.py`: `SCENARIOS` list, 6 entries: `WrongWhereUpdateScenario` (R1), `CascadingDeleteScenario` (R2, expect "partial"), `RunawayInsertLoopScenario` (R1), `MixedSessionScenario` (R2), `PartialDriftScenario` (R1, `expect_refusal=True`), `UnparseableSqlScenario` (R2, `expect_refusal=True`).
- `src/backstep/journal.py` (347 lines, full contents already read into this plan's context): `json.dumps(args, default=str)` at line 171, `json.dumps(pre_state, default=str)` at line 172 (`begin_action`), `json.dumps(post_state, default=str)` at line 214 (`commit_action`). Read path `_row_to_action` `journal.py:329-347` uses plain `json.loads` — no custom decoder. `ActionRecord` pydantic model `journal.py:69-85`. `SCHEMA_VERSION = 1` at line 26.
- `src/backstep/adapters/postgres.py` (662 lines, full contents already read): `_fingerprint_state` `postgres.py:144-164` — sha256 over rows sorted by PK, `json.dumps(ordered, sort_keys=True, separators=(",",":"), default=str)`. `PostgresAdapter.fingerprint()` `postgres.py:249-250` thin wrapper. `current_fingerprint(adapter, entry)` free function `postgres.py:651-662`. Row values come from `dict(record)` on `asyncpg.Record` (lines 360, 445, 581) — native `Decimal`/`datetime`/`UUID`/etc. sqlglot parse guard `postgres.py:206-210` (catches syntax errors only). `capture()` `postgres.py:201-229` wraps dispatch in `try/except Exception: logger.exception(...); return None` (lines 218-227) — this is where a runtime Postgres error during `_capture_rows`'s `conn.fetch(select.sql(...))` (e.g. `UndefinedTableError` from a CTE-referencing WHERE clause) is already caught, today, and logged at ERROR via `logger.exception`.
- `src/backstep/reverter.py` (432 lines): `RevertOutcome` Literal + `REFUSAL_OUTCOMES` frozenset `reverter.py:39-65` (`"failed"` and `"reverted"` deliberately excluded). `_revert_entry` `reverter.py:258-370` — ordered refusal checks: `refused_already_reverted` (259-267), `refused_not_committed` (269-277), `refused_irreversible`/R4 (279-289, calls `mark_unrevertable`), `refused_no_inverse` (294-299), `refused_no_pre_state`/`refused_no_adapter` (301-313), `refused_no_primary_key` (315-331, DELETE-specific, calls `_delete_pre_state_data` `reverter.py:185-192`), `refused_drift` (333-349, via `_check_drift`, does NOT call `mark_unrevertable` — status stays `committed` so `force=True` retry works), `"failed"` (351-362, `adapter.revert()` returned `ok=False`, calls `mark_unrevertable`), `"reverted"` (364-370, calls `mark_reverted`). `_check_drift` `reverter.py:372-380` dispatches to `_check_delete_drift` or `_check_update_or_insert_drift`. `force` only affects the drift check (step 7); nothing else is bypassable.
- `tests/conftest.py`: `_postgres_base_dsn` (session-scoped, `conftest.py:26-55`) — uses `BACKSTEP_TEST_PG_DSN` directly when set, else testcontainers/skip. `postgres_dsn` (function-scoped, `conftest.py:58-77`) — creates/drops a disposable `backstep_test_<uuid>` schema per test via `with_search_path`.
- `tests/pg_testing.py`: `TEST_PG_DSN_ENV_VAR`, `postgres_available()`, `postgres_test_mode()`, `requires_postgres = pytest.mark.skipif(...)`, `with_search_path`.
- `pyproject.toml`: no `markers` section exists yet; deps include `asyncpg>=0.30.0`, `sqlglot>=25.0.0`, dev deps include `testcontainers[postgres]>=4.8.0`. Ruff `select = ["E","F","I","UP","B","SIM"]`, line-length 100. Mypy `disallow_untyped_defs = true`, override for `asyncpg.*` ignore_missing_imports.
- `README.md`: `## Development` (5-15), `### Postgres-backed tests and \`backstep eval\`` (17-44, closest thing to a testing section — add `mutation` marker docs here), `## Recovery benchmark` (45-83, hardcoded results table at 73-82 mirroring `eval_results.json`).

---

### Task 1a: `backstep.example.toml` + README quickstart reference

**Files:**
- Create: `backstep.example.toml` (repo root)
- Modify: `README.md` (add a Quickstart reference before or in `## Development`)

**Interfaces:**
- Consumes: `BackstepConfig` schema from `src/backstep/config.py` (read the file as-is; do not modify config.py in this task).
- Produces: nothing consumed by later tasks in this plan, but Task 1b's tests reference this file's existence/content as the "valid config" fixture case.

- [ ] **Step 1: Write `backstep.example.toml`**

Base it on the real `BackstepConfig`/`UpstreamServer`/`ToolPolicy`/`PostgresAdapterConfig` fields read from `src/backstep/config.py`. One upstream Supabase/Postgres server, `[adapters.postgres]` with a `dsn` key, `unknown_tool_behavior = "warn"`, and 4 `ToolPolicy` entries covering R0/R1/R2/R4 with a one-line comment each explaining the risk class (definitions live in `bootstrap.py:33-39`: R0 read-only, R1 fully reversible, R2 reversible with loss, R3 compensable-only, R4 irreversible — use R0/R1/R2/R4 per the task, skipping R3 is fine since only 3-4 entries are asked for):

```toml
# backstep.example.toml
#
# Copy this to backstep.toml and fill in your own server command/args and
# Postgres DSN. Run `backstep doctor` afterwards to verify connectivity.

journal_path = "~/.backstep/journal.db"

# What to do when an upstream tool has no matching [[servers.tools]] entry
# below: "deny" blocks the call, "warn" lets it through but logs, "allow"
# lets it through silently. Start with "warn" until you've classified every
# tool your upstream server exposes.
unknown_tool_behavior = "warn"

[adapters.postgres]
# Used by the postgres adapter to capture/revert row state independently of
# whatever connection your upstream MCP server uses. Overridden by the
# BACKSTEP_PG_DSN environment variable if set.
dsn = "postgresql://postgres:postgres@localhost:54322/postgres"
max_captured_rows = 1000

[[servers]]
name = "supabase"
command = "npx"
args = ["-y", "@supabase/mcp-server-supabase@latest", "--project-ref=<your-project-ref>"]

[servers.env]
SUPABASE_ACCESS_TOKEN = "<your-access-token>"

# R0: read-only, no state change — safe to allow unconditionally.
[[servers.tools]]
name = "list_tables"
reversibility = "R0"

# R1: fully reversible — pre-state can be captured and restored exactly.
# adapter = "postgres" tells backstep to use the Postgres adapter to
# capture/revert the row(s) this tool's SQL touches.
[[servers.tools]]
name = "execute_sql"
reversibility = "R1"
adapter = "postgres"

# R2: reversible with loss — e.g. a DELETE whose identity/metadata columns
# can't be perfectly restored on revert. require_confirmation asks before
# running.
[[servers.tools]]
name = "apply_migration"
reversibility = "R2"
adapter = "postgres"
require_confirmation = true

# R4: irreversible — blocked outright rather than merely warned about.
[[servers.tools]]
name = "delete_project"
reversibility = "R4"
blocked = true
```

Validate it parses and matches the schema:

```bash
uv run python -c "from backstep.config import load_config; print(load_config('backstep.example.toml'))"
```
Expected: prints a `BackstepConfig(...)` repr with no exception.

- [ ] **Step 2: Reference it from the README quickstart**

Add a new `## Quickstart` section to `README.md` immediately after the title/description (before `## Development`):

```markdown
## Quickstart

1. Copy `backstep.example.toml` to `backstep.toml` and fill in your upstream
   server command, args, and Postgres DSN.
2. Run `uv run backstep doctor` to verify config, journal, and adapter
   connectivity before pointing a real MCP client at `backstep serve`.
```

- [ ] **Step 3: Commit**

```bash
git add backstep.example.toml README.md
git commit -m "docs: add backstep.example.toml and README quickstart"
```

---

### Task 1b: `backstep doctor` degrades instead of failing fast on missing config

**Files:**
- Modify: `src/backstep/doctor.py` (`DoctorCheck`/`DoctorReport` types, `run_doctor`, `_check_journal`, add new "config-dependent vs config-independent" split)
- Modify: `src/backstep/cli.py:595-621` (`_print_doctor_report` — needs to render SKIPPED distinctly from FAILED)
- Test: `tests/test_cli.py` (extend existing doctor tests)

**Interfaces:**
- Consumes: `load_config` from `backstep.config` (unchanged signature), `Journal` from `backstep.journal`, `PostgresAdapter.connect`/`resolve_dsn` from `backstep.adapters.postgres`, `TEST_PG_DSN_ENV_VAR` from `backstep.eval`.
- Produces: `DoctorCheck` gets a third state. Read `doctor.py` in full before editing — the exploration only summarized it; confirm exact current signatures of `_check_server`, `_check_postgres_adapter`, `_check_journal`, `_eval_postgres_mode_check` before changing their call sites.

- [ ] **Step 1: Read `src/backstep/doctor.py` in full** to get exact current code (the plan's map is a summary, not a substitute for reading before editing).

- [ ] **Step 2: Add a `status` field to `DoctorCheck` replacing/extending the boolean `ok`**

Change `DoctorCheck` from `(name, ok, detail)` to carry a tri-state status while staying backward compatible with `.ok`:

```python
CheckStatus = Literal["passed", "failed", "skipped"]

@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str

    @property
    def ok(self) -> bool:
        """True unless this check FAILED. SKIPPED checks do not fail the run."""
        return self.status != "failed"
```

Update `DoctorReport.ok` (`doctor.py:30-50` area) to: `all(check.status != "failed" for check in self.checks)` — exit code stays nonzero only on an actual FAILED check, never on SKIPPED.

Update every existing call site in `doctor.py` that constructs `DoctorCheck(name, True/False, detail)` to use `DoctorCheck(name, "passed"/"failed", detail)` instead (grep for `DoctorCheck(` inside `doctor.py`).

- [ ] **Step 3: Rewrite `run_doctor` to degrade on missing/unparseable config**

Replace the current try/except-around-`load_config`-then-continue-anyway logic (verify from the full read in Step 1 whether checks after config currently even run when config load fails — the exploration notes it *is* wrapped in try/except but the report says "each check... must still run," so confirm and fix if any check currently short-circuits). Target structure:

```python
async def run_doctor(config_path: str | Path) -> DoctorReport:
    checks: list[DoctorCheck] = []
    unclassified_tools: list[UnclassifiedTool] = []

    config: BackstepConfig | None = None
    try:
        config = load_config(config_path)
        checks.append(DoctorCheck("config", "passed", f"loaded {config_path}"))
    except FileNotFoundError:
        checks.append(DoctorCheck(
            "config", "failed",
            f"no config found at {config_path}; run `backstep init` or copy "
            "backstep.example.toml to backstep.toml",
        ))
    except Exception as exc:
        checks.append(DoctorCheck(
            "config", "failed",
            f"failed to parse {config_path}: {exc}; run `backstep init` or copy "
            "backstep.example.toml to backstep.toml",
        ))

    # --- config-independent checks: always run ---
    checks.append(await _check_journal(config))          # journal readability + schema version
    checks.append(await _check_postgres_env_adapter())    # BACKSTEP_PG_DSN connectivity, if set
    checks.append(_eval_postgres_mode_check())

    # --- config-dependent checks: SKIPPED (not FAILED) when config is None ---
    if config is None:
        checks.append(DoctorCheck("upstream servers", "skipped", "no config loaded"))
        checks.append(DoctorCheck("unclassified tools", "skipped", "no config loaded"))
    else:
        for server in config.servers:
            checks.append(await _check_server(server))
        # existing unclassified-tool collection logic, appended to `unclassified_tools`
        if config.adapters.postgres is not None or any(
            t.adapter == "postgres" for s in config.servers for t in s.tools
        ):
            checks.append(await _check_postgres_adapter(config))
        else:
            checks.append(DoctorCheck("postgres adapter", "skipped", "not configured"))

    return DoctorReport(checks=checks, unclassified_tools=unclassified_tools)
```

Key semantic decisions this codifies (match task requirements exactly):
- Journal readability/schema version check must run **even with no config** — `_check_journal` currently takes `config` to find `journal_path`; if config is `None`, fall back to the default `journal_path` from `BackstepConfig.model_fields["journal_path"].default` (or hardcode `"~/.backstep/journal.db"` matching `config.py`'s default — prefer reading the default programmatically so the two never drift) rather than skipping the check.
- Adapter DSN connectivity must run "if `BACKSTEP_PG_DSN` or the env DSN is set" — this needs a **new** helper `_check_postgres_env_adapter()` that checks `os.environ.get("BACKSTEP_PG_DSN")` directly (not the config-derived DSN, since this must work with no config) and reports `skipped` with reason "BACKSTEP_PG_DSN not set" if absent, `passed`/`failed` on actual connection attempt otherwise. This is distinct from the existing `_check_postgres_adapter(config)` (which is config-dependent because it also reads `config.adapters.postgres.dsn`) — keep both: the new one is unconditional, the existing one is only invoked in the `config is not None` branch and should itself become `skipped` (not omitted) when postgres isn't referenced by config.
- `eval:postgres-mode` check must always run — already the case (`_eval_postgres_mode_check` is already config-independent per the exploration notes; just ensure it's called unconditionally in the new structure, and confirm it doesn't itself take `config` as a required non-optional arg that this change breaks).
- Upstream reachability (`_check_server` per server) and unclassified-tool listing must become `skipped` (not omitted from the report and not `failed`) when config is missing.

Adjust each helper's signature as needed (e.g., `_check_journal` taking `BackstepConfig | None`) — read their current bodies in Step 1 before changing signatures, and update every call site.

- [ ] **Step 4: Update `_print_doctor_report` in `cli.py:595-621`** to render the three states distinctly (e.g. green "PASSED", red "FAILED", yellow "SKIPPED") and keep the exit-code logic (`cli.py`'s doctor command should `raise typer.Exit(code=1)` iff `report.ok` is `False`, i.e. iff any check has `status == "failed"` — unchanged semantics via the `.ok` property, just verify the CLI reads `.ok` and not raw booleans anywhere).

- [ ] **Step 5: Write CLI tests for the three required cases** in `tests/test_cli.py` (append to the existing doctor test block near `test_doctor_fails_on_unparseable_config`):

```python
def test_doctor_no_config_present(tmp_path, ...):
    """No backstep.toml at all: config check FAILS with a path + backstep init
    pointer, but journal/adapter/eval:postgres-mode checks still run, and
    config-dependent checks (servers, unclassified tools) report SKIPPED."""
    missing_path = tmp_path / "backstep.toml"
    result = runner.invoke(app, ["doctor", "--config", str(missing_path)])
    assert result.exit_code != 0
    assert "backstep init" in result.stdout
    assert "backstep.example.toml" in result.stdout
    assert "SKIPPED" in result.stdout  # upstream servers / unclassified tools
    # journal check must have run (its name appears in the table) even though
    # config failed to load
    assert "journal" in result.stdout.lower()

def test_doctor_malformed_config(tmp_path, ...):
    bad = tmp_path / "backstep.toml"
    bad.write_text("this is not [ valid toml", encoding="utf-8")
    result = runner.invoke(app, ["doctor", "--config", str(bad)])
    assert result.exit_code != 0
    assert "failed to parse" in result.stdout.lower() or "config" in result.stdout.lower()
    assert "SKIPPED" in result.stdout

def test_doctor_valid_config(tmp_path, ...):
    # copy backstep.example.toml content (adjust DSN/command as needed to be
    # inert for a CLI-level test — the servers don't need to actually
    # connect for this test if _check_server failures still produce a
    # DoctorCheck rather than raising)
    ...
    result = runner.invoke(app, ["doctor", "--config", str(cfg_path)])
    assert "SKIPPED" not in result.stdout  # all config-dependent checks ran
```
Match the actual fixture/CLI-invocation pattern already used in `tests/test_cli.py` for `test_doctor_fails_on_unparseable_config` (read that test first, reuse its `runner`/fixture setup rather than inventing a new one).

- [ ] **Step 6: Run the new tests**

```bash
uv run pytest tests/test_cli.py -k doctor -v
```
Expected: all doctor tests PASS, none skipped.

- [ ] **Step 7: Commit**

```bash
git add src/backstep/doctor.py src/backstep/cli.py tests/test_cli.py
git commit -m "fix: backstep doctor degrades gracefully on missing/malformed config"
```

---

### Task 1c: `backstep eval` — no raw traceback for unparseable_sql, add `--verbose`

**Files:**
- Modify: `src/backstep/adapters/postgres.py` (`capture()`, `postgres.py:201-229` and the `_capture_rows` call path — confirm exact current exception handling before changing)
- Modify: `src/backstep/eval.py` (`ScenarioResult`/`_grade`/runner — notes column rendering for the "capture declined" case; may not need changes if `postgres.py` already sets the right note text, but verify)
- Modify: `src/backstep/cli.py:119-136` (`eval` command — add `--verbose` flag), `cli.py:139-178` (`_print_eval_table` — verbose toggles traceback visibility)
- Test: `tests/test_cli.py` or `tests/test_eval.py` (assert default output has no traceback text)

**Interfaces:**
- Consumes: `UnparseableSqlScenario` from `tests/scenarios/unparseable_sql.py` (dev-only, read it first — confirm what SQL it actually issues and what exception it triggers; the exploration found capture already returns `None` via a caught `Exception` inside `postgres.py:218-227`, with `logger.exception(...)` at ERROR level, not DEBUG).
- Produces: `capture()`'s behavior on this path (return `None` on any exception) is UNCHANGED by this task — only the **logging level** (ERROR → DEBUG per the task) and the **downstream notes-column text** change. Do not alter control flow that other tasks (2, 3) depend on.

- [ ] **Step 1: Read `tests/scenarios/unparseable_sql.py` and confirm the actual failure path**

The exploration agent already traced this: the scenario's CTE-heavy UPDATE parses fine under sqlglot (so the `postgres.py:206-210` parse guard is NOT what fires), but the runtime `SELECT`-rewrite (`_capture_rows`, `postgres.py:328-345`) against Postgres raises `asyncpg.exceptions.UndefinedTableError` (the CTE alias doesn't exist as a real table once rewritten as a bare SELECT), which is caught by the `try/except Exception` wrapping the dispatch in `capture()` at `postgres.py:218-227` — logged via `logger.exception(...)` (ERROR level, includes traceback) and returns `None`. Confirm this by actually running the scenario and observing today's output before changing anything:

```bash
uv run backstep eval 2>&1 | tee /tmp_or_scratchpad/before.txt
```
(Use the scratchpad dir, not `/tmp`, per environment rules.) Confirm a traceback appears in stderr/stdout output.

- [ ] **Step 2: Downgrade the log level and clarify the message at `postgres.py:218-227`**

```python
        try:
            if isinstance(statement, exp.Insert):
                return await self._capture_insert(statement)
            if isinstance(statement, exp.Update):
                return await self._capture_rows(statement, op="update")
            if isinstance(statement, exp.Delete):
                return await self._capture_rows(statement, op="delete")
        except Exception:
            logger.debug(
                "postgres adapter: capture declined for %r (unparseable statement or "
                "runtime error building the pre-state SELECT)",
                tool_name,
                exc_info=True,
            )
            return None
```
`exc_info=True` still attaches the traceback to the DEBUG log record (so it's available with `-v`/DEBUG logging configured) without it appearing at default log levels or being printed to the console by Rich.

- [ ] **Step 3: Verify nothing else prints a traceback for this path**

Check `proxy.py`'s `_intercept_call_tool` (~`proxy.py:341-352` per the exploration) which also wraps `await adapter.capture(...)` in its own `try/except Exception as exc: note = f"capture_failed: {exc}"` — confirm this doesn't itself print anything beyond building the `note` string (read the surrounding code to be sure there's no `traceback.print_exc()` or unguarded re-raise nearby).

- [ ] **Step 4: Make the eval notes column render "capture declined: unparseable statement"**

Trace how `note`/`capture_failed` ends up in `ScenarioResult.notes` for this scenario — via `_grade` in `eval.py:124-154` (read the full function) and whatever assembles `ScenarioResult` from journal entries/reports. The task wants the **eval table's notes column** specifically to show `capture declined: unparseable statement` for this scenario, not the raw `capture_failed: <exception message>` text. Two implementation options depending on what's actually there after reading the code:
  - If `_grade`/the result-builder already surfaces the journal's `error`/note field verbatim, add a small translation step: when the refusal reason traces back to a capture failure (`refused_no_pre_state` with a `capture_failed:` prefixed note, per `journal.begin_action`'s `note` param docs), rewrite the displayed note to `"capture declined: unparseable statement"` specifically for scenarios where `expect_refusal=True` and outcome is `refused_no_pre_state`.
  - Prefer changing this at the point closest to where the note is already synthesized for display (likely inside `_grade` or the scenario-result assembly in `eval.py`) rather than changing the journal's stored `note` text, since the journal's stored text should stay accurate/generic for `backstep log`/`backstep show` (other CLI surfaces) — the task specifically asks for the **eval scenario row**, not the journal record.
  - Write the actual code after reading `eval.py` end to end; do not guess at variable names not yet confirmed.

- [ ] **Step 5: Add `--verbose` to the `eval` Typer command**

```python
@app.command(name="eval")
def eval_command(
    out: Path | None = typer.Option(None, "--out", "-o", help="..."),
    verbose: bool = typer.Option(False, "--verbose", help="Show full tracebacks for capture failures."),
) -> None:
    if verbose:
        logging.getLogger("backstep").setLevel(logging.DEBUG)
        logging.basicConfig(level=logging.DEBUG)
    ...
```
Match whatever logging setup convention already exists elsewhere in `cli.py` (check if `serve`/other commands configure logging already — reuse that pattern rather than inventing a second one). The effect required: with `--verbose`, the DEBUG-level `exc_info=True` log from Step 2 becomes visible (full traceback restored); without it, default output stays traceback-free.

- [ ] **Step 6: Test — default output has no traceback text**

```python
def test_eval_unparseable_sql_no_traceback_by_default(...):
    result = runner.invoke(app, ["eval"])
    assert "Traceback (most recent call last)" not in result.stdout
    assert "UndefinedTableError" not in result.stdout
    assert "capture declined: unparseable statement" in result.stdout

def test_eval_verbose_restores_traceback(caplog, ...):
    with caplog.at_level(logging.DEBUG, logger="backstep.adapters.postgres"):
        result = runner.invoke(app, ["eval", "--verbose"])
    assert any("UndefinedTableError" in r.message or r.exc_info for r in caplog.records)
```
Place in `tests/test_cli.py` (CLI-level) since it needs to invoke the actual `eval` command with real Postgres — this test requires the `requires_postgres` marker/fixture since `backstep eval` runs real scenarios against real Postgres. Confirm test placement/fixture pattern by reading `tests/test_eval.py` first (it already does an e2e smoke test of `run_eval` under `requires_postgres`).

- [ ] **Step 7: Run**

```bash
uv run pytest tests/test_cli.py tests/test_eval.py -v
uv run backstep eval
```
Confirm the eval table's `unparseable_sql` row now shows the clean note and the console has no traceback.

- [ ] **Step 8: Commit**

```bash
git add src/backstep/adapters/postgres.py src/backstep/eval.py src/backstep/cli.py tests/
git commit -m "fix: eval no longer prints raw traceback for unparseable_sql; add --verbose"
```

---

### Task 2a: Reversible type codec for the journal + Postgres fingerprinting

**This is the highest-risk task in the plan. Read `journal.py` and `postgres.py` in full (already done — contents are above) before writing code. Do not proceed to 2b/2c until 2a's codec round-trips every listed type exactly.**

**Files:**
- Create: `src/backstep/codec.py` — new module, `encode`/`decode` helpers plus the tagged-value scheme
- Modify: `src/backstep/journal.py` (lines 171-172, 214 write path; line 338-340 read path — replace `json.dumps(..., default=str)` / `json.loads(...)` with the new codec)
- Modify: `src/backstep/adapters/postgres.py` (`_fingerprint_state`, `postgres.py:144-164` — replace `json.dumps(..., default=str)` at lines 160, 163 with the codec's canonical encoder)
- Test: `tests/test_codec.py` (unit tests, no Postgres needed) — new file

**Interfaces:**
- Produces (consumed by 2b, 2c, and by journal.py/postgres.py):
  ```python
  # src/backstep/codec.py
  def encode(value: Any) -> Any:
      """Recursively convert a Python value (possibly containing Decimal,
      datetime/date/time, UUID, bytes, timedelta, asyncpg.Range) into a
      JSON-serializable structure using tagged objects for non-JSON-native
      types. Safe to pass directly to json.dumps with no `default=`."""

  def decode(value: Any) -> Any:
      """Inverse of encode: recursively walk a JSON-decoded structure and
      reconstruct tagged objects back into their original Python types."""

  def dumps(value: Any, *, sort_keys: bool = False) -> str:
      """json.dumps(encode(value), sort_keys=sort_keys, separators=(",", ":"))"""

  def loads(text: str) -> Any:
      """decode(json.loads(text))"""
  ```

- [ ] **Step 1: Design the tag scheme and enumerate every type to cover**

Required minimum (from task spec): `Decimal`, `datetime`/`date`/`time` (with tz), `UUID`, `bytes`, `Interval` (Python `datetime.timedelta` — asyncpg maps Postgres `interval` to `datetime.timedelta`), and `asyncpg.Range` types if present (check `asyncpg` for `asyncpg.Range`/`asyncpg.types.Range` — used for `int4range`/`tstzrange`/etc. columns; confirm the actual import path via `python -c "import asyncpg; print(asyncpg.Range)"` before writing the encoder for it).

Tag format: `{"__t": "<type>", "v": <json-safe representation>}`. Exact encodings:

| Python type | `__t` | `v` encoding | Decode |
|---|---|---|---|
| `decimal.Decimal` | `"decimal"` | `str(value)` — **must preserve scale**: `str(Decimal("10.50"))` → `"10.50"`, `str(Decimal("10.5"))` → `"10.5"` (Python's `Decimal.__str__` already preserves trailing zeros/scale by construction, so plain `str()` satisfies the requirement — verify with a unit test, don't assume) | `Decimal(v)` |
| `datetime.datetime` | `"datetime"` | `value.isoformat()` (preserves tz offset if aware, omits it if naive) | `datetime.fromisoformat(v)` |
| `datetime.date` | `"date"` | `value.isoformat()` | `date.fromisoformat(v)` |
| `datetime.time` | `"time"` | `value.isoformat()` (preserves tz if aware) | `time.fromisoformat(v)` |
| `uuid.UUID` | `"uuid"` | `str(value)` | `UUID(v)` |
| `bytes` | `"bytes"` | `value.hex()` | `bytes.fromhex(v)` |
| `datetime.timedelta` (Postgres interval) | `"interval"` | `value.total_seconds()` as a `str(Decimal(...))`-style exact string (avoid float precision loss — build from `(value.days, value.seconds, value.microseconds)` tuple instead: `f"{value.days}:{value.seconds}:{value.microseconds}"`) | reconstruct `timedelta(days=..., seconds=..., microseconds=...)` from the parsed tuple |
| `asyncpg.Range` | `"range"` | `{"lower": encode(value.lower), "upper": encode(value.upper), "lower_inc": value.lower_inc, "upper_inc": value.upper_inc, "empty": value.isempty}` (nested encode call so a `tstzrange`'s bounds get datetime-encoded too) | reconstruct via `asyncpg.Range(lower=decode(v["lower"]), upper=decode(v["upper"]), lower_inc=..., upper_inc=..., empty=...)` — confirm actual `asyncpg.Range.__init__` signature first |

Collision guard: a dict that legitimately contains a key literally named `"__t"` from real application data would collide with this scheme. Given this is DB row data (column names as keys), a column literally named `__t` is exceedingly unlikely but note the limitation in a one-line comment rather than building elaborate escaping (YAGNI — the task doesn't ask for that level of robustness).

- [ ] **Step 2: Write `src/backstep/codec.py`**

```python
"""Reversible JSON codec for values the journal stores: DB-native Python
types (Decimal, datetime, UUID, bytes, ...) that plain `json` can't encode
and that `json.dumps(default=str)` previously flattened irreversibly.

Both the journal's write/read paths and the Postgres adapter's fingerprint
canonicalization use `encode`/`decode` so a round trip through either is
lossless, and so two independently-serialized snapshots of the same typed
value always produce byte-identical JSON (required for fingerprint
stability — see backstep.adapters.postgres._fingerprint_state).
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

try:
    from asyncpg import Range as _AsyncpgRange
except ImportError:  # pragma: no cover
    _AsyncpgRange = None  # type: ignore[assignment,misc]


def encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {"__t": "decimal", "v": str(value)}
    if isinstance(value, datetime):
        return {"__t": "datetime", "v": value.isoformat()}
    if isinstance(value, date):
        return {"__t": "date", "v": value.isoformat()}
    if isinstance(value, time):
        return {"__t": "time", "v": value.isoformat()}
    if isinstance(value, UUID):
        return {"__t": "uuid", "v": str(value)}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"__t": "bytes", "v": bytes(value).hex()}
    if isinstance(value, timedelta):
        return {
            "__t": "interval",
            "v": f"{value.days}:{value.seconds}:{value.microseconds}",
        }
    if _AsyncpgRange is not None and isinstance(value, _AsyncpgRange):
        return {
            "__t": "range",
            "v": {
                "lower": encode(value.lower),
                "upper": encode(value.upper),
                "lower_inc": value.lower_inc,
                "upper_inc": value.upper_inc,
                "empty": value.isempty,
            },
        }
    if isinstance(value, dict):
        return {k: encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    if isinstance(value, dict):
        tag = value.get("__t")
        if tag == "decimal":
            return Decimal(value["v"])
        if tag == "datetime":
            return datetime.fromisoformat(value["v"])
        if tag == "date":
            return date.fromisoformat(value["v"])
        if tag == "time":
            return time.fromisoformat(value["v"])
        if tag == "uuid":
            return UUID(value["v"])
        if tag == "bytes":
            return bytes.fromhex(value["v"])
        if tag == "interval":
            days_s, seconds_s, micros_s = value["v"].split(":")
            return timedelta(days=int(days_s), seconds=int(seconds_s), microseconds=int(micros_s))
        if tag == "range":
            if _AsyncpgRange is None:  # pragma: no cover
                raise RuntimeError("asyncpg is required to decode a range value")
            v = value["v"]
            return _AsyncpgRange(
                lower=decode(v["lower"]),
                upper=decode(v["upper"]),
                lower_inc=v["lower_inc"],
                upper_inc=v["upper_inc"],
                empty=v["empty"],
            )
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def dumps(value: Any, *, sort_keys: bool = False) -> str:
    return json.dumps(encode(value), sort_keys=sort_keys, separators=(",", ":"))


def loads(text: str) -> Any:
    return decode(json.loads(text))
```

Before finalizing, verify `asyncpg.Range`'s actual constructor signature and attribute names (`lower`, `upper`, `lower_inc`, `upper_inc`, `isempty` vs `is_empty`) against the installed `asyncpg` version — read the installed package source (`uv run python -c "import asyncpg, inspect; print(inspect.getsource(asyncpg.Range))"` or equivalent) rather than trusting this plan's guess, and fix field names accordingly.

- [ ] **Step 3: Unit tests for the codec — `tests/test_codec.py`**

```python
import asyncpg
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from backstep.codec import decode, dumps, encode, loads


def test_decimal_scale_preserved_distinctly():
    assert encode(Decimal("10.50")) != encode(Decimal("10.5"))
    assert dumps(Decimal("10.50")) != dumps(Decimal("10.5"))
    assert decode(encode(Decimal("10.50"))) == Decimal("10.50")
    assert str(decode(encode(Decimal("10.50")))) == "10.50"  # scale, not just value equality


def test_datetime_roundtrip_with_tz():
    dt = datetime(2024, 3, 1, 12, 30, 45, 123456, tzinfo=timezone.utc)
    assert decode(encode(dt)) == dt

def test_datetime_roundtrip_naive():
    dt = datetime(2024, 3, 1, 12, 30, 45, 123456)
    assert decode(encode(dt)) == dt

def test_date_roundtrip():
    assert decode(encode(date(2024, 3, 1))) == date(2024, 3, 1)

def test_time_roundtrip_with_tz():
    t = time(12, 30, 45, 1, tzinfo=timezone.utc)
    assert decode(encode(t)) == t

def test_uuid_roundtrip():
    u = uuid4()
    assert decode(encode(u)) == u

def test_bytes_roundtrip():
    b = b"\x00\x01\xff\xfe hello"
    assert decode(encode(b)) == b

def test_interval_roundtrip():
    td = timedelta(days=3, seconds=45, microseconds=999)
    assert decode(encode(td)) == td

def test_asyncpg_range_roundtrip():
    r = asyncpg.Range(lower=1, upper=10, lower_inc=True, upper_inc=False)
    decoded = decode(encode(r))
    assert decoded.lower == r.lower
    assert decoded.upper == r.upper
    assert decoded.lower_inc == r.lower_inc
    assert decoded.upper_inc == r.upper_inc

def test_nested_dict_and_list():
    original = {"a": [Decimal("1.10"), {"b": uuid4()}], "c": None, "d": 42, "e": "text"}
    assert decode(encode(original)) == original

def test_dumps_loads_full_roundtrip():
    original = {"amount": Decimal("10.50"), "id": uuid4(), "seen": datetime.now(timezone.utc)}
    assert loads(dumps(original)) == original

def test_none_and_plain_json_types_passthrough():
    assert encode(None) is None
    assert encode(42) == 42
    assert encode("text") == "text"
    assert encode(True) is True
```

- [ ] **Step 4: Run codec unit tests**

```bash
uv run pytest tests/test_codec.py -v
```
Expected: all pass. Fix `codec.py` until they do — do not proceed to Step 5 with a failing codec.

- [ ] **Step 5: Wire the codec into `journal.py`'s write path**

Replace lines 171-172 (`begin_action`):
```python
        args_json = json.dumps(args, default=str)
        pre_state_json = None if pre_state is None else json.dumps(pre_state, default=str)
```
with:
```python
        args_json = codec.dumps(args)
        pre_state_json = None if pre_state is None else codec.dumps(pre_state)
```
And line 214 (`commit_action`):
```python
        post_state_json = None if post_state is None else json.dumps(post_state, default=str)
```
→
```python
        post_state_json = None if post_state is None else codec.dumps(post_state)
```
Add `from backstep import codec` to the imports; remove the now-unused `import json` if nothing else in the file uses it (check first — `_row_to_action`'s read path, Step 6, will still need `codec`, not `json`, so `import json` may become fully removable).

Update the module docstring (`journal.py:1-8`) and the `begin_action` docstring note about `default=str` (lines 165-169) — both currently describe the old lossy behavior and must be corrected to describe the new reversible codec, or removed if redundant with `codec.py`'s own docstring.

- [ ] **Step 6: Wire the codec into `journal.py`'s read path**

Replace `_row_to_action` (lines 338-340):
```python
        args=json.loads(row["args_json"]),
        pre_state=None if row["pre_state_json"] is None else json.loads(row["pre_state_json"]),
        post_state=None if row["post_state_json"] is None else json.loads(row["post_state_json"]),
```
→
```python
        args=codec.loads(row["args_json"]),
        pre_state=None if row["pre_state_json"] is None else codec.loads(row["pre_state_json"]),
        post_state=None if row["post_state_json"] is None else codec.loads(row["post_state_json"]),
```

- [ ] **Step 7: Wire the codec into Postgres fingerprinting (`postgres.py:144-164`)**

Replace the two `json.dumps(..., default=str)` calls in `_fingerprint_state`:
```python
    def sort_key(row: Any) -> str:
        key_values: Any = row
        if isinstance(row, dict) and pk_columns and all(c in row for c in pk_columns):
            key_values = [row[c] for c in pk_columns]
        return codec.dumps(key_values, sort_keys=True)

    ordered = sorted(rows, key=sort_key)
    canonical = codec.dumps(ordered, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```
Add `from backstep import codec` import; remove now-unused `import json` from `postgres.py` if nothing else there needs it (check `_extract_result_rows` and other functions in the file for other `json.` usages first — the exploration notes `json.loads` is used in `_extract_result_rows` at `postgres.py:123`, so `import json` likely stays).

Also check `adapters/base.py`'s `canonical_fingerprint` (the fallback path used when `state["rows"]` isn't a list, `adapters/base.py:41-48` per the exploration) — read it and apply the same `default=str` → `codec.dumps` replacement there for consistency, since it's the fallback fingerprint path and would otherwise remain lossy/unstable for non-row-shaped state.

- [ ] **Step 8: Run the full existing test suite to catch fallout**

```bash
uv run pytest tests/test_journal.py tests/adapters/test_postgres.py tests/test_reverter.py -v
```
Expected: existing tests pass, or fail in ways that reveal a test asserting the *old* stringified behavior (e.g. a test asserting `record.pre_state["amount"] == "10.50"` as a string) — if so, fix the test's assertion to expect the native `Decimal("10.50")` now that round-tripping is exact, since that's the whole point of this task. Do not weaken the new codec to keep an old test passing.

- [ ] **Step 9: Commit**

```bash
git add src/backstep/codec.py src/backstep/journal.py src/backstep/adapters/postgres.py src/backstep/adapters/base.py tests/test_codec.py
git commit -m "feat: reversible type codec for journal storage and fingerprint canonicalization

Replaces json.dumps(default=str) — lossy and non-reversible for Decimal,
datetime, UUID, bytes, interval, and range values — with a tagged-object
codec used identically on write and read, and in Postgres fingerprinting."
```

---

### Task 2b: `tests/adapters/test_type_fidelity.py` — real-Postgres round trip + fingerprint tests

**Files:**
- Create: `tests/adapters/test_type_fidelity.py`

**Interfaces:**
- Consumes: `codec` from `src/backstep/codec.py` (Task 2a), `PostgresAdapter` from `src/backstep/adapters/postgres.py`, `current_fingerprint`/`fetch_current_rows` from same, `postgres_dsn` fixture from `tests/conftest.py`, `requires_postgres` marker from `tests/pg_testing.py`. Read `tests/adapters/test_postgres.py` in full first — reuse its exact patterns for connecting/tearing down a `PostgresAdapter` against the `postgres_dsn` fixture, and its `test_fingerprint_is_stable_and_order_independent` / `test_current_fingerprint_detects_drift_and_missing_rows` tests as templates for structure (don't reinvent connection/schema setup boilerplate that already exists there).
- Produces: nothing consumed downstream, but MUST pass before Task 3/4 tests are written (per Global Constraints — Task 2 changes fingerprint canonicalization and these are the tests proving it didn't break).

- [ ] **Step 1: Read `tests/adapters/test_postgres.py` and `tests/conftest.py` in full** to confirm exact fixture usage, connection helper patterns, and how existing tests build/tear down a test table.

- [ ] **Step 2: Write the schema + seed helper**

```python
CREATE_TABLE_SQL = """
CREATE TABLE type_fidelity (
    id SERIAL PRIMARY KEY,
    amount NUMERIC(12,4) NOT NULL,
    amount_null NUMERIC(12,4),
    seen_at TIMESTAMPTZ NOT NULL,
    seen_at_null TIMESTAMPTZ,
    plain_ts TIMESTAMP NOT NULL,
    plain_ts_null TIMESTAMP,
    seen_date DATE NOT NULL,
    seen_date_null DATE,
    ext_id UUID NOT NULL,
    ext_id_null UUID,
    payload JSONB NOT NULL,
    payload_null JSONB,
    tags TEXT[] NOT NULL,
    tags_null TEXT[],
    scores INT[] NOT NULL,
    scores_null INT[],
    blob BYTEA NOT NULL,
    blob_null BYTEA,
    flag BOOLEAN NOT NULL,
    flag_null BOOLEAN
);
"""
```
Seed row 1 (non-trivial values): `amount=Decimal("1234.5678")`, `seen_at` a tz-aware datetime with non-zero microseconds, `plain_ts` a naive datetime, `seen_date` a real date, `ext_id` a real UUID, `payload={"b": 2, "a": 1}` (deliberately unordered keys — used for the key-reorder sensitivity check), `tags=["x","y"]`, `scores=[3,1,2]`, `blob=b"\x00\x01\xff"`, `flag=True`; all `_null` columns explicit non-null values too (row 1 covers the "representative non-trivial values" requirement across every column). Seed row 2: every nullable column set to SQL `NULL`, non-nullable columns given minimal valid values.

Insert via `conn.execute` with the adapter's own pool/connection (or a raw asyncpg connection opened against `postgres_dsn` — match whatever pattern `test_postgres.py` already uses for table setup, likely a fixture or helper function; reuse it, don't duplicate).

- [ ] **Step 3: Round-trip test (requirement i)**

```python
@requires_postgres
async def test_round_trip_capture_update_revert(postgres_dsn):
    # 1. create table + seed 2 rows (row1 non-trivial, row2 all-NULL nullables)
    # 2. read original values directly via asyncpg for later comparison
    # 3. adapter = await PostgresAdapter.connect(postgres_dsn)
    # 4. capture() an UPDATE statement that changes every column on row 1
    #    (simulating the "agent UPDATE") via adapter.capture("execute_sql",
    #    {"sql": "UPDATE type_fidelity SET ... WHERE id = 1"})
    # 5. actually execute that UPDATE against Postgres (capture() only reads
    #    pre-state; the caller is responsible for running the statement,
    #    matching how proxy.py's real interception flow works — read
    #    proxy.py to confirm this division of responsibility before writing
    #    the test)
    # 6. adapter.revert(...) using a constructed ActionRecord carrying the
    #    pre_state from step 4 (match ActionRecord's required fields exactly
    #    — read journal.py's ActionRecord definition, already in this plan)
    # 7. SELECT the row directly via asyncpg (NOT through the adapter) and
    #    assert every column's Python value from asyncpg equals the
    #    ORIGINAL value read in step 2 — compare as native Python objects
    #    (Decimal, datetime, UUID, bytes, list, dict, bool), not strings.
    for original_row, current_row in zip(original_rows, reverted_rows, strict=True):
        for column in original_row.keys():
            assert current_row[column] == original_row[column], (
                f"column {column}: {current_row[column]!r} != {original_row[column]!r}"
            )
```
Do this for BOTH seeded rows (the non-trivial row and the all-NULL row) — the task says "the row," singular, for the round-trip assertion structure, but since there are two seeded rows, run the UPDATE→revert→compare cycle against row 1 (the non-trivial one) as the primary round-trip proof, and separately assert (in requirement ii/iii below) that the all-NULL row's fingerprint behaves correctly too. Write actual working code once `proxy.py`'s capture/execute/revert division of responsibility is confirmed by reading it — do not guess at the ActionRecord construction without checking required fields.

- [ ] **Step 4: Fingerprint stability test (requirement ii) — parametrized per column type**

```python
@requires_postgres
@pytest.mark.parametrize("column", [
    "amount", "amount_null", "seen_at", "seen_at_null", "plain_ts", "plain_ts_null",
    "seen_date", "seen_date_null", "ext_id", "ext_id_null", "payload", "payload_null",
    "tags", "tags_null", "scores", "scores_null", "blob", "blob_null", "flag", "flag_null",
])
async def test_fingerprint_stable_under_no_change(postgres_dsn, column):
    # capture + commit an action touching the row (e.g. a no-op UPDATE
    # "SET id = id WHERE id = 1", or the same UPDATE used in Step 3 against
    # a fresh row so this test is independent), record post_fingerprint,
    # then IMMEDIATELY call current_fingerprint(adapter, entry) with no
    # intervening changes and assert equality.
    assert post_fingerprint == current_fp, (
        f"fingerprint drifted with zero changes for column {column!r} — "
        "false-positive drift bug"
    )
```
Per Global Constraints: if this fails for ANY column, STOP and fix `codec.py`/`_fingerprint_state` before writing requirement (iii) or continuing to Task 2c. Do not comment out or skip a failing case.

- [ ] **Step 5: Fingerprint sensitivity test (requirement iii)**

One test per mutation type, each asserting `current_fingerprint(...)` differs from the pre-mutation `post_fingerprint` EXCEPT the jsonb key-reorder case, which must NOT differ:

```python
async def test_fingerprint_sensitive_to_decimal_scale_change(postgres_dsn):
    # UPDATE amount FROM 1234.5678 TO 1234.5679 (smallest scale-preserving
    # change) -> fingerprint MUST differ

async def test_fingerprint_sensitive_to_microsecond_change(postgres_dsn):
    # UPDATE seen_at, adding exactly 1 microsecond -> fingerprint MUST differ

async def test_fingerprint_sensitive_to_single_byte_change(postgres_dsn):
    # UPDATE blob, flipping one byte -> fingerprint MUST differ

async def test_fingerprint_insensitive_to_jsonb_key_reorder(postgres_dsn):
    # UPDATE payload FROM {"a":1,"b":2} TO {"b":2,"a":1} (same keys/values,
    # different insertion order) -> fingerprint MUST NOT differ.
    # Postgres's jsonb type itself normalizes key order on storage (jsonb
    # is stored in a parsed binary form, not text) — confirm this is true
    # for the installed Postgres version by asserting asyncpg returns the
    # SAME Python dict (same repr) for both UPDATEs before even getting to
    # the fingerprint comparison; if Postgres's jsonb round trip already
    # normalizes order, this test is really validating that `codec.py`
    # doesn't accidentally introduce order-sensitivity (e.g. via a
    # non-sort_keys dict encoding) rather than validating jsonb itself.
    assert current_fp_before_reorder == current_fp_after_reorder
```

- [ ] **Step 6: Run — MUST NOT skip**

```bash
uv run pytest tests/adapters/test_type_fidelity.py -v
```
Expected: every test in this file actually runs (no `SKIPPED`) since `BACKSTEP_TEST_PG_DSN` is live, and all pass. If requirement (ii) fails for any type, stop, fix `codec.py`/`postgres.py`, rerun, and only proceed once green.

- [ ] **Step 7: Commit**

```bash
git add tests/adapters/test_type_fidelity.py
git commit -m "test: round-trip and fingerprint stability/sensitivity for all DB-native types"
```

---

### Task 2c: Cross-process fingerprint stability test

**Files:**
- Create or append to `tests/adapters/test_type_fidelity.py` (or a new `tests/test_codec_cross_process.py` — prefer a new small file since this test doesn't need Postgres at all, only the codec + fingerprint function, keeping it fast and independent of the DB fixture)

**Interfaces:**
- Consumes: `codec.dumps`/`_fingerprint_state` (or the codec's `dumps` directly, whichever is the actual fingerprinted representation per Task 2a) — this test does NOT need `postgres_dsn`/`requires_postgres` since it's testing serialization determinism, not DB behavior, so it should NOT be marked `requires_postgres` and must not be skippable.

- [ ] **Step 1: Write `tests/test_fingerprint_cross_process.py`**

```python
"""Guards against dict ordering or PYTHONHASHSEED randomization leaking into
fingerprint output across separate Python processes."""

from __future__ import annotations

import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backstep.adapters.postgres import _fingerprint_state


def _sample_state() -> dict:
    return {
        "pk_columns": ["id"],
        "rows": [
            {"id": 2, "amount": Decimal("10.50"), "note": "b", "ext_id": str(uuid4())},
            {"id": 1, "amount": Decimal("1234.5678"), "note": "a", "ext_id": str(uuid4())},
        ],
    }


_SUBPROCESS_SCRIPT = """
import json, sys
from backstep.adapters.postgres import _fingerprint_state

state = json.loads(sys.argv[1])
print(_fingerprint_state(state))
"""


def test_fingerprint_stable_across_processes(tmp_path: Path) -> None:
    state = _sample_state()
    # Decimal isn't JSON-native, so round it through the codec-aware encoder
    # before crossing the subprocess boundary via argv/json.
    from backstep.codec import encode
    encoded_state = encode(state)

    fp_here = _fingerprint_state(state)

    script_path = tmp_path / "fp_subprocess.py"
    script_path.write_text(_SUBPROCESS_SCRIPT, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(script_path), json.dumps(encoded_state)],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "random", **__import__("os").environ},
    )
    fp_subprocess = result.stdout.strip()

    assert fp_here == fp_subprocess
```

Note: `_fingerprint_state` expects native Python objects (e.g. `Decimal`) in `state["rows"]`, not the encoded tagged form — check its actual input contract from Task 2a's changes (it calls `codec.dumps` internally, which itself calls `encode`) before deciding whether the subprocess script should decode first. Adjust the script to call `decode(json.loads(...))` before `_fingerprint_state(...)` if that's what the real signature requires — write this against the actual Step 2a code, not this plan's guess.

- [ ] **Step 2: Run**

```bash
uv run pytest tests/test_fingerprint_cross_process.py -v
```
Expected: PASS, not skipped (no Postgres dependency).

- [ ] **Step 3: Commit**

```bash
git add tests/test_fingerprint_cross_process.py
git commit -m "test: cross-process fingerprint determinism guard"
```

---

### Task 3a/3b: `tests/test_mutation_guards.py` — prove the refusal scenarios can fail

**Files:**
- Create: `tests/test_mutation_guards.py`
- Modify: `pyproject.toml` (register `mutation` marker)
- Modify: `README.md` (document the marker in the testing section, `### Postgres-backed tests and \`backstep eval\`` around line 17-44)

**Interfaces:**
- Consumes: `PartialDriftScenario`/`UnparseableSqlScenario` from `tests/scenarios/`, `run_eval`/`_grade` from `backstep.eval`, `Reverter._check_drift` and the sqlglot parse guard (`postgres.py:206-210`) and the DELETE presence-check (`fetch_rows_at_pks`/`Reverter._check_delete_drift`) as monkeypatch targets, `REFUSAL_OUTCOMES` from `backstep.reverter`. Read `tests/scenarios/partial_drift.py`, `tests/scenarios/unparseable_sql.py`, and `reverter.py`'s `_check_delete_drift`/`_check_update_or_insert_drift` in full before writing — this plan's map is not a substitute.
- Note: no existing "DELETE reoccupation" scenario is listed among the 6 `SCENARIOS` per the exploration (`WrongWhereUpdateScenario`, `CascadingDeleteScenario`, `RunawayInsertLoopScenario`, `MixedSessionScenario`, `PartialDriftScenario`, `UnparseableSqlScenario`) — the task's 3rd guard ("DELETE reoccupation with the presence check patched out") is not necessarily tied to one of the 6 named eval scenarios; it may need to be driven directly against `Reverter`/`PostgresAdapter` (a DELETE + revert flow) rather than through `run_eval`. Confirm by grepping `tests/scenarios/` and `reverter.py`/`test_reverter.py` for "reoccup" or similar before deciding whether this guard runs through the eval grader or through `Reverter` directly — if no such eval scenario exists, build the guard as a direct `Reverter.revert_action` test (still asserting on the returned `RevertOutcome`/journal status, which is the closest equivalent to "grade" outside the eval harness) and note this deviation explicitly in the test file's docstring.

- [ ] **Step 1: Read `tests/scenarios/partial_drift.py`, `tests/scenarios/unparseable_sql.py`, `reverter.py` (`_check_drift`, `_check_delete_drift`, `_check_update_or_insert_drift`, `_revert_entry`), and `tests/test_reverter.py`'s `TestPostgresDrift` class** in full.

- [ ] **Step 2: Guard 1 — `partial_drift` with `_check_drift` patched to always return no-drift**

```python
import pytest

pytestmark = [pytest.mark.mutation, requires_postgres]  # import requires_postgres from tests.pg_testing

async def test_partial_drift_guard_disabling_drift_check_clobbers_edit(postgres_dsn, monkeypatch):
    """With drift detection disabled, partial_drift's out-of-band edit gets
    clobbered by the revert instead of triggering a refusal — proving the
    scenario's "refused (correct)" grade depends on _check_drift actually
    running, not just on the scenario's own assertions."""
    from backstep import reverter as reverter_module

    async def _always_no_drift(self, adapter, entry):
        return None  # None means "no drift" per _check_drift's contract

    monkeypatch.setattr(reverter_module.Reverter, "_check_drift", _always_no_drift)

    # Run the partial_drift scenario end-to-end (via run_eval or by driving
    # the scenario's setup/damage/revert steps directly — match whatever
    # tests/test_eval.py's e2e pattern already uses for running a single
    # scenario, to avoid re-deriving proxy/journal wiring from scratch).
    result = await _run_single_scenario(PartialDriftScenario, postgres_dsn)

    # The guard must assert on the GRADE, not just an exception:
    assert result.recovered != "refused", (
        "partial_drift graded as refused even with drift detection disabled "
        "— the mutation guard did not change the outcome, meaning this "
        "scenario is not actually testing drift refusal"
    )
    # And it must positively confirm the clobber happened (revert executed
    # over the out-of-band edit) rather than merely "not refused":
    assert result.recovered in ("full", "partial", "none")
    # Confirm via direct SQL that the out-of-band edit was overwritten:
    ...
```

The `_run_single_scenario` helper (or equivalent) needs to be either imported from `tests/test_eval.py` if it already exposes one, or written fresh by reading how `run_eval` iterates `SCENARIOS` and extracting the single-scenario execution path. Do not skip building this — it's required plumbing, not optional.

- [ ] **Step 3: Guard 2 — `unparseable_sql` with the sqlglot parse guard patched to attempt capture anyway**

```python
async def test_unparseable_sql_guard_forcing_capture_fails_or_miscaptures(postgres_dsn, monkeypatch):
    """With the sqlglot parse-failure guard bypassed (forcing capture() to
    attempt _capture_rows regardless), the scenario either raises or
    produces an incorrect capture, and grades as a failure rather than
    refused."""
    from backstep.adapters import postgres as postgres_module

    original_capture = postgres_module.PostgresAdapter.capture

    async def _force_capture_attempt(self, tool_name, args):
        # Bypass the try/except-return-None guard entirely: call the
        # dispatch logic directly without the protective wrapper, so a
        # runtime failure propagates instead of degrading to None.
        sql = postgres_module._extract_sql(args)
        statements = [s for s in __import__("sqlglot").parse(sql, dialect="postgres") if s is not None]
        statement = statements[0]
        if isinstance(statement, postgres_module.exp.Update):
            return await self._capture_rows(statement, op="update")
        return await original_capture(self, tool_name, args)

    monkeypatch.setattr(postgres_module.PostgresAdapter, "capture", _force_capture_attempt)

    result = await _run_single_scenario(UnparseableSqlScenario, postgres_dsn)

    assert result.recovered != "refused", (
        "unparseable_sql graded as refused even with the capture guard "
        "bypassed — this scenario is not actually exercising the guard"
    )
```

- [ ] **Step 4: Guard 3 — DELETE reoccupation with the presence check patched out**

Per Step 1's investigation, either wire this through an existing eval scenario (if one is found on closer reading) or drive it directly:

```python
async def test_delete_reoccupation_guard_disabling_presence_check(postgres_dsn, monkeypatch):
    """With fetch_rows_at_pks (the DELETE-reoccupation presence check)
    patched to always report 'nothing occupies these PKs', a revert that
    should refuse because an intruder row now occupies the freed primary
    key instead either PK-conflicts or silently overwrites the intruder."""
    from backstep.adapters import postgres as postgres_module

    async def _always_empty(adapter, table, pk_columns, pk_values):
        return []

    monkeypatch.setattr(postgres_module, "fetch_rows_at_pks", _always_empty)
    # Also patch the reference reverter.py holds, if it imports the name
    # directly (`from backstep.adapters.postgres import fetch_rows_at_pks`)
    # rather than referencing it via the module — check reverter.py's
    # import style and patch BOTH bindings if needed, since monkeypatching
    # only the source module doesn't affect a `from x import y` binding
    # already resolved in reverter.py's namespace.

    # 1. Seed a row, DELETE it (captured by the adapter), then INSERT a new
    #    row that reoccupies the same primary key value (the "intruder").
    # 2. Attempt Reverter.revert_action(...) on the DELETE's action id.
    # 3. Assert the outcome is NOT a refusal (REFUSAL_OUTCOMES) — it must
    #    either raise (PK conflict) or the revert `reverted` outcome must
    #    have overwritten the intruder row (assert via direct SQL that the
    #    intruder's distinguishing column is gone, replaced by the
    #    restored original row's data).
    from backstep.reverter import REFUSAL_OUTCOMES
    assert outcome.status not in REFUSAL_OUTCOMES if hasattr(outcome, "status") else outcome not in REFUSAL_OUTCOMES
```
Write the exact assertion once `Reverter.revert_action`'s actual return type is confirmed by reading `reverter.py` (likely returns something carrying a `RevertOutcome` and a note — match its real shape, don't guess field names blind).

- [ ] **Step 5: Each guard must fail loudly if the scenario still PASSES with the guard disabled**

This is already satisfied by the `assert result.recovered != "refused"` / `assert outcome not in REFUSAL_OUTCOMES` structure in Steps 2-4 — if disabling the mechanism does NOT change the outcome (i.e. the scenario still refuses correctly even with the guard bypassed), those assertions themselves fail, which is exactly "fail loudly if they PASS the scenario." Confirm each test file docstring states this explicitly per the task's requirement.

- [ ] **Step 6: Register the `mutation` marker in `pyproject.toml`**

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
markers = [
    "mutation: disables one safety mechanism via monkeypatch and asserts the affected scenario/revert now fails instead of refusing correctly; a passing scenario under a mutation guard is a test bug, not a pass",
]
```

- [ ] **Step 7: Document the marker in README's testing section**

Add to `### Postgres-backed tests and \`backstep eval\`` (or a new adjacent subsection):
```markdown
### Mutation guard tests

`tests/test_mutation_guards.py` proves the refusal machinery (drift
detection, the SQL parse guard, the DELETE-reoccupation presence check)
actually gates the outcome it claims to, rather than the passing grade
being incidental. Each test monkeypatches one safety mechanism off and
asserts the scenario's grade flips from "refused (correct)" to a failure;
a guard test that still passes with its target mechanism disabled means
the scenario isn't exercising that mechanism and is itself a bug.

Run just these:

    uv run pytest -m mutation
```

- [ ] **Step 8: Run**

```bash
uv run pytest tests/test_mutation_guards.py -v
uv run pytest -m mutation -v
```
Expected: all guard tests PASS (meaning: disabling the mechanism DID cause the expected failure/change in grade — the assertions inside each test are what "passing" means here). None skipped.

- [ ] **Step 9: Commit**

```bash
git add tests/test_mutation_guards.py pyproject.toml README.md
git commit -m "test: mutation guards proving drift/parse/presence-check refusals are load-bearing"
```

---

### Task 4a/4b: `tests/test_journal_state_machine.py`

**Files:**
- Create: `tests/test_journal_state_machine.py`

**Interfaces:**
- Consumes: `Journal` from `backstep.journal` (all methods: `begin_action`, `commit_action`, `mark_reverted`, `mark_unrevertable`, `fail_action`, `get_action`), `Reverter`/`Reverter.revert_action` from `backstep.reverter`, `REFUSAL_OUTCOMES`/`RevertOutcome` from `backstep.reverter`, `PostgresAdapter` for the real-Postgres-backed transitions. Read `reverter.py`'s full `_revert_entry` (already summarized above with line numbers) and `journal.py` (already read in full) before writing — confirm `Reverter.revert_action`'s exact public signature/return type by reading `reverter.py` end to end (the map only covers `_revert_entry`, the private worker).

- [ ] **Step 1: Read `reverter.py` end to end** (the plan has only excerpted `_revert_entry` and `_check_drift`) to get `Reverter.__init__`, `Reverter.revert_action`'s public signature, `Reverter.revert_session`, and `Reverter.check_drift` exactly.

- [ ] **Step 2: Write the 5 required transition sequences (4a)**

Each as its own test function against a real Postgres-backed row (reuse the `postgres_dsn` fixture + a simple single-table setup, similar to `tests/test_reverter.py`'s `TestPostgresDrift` class — read and reuse its harness rather than reinventing):

```python
pytestmark = [requires_postgres]

async def test_committed_drift_refusal_force_revert_reverted(postgres_dsn):
    # 1. seed row, capture+commit an UPDATE action -> assert status == "committed"
    # 2. mutate the row out-of-band (raw SQL) to induce drift
    # 3. Reverter.revert_action(action_id, force=False) -> assert outcome is
    #    a refusal (refused_drift), and re-fetch the journal entry: assert
    #    status == "committed", error/revert_note still unset (the drift
    #    branch writes nothing to the journal)
    # 4. Reverter.revert_action(action_id, force=True) -> assert outcome ==
    #    "reverted"; re-fetch: assert status == "reverted", revert_note
    #    mentions "FORCED OVER DETECTED DRIFT" (per reverter.py:364-366)
    # Assert on status, error, AND revert_note after EVERY transition step
    # (2 through 4), not only the final one.

async def test_committed_drift_refusal_manual_restore_then_plain_revert_succeeds(postgres_dsn):
    # 1. seed, capture+commit -> committed
    # 2. out-of-band mutation -> drift
    # 3. revert_action(force=False) -> refused_drift, status stays committed
    # 4. manually restore the row to its post-commit state via raw SQL
    #    (undoing the out-of-band edit by hand)
    # 5. revert_action(force=False) again (no force) -> assert this NOW
    #    succeeds ("reverted"), since drift is no longer present; assert
    #    status transitions committed -> reverted

async def test_committed_reverted_second_revert_refuses(postgres_dsn):
    # 1. seed, capture+commit -> committed
    # 2. revert_action(...) -> reverted; assert status == "reverted"
    # 3. revert_action(...) again -> assert outcome == "refused_already_reverted";
    #    assert status is STILL "reverted" (unchanged by the second attempt)

async def test_committed_refused_no_primary_key_retry_still_refuses(postgres_dsn):
    # Use a table with NO primary key for a DELETE capture (or synthesize a
    # pre_state with empty pk_columns matching reverter.py:315-331's check)
    # 1. capture+commit a DELETE against a PK-less table -> committed
    # 2. revert_action(...) -> refused_no_primary_key; assert status ==
    #    "unrevertable" (per Global Constraints: this is a PERMANENT refusal)
    # 3. revert_action(...) again (retry) -> still refuses (now via
    #    refused_not_committed, since status is already unrevertable, not
    #    "committed" — confirm the ACTUAL outcome value _revert_entry
    #    returns for a non-committed status, per reverter.py:269-277, and
    #    assert that exact value, not a guess)

async def test_committed_adapter_revert_raises_status_stays_committed_retry_possible(postgres_dsn, monkeypatch):
    # 1. seed, capture+commit -> committed
    # 2. monkeypatch PostgresAdapter.revert to raise an exception
    # 3. revert_action(...) -> confirm what _revert_entry actually does when
    #    adapter.revert() raises rather than returning ok=False (read
    #    reverter.py's exact exception handling around line 351-362 — the
    #    plan's map only covers the ok=False->"failed" path; confirm
    #    whether a raised exception is caught there too, or propagates, and
    #    write the test against the REAL behavior, adding a try/except in
    #    the test only if the real code doesn't already catch it)
    # 4. assert status remains "committed" (per task requirement) after the
    #    raise, NOT "unrevertable" or "failed"
    # 5. un-monkeypatch, revert_action(...) again -> assert this retry
    #    succeeds ("reverted")
```

For Step 5's transition (`adapter.revert() raises`), if reading `reverter.py` shows the code does NOT currently catch a raised exception from `adapter.revert()` (only handles the `ok=False` return case), this test will surface a real gap — the task requires "status remains committed and a subsequent retry is possible" as the CORRECT behavior, so if the current code lets the exception propagate uncaught out of `revert_action` without updating the journal at all, status implicitly stays "committed" (since no `mark_*` call happened) and a retry is trivially possible — confirm this is actually true by testing it, rather than assuming reverter.py needs a code change. Only modify `reverter.py` if the test reveals status is corrupted incorrectly (e.g. left in `pending` or crashes the journal) — do not add unnecessary try/except scaffolding if the existing behavior already satisfies the requirement.

- [ ] **Step 3: Write the parametrized invariant test (4b)**

```python
import pytest
from backstep.reverter import REFUSAL_OUTCOMES

TRANSIENT_REFUSALS = {"refused_drift"}
PERMANENT_REFUSALS = REFUSAL_OUTCOMES - TRANSIENT_REFUSALS
# = {"refused_not_committed", "refused_already_reverted", "refused_irreversible",
#    "refused_no_inverse", "refused_no_pre_state", "refused_no_adapter",
#    "refused_no_primary_key"}
# NOTE: confirm this set matches reverter.py's ACTUAL REFUSAL_OUTCOMES frozenset
# contents exactly (already transcribed above from the full file read) before
# hardcoding it here — import REFUSAL_OUTCOMES and TRANSIENT_REFUSALS-subtract
# rather than hand-listing PERMANENT_REFUSALS, so a newly added refusal reason
# in reverter.py automatically lands in PERMANENT_REFUSALS here and this test
# forces a conscious decision (add it to TRANSIENT_REFUSALS explicitly, or
# accept the permanent-unrevertable default) rather than silently passing.

@pytest.mark.parametrize("refusal_reason", sorted(REFUSAL_OUTCOMES))
async def test_refusal_status_invariant(postgres_dsn, refusal_reason):
    """TRANSIENT refusals (drift) must leave status == 'committed'.
    PERMANENT refusals must leave status == 'unrevertable'."""

> **Implementation note (Task 4):** as written this invariant cannot pass —
> `refused_already_reverted` and `refused_not_committed` are status
> preconditions that `_revert_entry` returns before any `mark_*` call, and 4a
> itself requires an already-reverted entry to stay `"reverted"`. The
> implemented test adds a third class, `STATUS_GUARD_REFUSALS`, asserting
> status is left exactly as found; `PERMANENT_REFUSALS` is still computed by
> subtraction so new refusal reasons default to it.
    entry = await _drive_to_refusal(postgres_dsn, refusal_reason)  # per-reason setup helper
    if refusal_reason in TRANSIENT_REFUSALS:
        assert entry.status == "committed", (
            f"{refusal_reason} is TRANSIENT but left status={entry.status!r}"
        )
    else:
        assert entry.status == "unrevertable", (
            f"{refusal_reason} is PERMANENT but left status={entry.status!r}"
        )
```

`_drive_to_refusal(postgres_dsn, refusal_reason)` needs one small setup branch per `refusal_reason` value (already-reverted needs a prior successful revert first, R4/irreversible needs a `reversibility="R4"` action, no-pre-state needs a capture-failed action, no-primary-key needs a PK-less DELETE, drift needs an out-of-band mutation, etc.) — write each branch by directly constructing journal entries via `Journal.begin_action`/`commit_action` with the right `reversibility`/`pre_state`/`adapter` values to hit each specific refusal path in `_revert_entry`, rather than always going through a full capture/execute cycle (faster, and more precisely targeted at the specific branch). Confirm each branch actually reaches the intended refusal reason by asserting on `outcome` before asserting on `status` (a two-part assertion: "did we hit the refusal we meant to test" then "did status land where the invariant says it should").

- [ ] **Step 4: Run**

```bash
uv run pytest tests/test_journal_state_machine.py -v
```
Expected: all pass, none skipped, including all parametrized cases (one per `RevertOutcome` refusal reason).

- [ ] **Step 5: Commit**

```bash
git add tests/test_journal_state_machine.py
git commit -m "test: multi-transition journal state machine + transient/permanent refusal invariant"
```

---

### Task 5: Verify and report

**Files:** none modified — this task only runs commands and reports real output.

- [ ] **Step 1: Run each command in order, capturing full output**

```bash
uv run ruff check .
uv run mypy .
uv run pytest
uv run pytest -m mutation
uv run backstep eval
uv run backstep doctor            # with backstep.toml present (use backstep.example.toml copied in)
mv backstep.toml /tmp-or-scratchpad/backstep.toml.bak   # or use a temp dir invocation instead of moving the real file
uv run backstep doctor            # with no backstep.toml present
```
(Prefer invoking `doctor --config <nonexistent path>` over moving files around, if the CLI supports a `--config` override — matches the CLI test pattern from Task 1b and avoids touching repo state destructively. Confirm via `backstep doctor --help`.)

- [ ] **Step 2: Report real numbers**

For `uv run pytest`: report exact passed/failed/skipped/error counts from pytest's summary line. Per Global Constraints, skipped should be 0 — if it isn't, identify which tests skipped and why (e.g. a stray `skipif` not covered by the live `BACKSTEP_TEST_PG_DSN`) and fix or explain, don't just report the number silently.

For `uv run backstep eval`: paste the actual table output. Confirm it reads 3 full / 1 partial / 2 refused. If Task 2's codec change altered any scenario's grade (e.g. a fingerprint that now correctly detects something the old lossy encoding masked, or vice versa), STOP — do not edit `README.md`'s hardcoded results table to match — instead report the discrepancy explicitly as a flagged item for human review, including which scenario changed and the before/after grade.

For `ruff`/`mypy`: report clean or list every remaining violation with file:line.

- [ ] **Step 3: No commit for this task** — it's a verification/reporting step, not a code change. If Step 1 surfaces a fixable issue (lint violation, type error, unexpected test failure), fix it as a small follow-up commit referencing which verification step caught it, then re-run the full sequence from Step 1 to confirm green before reporting final numbers.

---

## Self-Review Notes

- **Spec coverage:** 1a (example config + README), 1b (doctor degrade + 3 CLI tests), 1c (traceback fix + --verbose + no-traceback test), 2a (codec + wiring), 2b (round-trip/stability/sensitivity tests against real PG), 2c (cross-process test), 3a/3b (3 mutation guards + marker + docs), 4a (5 transition sequences), 4b (parametrized invariant test), 5 (verification/report) — all present as tasks above.
- **Placeholder scan:** Steps that depend on code not yet re-read in full (e.g. exact `Reverter.revert_action` signature, `proxy.py`'s capture/execute division, `tests/test_eval.py`'s single-scenario runner if any) are explicitly flagged as "read this file first, do not guess" rather than presenting fabricated signatures as fact — this is intentional given the scale of the codebase relative to what was read into this plan directly; it is a deliberate acknowledgment of what still needs confirming during implementation, not a placeholder for effort not yet spent.
- **Type/interface consistency:** `codec.encode`/`decode`/`dumps`/`loads` signatures introduced in Task 2a are used identically (same names, same module path `backstep.codec`) in Tasks 2b/2c and in the `journal.py`/`postgres.py` call sites.
