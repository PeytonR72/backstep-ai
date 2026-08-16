"""Backstep command-line interface."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from backstep.adapters.base import AdapterRegistry
from backstep.adapters.postgres import PostgresAdapter
from backstep.bootstrap import InitResult
from backstep.bootstrap import run_init as run_init_bootstrap
from backstep.config import load_config
from backstep.doctor import DoctorReport
from backstep.doctor import run_doctor as run_doctor_checks
from backstep.eval import DEFAULT_RESULTS_PATH, EvalReport
from backstep.eval import run_eval as run_eval_scenarios
from backstep.eval import write_report as write_eval_report
from backstep.journal import ActionRecord, Journal, SessionSummary
from backstep.proxy import serve as run_proxy
from backstep.reverter import DriftDetail, Reverter, RevertResult, SessionRevertReport

app = typer.Typer(
    name="backstep",
    help="Backstep: an undo button for AI agents. Proxies MCP servers and journals write ops.",
    no_args_is_help=True,
)
console = Console()

_CONFIG_OPTION = typer.Option("backstep.toml", "--config", "-c", help="Path to backstep.toml")
_VALID_STATUSES = ("pending", "committed", "failed", "reverted", "unrevertable")

_REVERSIBILITY_STYLES = {"R3": "yellow", "R4": "bold red"}
_STATUS_STYLES = {"failed": "bold red", "unrevertable": "bold red"}


@app.command()
def serve(
    config: str = typer.Option("backstep.toml", "--config", "-c", help="Path to backstep.toml"),
    allow_irreversible: bool = typer.Option(
        False,
        "--allow-irreversible",
        help=(
            "Allow R4 and require_confirmation tools to run without per-call human "
            "approval (they are still journaled as unrevertable)"
        ),
    ),
) -> None:
    """Start the MCP proxy, journaling write operations from upstream servers.

    Speaks MCP over stdio, so nothing but the protocol may touch stdout here.
    """
    asyncio.run(run_proxy(config, allow_irreversible=allow_irreversible))


@app.command()
def init(
    config: str = typer.Option("backstep.toml", "--config", "-c", help="Path to backstep.toml"),
    force: bool = typer.Option(
        False,
        "--force",
        help="Overwrite a server's existing tool classifications instead of skipping it",
    ),
) -> None:
    """Discover upstream tools and propose reversibility classifications via an LLM.

    Proposals are written into `backstep.toml` for review; nothing here is
    trusted automatically. Requires ANTHROPIC_API_KEY in the environment.
    """
    result = asyncio.run(run_init_bootstrap(config, force=force))
    _print_init_summary(result)


def _print_init_summary(result: InitResult) -> None:
    if result.skipped_servers:
        console.print(
            "[yellow]Skipped (already has tool policies; pass --force to overwrite):[/yellow] "
            f"{', '.join(result.skipped_servers)}"
        )

    if not result.classifications:
        console.print("[yellow]No tools were classified.[/yellow]")
        return

    info_by_name = {info.exposed_name: info for info in result.tool_infos}
    table = Table(title="backstep init: proposed reversibility classifications")
    table.add_column("Server")
    table.add_column("Tool")
    table.add_column("Reversibility")
    table.add_column("Confidence")
    table.add_column("Reasoning")

    for name in sorted(result.classifications):
        classification = result.classifications[name]
        info = info_by_name[name]
        style = "bold red" if classification.reversibility in ("R3", "R4") else None
        table.add_row(
            info.server_name,
            name,
            classification.reversibility,
            f"{classification.confidence:.2f}",
            classification.reasoning,
            style=style,
        )

    console.print(table)
    console.print(
        "\n[bold]These are proposals, not guarantees.[/bold] "
        "Review backstep.toml — especially any R3/R4 entries above — before trusting them."
    )


@app.command(name="eval")
def eval_scenarios(
    out: str = typer.Option(
        DEFAULT_RESULTS_PATH, "--out", "-o", help="Path to write the JSON eval report"
    ),
) -> None:
    """Run the scripted recovery-benchmark scenarios against a throwaway Postgres.

    For each scenario: seeds known-good data, damages it through a real
    backstep proxy exactly as a misbehaving agent would, attempts to revert
    everything, and grades whether recovery was full, partial, none, or a
    correct refusal. Requires Docker and the dev dependencies (this is a
    benchmarking tool, not something an installed backstep needs at runtime).
    """
    report = asyncio.run(run_eval_scenarios())
    path = write_eval_report(report, out)
    _print_eval_table(report)
    console.print(f"\nWrote {path}")


def _print_eval_table(report: EvalReport) -> None:
    table = Table(title="backstep eval: recovery benchmark")
    table.add_column("Scenario")
    table.add_column("Class")
    table.add_column("Recovered")
    table.add_column("Time to recovery (ms)", justify="right")
    table.add_column("Notes")

    style_by_recovery = {
        "full": "green",
        "partial": "yellow",
        "none": "bold red",
        "refused": "cyan",
    }

    for result in report.results:
        label: str = result.recovered
        if result.expect_refusal and result.recovered == "refused":
            label = "refused (correct)"
        elif result.expect_refusal:
            label = f"{result.recovered} (expected a refusal!)"
        table.add_row(
            result.name,
            result.reversibility_class,
            label,
            f"{result.time_to_recovery_ms:.1f}",
            result.notes,
            style=style_by_recovery.get(result.recovered),
        )

    console.print(table)
    summary = report.summary
    console.print(
        f"\n{summary['full']} full, {summary['partial']} partial, {summary['none']} none, "
        f"{summary['refused']} correctly refused — out of {len(report.results)} scenarios."
    )
    console.print(
        "[dim]Rows marked 'refused (correct)' are designed to fail safe: refusing to "
        "revert is the pass condition there, not a shortcoming.[/dim]"
    )


# -- sessions ---------------------------------------------------------------


@app.command()
def sessions(
    config: str = _CONFIG_OPTION,
    limit: int = typer.Option(20, "--limit", "-n", help="Number of sessions to show"),
) -> None:
    """List recorded sessions and how many of their actions are still undoable."""
    asyncio.run(_sessions_async(config, limit))


async def _sessions_async(config_path: str, limit: int) -> None:
    config = load_config(config_path)
    journal = Journal()
    await journal.open(config.journal_path)
    try:
        rows = await journal.list_sessions(limit=limit)
    finally:
        await journal.close()
    _print_sessions_table(rows)


def _print_sessions_table(rows: list[SessionSummary]) -> None:
    if not rows:
        console.print("[yellow]No sessions recorded yet.[/yellow]")
        return
    table = Table(title="backstep sessions")
    table.add_column("ID")
    table.add_column("Started")
    table.add_column("Client")
    table.add_column("Label")
    table.add_column("Actions", justify="right")
    table.add_column("Undoable", justify="right")
    for row in rows:
        table.add_row(
            row.id,
            row.started_at,
            row.client_name or "",
            row.label or "",
            str(row.action_count),
            str(row.committed_count),
        )
    console.print(table)


# -- log ----------------------------------------------------------------


@app.command()
def log(
    session: str = typer.Option(
        None, "--session", "-s", help="Session ID; defaults to the latest session"
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="Number of journal entries to show"),
    status: str = typer.Option(
        None, "--status", help=f"Filter by status ({', '.join(_VALID_STATUSES)})"
    ),
    config: str = _CONFIG_OPTION,
) -> None:
    """Show journaled operations for a session, most recent first."""
    if status is not None and status not in _VALID_STATUSES:
        console.print(
            f"[bold red]Error:[/bold red] --status must be one of {', '.join(_VALID_STATUSES)}"
        )
        raise typer.Exit(1)
    asyncio.run(_log_async(config, session, limit, status))


async def _log_async(
    config_path: str, session_id: str | None, limit: int, status: str | None
) -> None:
    config = load_config(config_path)
    journal = Journal()
    await journal.open(config.journal_path)
    try:
        resolved_session = session_id or await journal.latest_session()
        if resolved_session is None:
            console.print("[yellow]No sessions recorded yet.[/yellow]")
            return
        actions = await journal.list_actions(
            session_id=resolved_session,
            limit=limit,
            status=status,  # type: ignore[arg-type]
        )
    finally:
        await journal.close()
    _print_log_table(resolved_session, actions)


def _arg_summary(args: Any, width: int = 60) -> str:
    text = json.dumps(args, default=str, separators=(",", ":"))
    return text if len(text) <= width else text[: width - 1] + "…"


def _print_log_table(session_id: str, actions: list[ActionRecord]) -> None:
    table = Table(title=f"backstep log: session {session_id}")
    table.add_column("Seq", justify="right")
    table.add_column("Time")
    table.add_column("Tool")
    table.add_column("Class")
    table.add_column("Status")
    table.add_column("Args")

    if not actions:
        console.print("[yellow]No matching journal entries.[/yellow]")
        return

    for action in actions:
        class_style = _REVERSIBILITY_STYLES.get(action.reversibility, "")
        status_style = _STATUS_STYLES.get(action.status, "")
        table.add_row(
            str(action.seq),
            action.executed_at,
            action.tool_name,
            Text(action.reversibility, style=class_style),
            Text(action.status, style=status_style),
            _arg_summary(action.args),
        )
    console.print(table)


# -- show -----------------------------------------------------------------


@app.command()
def show(
    action_id: str = typer.Argument(..., help="Journal action ID"),
    config: str = _CONFIG_OPTION,
) -> None:
    """Show full detail for one journaled action, including a live drift check."""
    asyncio.run(_show_async(config, action_id))


async def _show_async(config_path: str, action_id: str) -> None:
    config = load_config(config_path)
    journal = Journal()
    await journal.open(config.journal_path)
    pg_adapter: PostgresAdapter | None = None
    try:
        try:
            entry = await journal.get_action(action_id)
        except KeyError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(1) from exc

        drift: DriftDetail | None = None
        if entry.status == "committed" and entry.adapter is not None:
            registry = AdapterRegistry()
            if entry.adapter == "postgres":
                try:
                    pg_adapter = await PostgresAdapter.from_config(config)
                    registry.register(pg_adapter)
                except Exception as exc:
                    console.print(
                        f"[yellow]Could not connect the postgres adapter for a live drift "
                        f"check: {exc}[/yellow]"
                    )
            if pg_adapter is not None:
                drift = await Reverter(journal, registry).check_drift(action_id)
    finally:
        if pg_adapter is not None:
            await pg_adapter.close()
        await journal.close()

    _print_action_detail(entry, drift)


def _print_action_detail(entry: ActionRecord, drift: DriftDetail | None) -> None:
    lines = [
        f"[bold]Tool:[/bold] {entry.tool_name} (server {entry.server_name})",
        f"[bold]Reversibility:[/bold] {entry.reversibility}",
        f"[bold]Adapter:[/bold] {entry.adapter or '(none)'}",
        f"[bold]Status:[/bold] {entry.status}",
        f"[bold]Executed at:[/bold] {entry.executed_at}",
        f"[bold]Args:[/bold] {json.dumps(entry.args, default=str, indent=2)}",
        f"[bold]Pre-state:[/bold] {json.dumps(entry.pre_state, default=str, indent=2)}",
        f"[bold]Post-state:[/bold] {json.dumps(entry.post_state, default=str, indent=2)}",
        f"[bold]Post-fingerprint:[/bold] {entry.post_fingerprint or '(none)'}",
    ]
    if entry.revert_note:
        lines.append(f"[bold]Revert note:[/bold] {entry.revert_note}")
    console.print(Panel("\n".join(lines), title=f"Action {entry.id}"))

    if drift is not None:
        _print_drift_table(drift)


def _print_drift_table(drift: DriftDetail) -> None:
    console.print(f"[bold yellow]Drift detected:[/bold yellow] {drift.summary}")
    if drift.changed_rows:
        table = Table(title="Changed rows")
        table.add_column("Primary key")
        table.add_column("Column")
        table.add_column("Old value")
        table.add_column("New value")
        for row_drift in drift.changed_rows:
            for change in row_drift.changes:
                table.add_row(
                    json.dumps(row_drift.pk, default=str),
                    change.column,
                    str(change.old),
                    str(change.new),
                )
        console.print(table)
    if drift.disappeared_rows:
        console.print("[bold yellow]Disappeared rows:[/bold yellow]")
        for row in drift.disappeared_rows:
            console.print(f"  {json.dumps(row, default=str)}")
    if drift.occupied_rows:
        console.print(
            "[bold yellow]Primary key(s) now occupied by different data "
            "(what a revert would overwrite):[/bold yellow]"
        )
        for row in drift.occupied_rows:
            console.print(f"  {json.dumps(row, default=str)}")


# -- undo -------------------------------------------------------------------


@app.command()
def undo(
    action_id: str = typer.Argument(..., help="Journal action ID to revert"),
    force: bool = typer.Option(
        False, "--force", help="Revert even over drift detected since this action ran"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    config: str = _CONFIG_OPTION,
) -> None:
    """Revert a single journaled action."""
    asyncio.run(_undo_async(config, action_id, force=force, yes=yes))


async def _undo_async(config_path: str, action_id: str, *, force: bool, yes: bool) -> None:
    config = load_config(config_path)
    journal = Journal()
    await journal.open(config.journal_path)
    pg_adapter: PostgresAdapter | None = None
    try:
        try:
            entry = await journal.get_action(action_id)
        except KeyError as exc:
            console.print(f"[bold red]Error:[/bold red] {exc}")
            raise typer.Exit(1) from exc

        registry = AdapterRegistry()
        if entry.adapter == "postgres":
            try:
                pg_adapter = await PostgresAdapter.from_config(config)
                registry.register(pg_adapter)
            except Exception as exc:
                console.print(f"[bold red]Could not connect the postgres adapter:[/bold red] {exc}")
                raise typer.Exit(1) from exc

        reverter = Reverter(journal, registry)
        _print_action_detail(entry, None)

        if not _confirm_destructive(action_id, force=force, yes=yes):
            console.print("[yellow]Aborted; nothing was reverted.[/yellow]")
            raise typer.Exit(0)

        result = await reverter.revert_action(action_id, force=force)
    finally:
        if pg_adapter is not None:
            await pg_adapter.close()
        await journal.close()

    _print_revert_result(result)
    if not result.ok:
        raise typer.Exit(1)


def _confirm_destructive(identifier: str, *, force: bool, yes: bool) -> bool:
    """Shared undo/undo-session confirmation: verbatim id retype when forcing without --yes."""
    if yes:
        return True
    if force:
        typed = typer.prompt(
            f"--force may overwrite drift detected since this ran. "
            f"Type the id to confirm ({identifier})"
        )
        if typed != identifier:
            console.print("[yellow]Confirmation did not match.[/yellow]")
            return False
        return True
    return typer.confirm("Proceed?")


def _print_revert_result(result: RevertResult) -> None:
    if result.outcome == "reverted":
        console.print(f"[bold green]Reverted:[/bold green] {result.detail}")
        return
    if result.outcome == "failed":
        console.print(f"[bold red]Failed:[/bold red] {result.detail}")
        return
    # Refusals are correct, conservative behavior — a warning, not an error.
    console.print(f"[bold yellow]Refused ({result.outcome}):[/bold yellow] {result.detail}")
    if result.drift is not None:
        _print_drift_table(result.drift)


# -- undo-session -------------------------------------------------------


@app.command(name="undo-session")
def undo_session(
    session_id: str = typer.Argument(
        None, help="Session ID to revert; defaults to the latest session"
    ),
    force: bool = typer.Option(
        False, "--force", help="Revert even over drift detected since actions ran"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt"),
    config: str = _CONFIG_OPTION,
) -> None:
    """Revert every committed action in a session, most-recent-first."""
    asyncio.run(_undo_session_async(config, session_id, force=force, yes=yes))


async def _undo_session_async(
    config_path: str, session_id: str | None, *, force: bool, yes: bool
) -> None:
    config = load_config(config_path)
    journal = Journal()
    await journal.open(config.journal_path)
    pg_adapter: PostgresAdapter | None = None
    try:
        resolved_session = session_id or await journal.latest_session()
        if resolved_session is None:
            console.print("[yellow]No sessions recorded yet.[/yellow]")
            return

        registry = AdapterRegistry()
        try:
            pg_adapter = await PostgresAdapter.from_config(config)
            registry.register(pg_adapter)
        except Exception:
            pass  # actions needing it will refuse cleanly with "no adapter"

        reverter = Reverter(journal, registry)
        planned = await reverter.plan(resolved_session)
        if not planned:
            console.print(
                f"[yellow]No undoable (committed) actions in {resolved_session}.[/yellow]"
            )
            return

        _print_plan(resolved_session, planned)

        if not _confirm_destructive(resolved_session, force=force, yes=yes):
            console.print("[yellow]Aborted; nothing was reverted.[/yellow]")
            raise typer.Exit(0)

        report = await reverter.revert_session(resolved_session, force=force)
    finally:
        if pg_adapter is not None:
            await pg_adapter.close()
        await journal.close()

    _print_session_report(report)
    if report.still_changed or report.skipped_with_reason:
        raise typer.Exit(1)


def _print_plan(session_id: str, planned: list[ActionRecord]) -> None:
    console.print(
        f"[bold]Plan for session {session_id}[/bold] "
        f"(reverse order, {len(planned)} action(s)):"
    )
    for i, action in enumerate(planned, start=1):
        summary = _arg_summary(action.args)
        console.print(f"  {i}. [{action.reversibility}] {action.tool_name} — {summary}")


def _print_session_report(report: SessionRevertReport) -> None:
    table = Table(title=f"backstep undo-session: {report.session_id}")
    table.add_column("Action ID")
    table.add_column("Tool")
    table.add_column("Outcome")
    table.add_column("Detail")

    for result in report.reverted:
        table.add_row(
            result.action_id, result.tool_name, result.outcome, result.detail, style="green"
        )
    for result in report.skipped_with_reason:
        table.add_row(
            result.action_id,
            result.tool_name,
            result.outcome,
            result.detail,
            style="bold yellow",
        )
    for action in report.still_changed:
        table.add_row(
            action.id, action.tool_name, "still_changed", "not attempted", style="bold red"
        )

    console.print(table)
    console.print(
        f"\n{len(report.reverted)} reverted, {len(report.skipped_with_reason)} refused/failed, "
        f"{len(report.still_changed)} left untouched."
    )
    if report.skipped_with_reason or report.still_changed:
        console.print(
            "[yellow]Stopping was the correct call here: continuing past a refusal or "
            "failure could have compounded the problem.[/yellow]"
        )


# -- doctor -----------------------------------------------------------------


@app.command()
def doctor(config: str = _CONFIG_OPTION) -> None:
    """Check that a backstep.toml, its upstream servers, and its adapters are healthy."""
    report = asyncio.run(run_doctor_checks(config))
    _print_doctor_report(report)
    if not report.ok:
        raise typer.Exit(1)


_DOCTOR_STATUS_TEXT = {
    "passed": Text("PASSED", style="green"),
    "failed": Text("FAILED", style="bold red"),
    "skipped": Text("SKIPPED", style="yellow"),
}


def _print_doctor_report(report: DoctorReport) -> None:
    table = Table(title="backstep doctor")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Detail")
    for check in report.checks:
        status_text = _DOCTOR_STATUS_TEXT[check.status]
        table.add_row(check.name, status_text, check.detail)
    console.print(table)

    if report.unclassified_tools:
        console.print(
            "\n[yellow]Tools with no entry in backstep.toml (falling back to "
            "unknown_tool_behavior):[/yellow]"
        )
        for tool in report.unclassified_tools:
            console.print(f"  {tool.server_name}: {tool.tool_name}")
        console.print("[dim]Run `backstep init` to propose classifications for these.[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
