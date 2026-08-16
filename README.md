# backstep-ai

Backstep gives AI agents an undo button by recording what data looked like before the agent changed it. It runs as a proxy between an MCP (Model Context Protocol) client and one or more upstream MCP servers, journaling the state affected by write operations so that any change can later be reviewed and reverted.

## Quickstart

1. Copy `backstep.example.toml` to `backstep.toml` and fill in your upstream
   server command, args, and Postgres DSN.
2. Run `uv run backstep doctor` to verify config, journal, and adapter
   connectivity before pointing a real MCP client at `backstep serve`.

## Development

This project is managed with [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run backstep --help
uv run pytest
uv run ruff check .
uv run mypy src
```

### Postgres-backed tests and `backstep eval`

The Postgres adapter tests, the reverter's drift tests, and `backstep eval`
all need a real Postgres. Two ways to give them one:

- **Docker (default, zero setup)** — leave `BACKSTEP_TEST_PG_DSN` unset and
  these tests spin up a throwaway [testcontainers](https://testcontainers-python.readthedocs.io/)
  Postgres automatically (one container per test session / per `eval` run).
  Requires a working Docker daemon; the affected tests are skipped, not
  failed, when one isn't available.
- **An already-running Postgres (faster local iteration, no Docker)** — set
  `BACKSTEP_TEST_PG_DSN` to point at it:

  ```sh
  export BACKSTEP_TEST_PG_DSN="postgresql://postgres:postgres@localhost:5432/postgres"
  uv run pytest
  uv run backstep eval
  ```

  Every test still gets its own disposable schema (`CREATE SCHEMA`, used as
  that connection's `search_path`, `DROP SCHEMA ... CASCADE` afterwards) —
  schemas are never reused across tests, so nothing leaks between runs.
  `backstep eval` does the same with a single schema named
  `backstep_eval_<timestamp>` for the whole run, dropped when it finishes.

Run `backstep doctor` to see which mode is currently active (look for the
`eval:postgres-mode` row).

## Recovery benchmark

Passing tests prove backstep's code is correct. They don't prove it actually
saves you when an agent does something dumb. `backstep eval` (`tests/scenarios/`)
is a scripted suite of "agent goes wrong" scenarios — a runaway UPDATE, a
cascading DELETE, a stuck insert loop, a mixed multi-statement session, an
out-of-band edit racing an undo, a statement backstep can't safely capture —
run end to end against a real, throwaway Postgres through the actual proxy,
interceptor, journal, and Postgres adapter. Each scenario is graded on how
well backstep recovered, not just whether the code ran:

- **full** — the database matches the pre-damage state exactly.
- **partial** — reverting changed something but not everything (e.g.
  `cascading_delete`: the parent row restores, but Postgres's own
  `ON DELETE CASCADE` destroyed the children in the same statement backstep
  captured, before backstep ever saw them individually — a real limit of
  statement-level capture, honestly reported rather than hidden).
- **none** — reverting made no difference.
- **refused (correct)** — backstep detected drift or an uncapturable
  statement and declined to touch anything, rather than guessing. For
  `partial_drift` and `unparseable_sql`, refusing *is* the pass condition —
  reverting anyway would be the failure.

Run it yourself with `uv run backstep eval` — requires Docker, or set
`BACKSTEP_TEST_PG_DSN` to point at an already-running Postgres instead (see
[Postgres-backed tests and `backstep eval`](#postgres-backed-tests-and-backstep-eval)
above). Latest run:

| Scenario | Class | Recovered | Time to recovery (ms) | Notes |
|---|---|---|---|---|
| wrong_where_update | R1 | full | 7.80 | reverted 1 action(s); state matches the pre-damage baseline |
| cascading_delete | R2 | partial | 12.46 | reverted 1 action(s), but the result doesn't fully match the pre-damage baseline |
| runaway_insert_loop | R1 | full | 1083.73 | reverted 200 action(s); state matches the pre-damage baseline |
| mixed_session | R2 | full | 31.99 | reverted 5 action(s); state matches the pre-damage baseline |
| partial_drift | R1 | refused (correct) | 1.25 | drift detected — an out-of-band edit changed the row after backstep's copy of it, so an automatic revert would have clobbered that change |
| unparseable_sql | R2 | refused (correct) | 5.36 | no pre_state was captured — a CTE-heavy statement `capture()` can't safely rewrite into a standalone SELECT |

**3 full, 1 partial, 0 none, 2 correctly refused — out of 6 scenarios.**
Full machine-readable output: [`eval_results.json`](eval_results.json).
