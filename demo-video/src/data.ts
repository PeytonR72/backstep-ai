// All on-screen copy and data lives here so wording can change without
// touching animation code.
//
// Sources:
//   accounts, SQL      tests/scenarios/wrong_where_update.py
//   documents, drift   tests/scenarios/partial_drift.py
//   CLI wording        src/backstep/cli.py (functions named per block below)
//                      src/backstep/reverter.py (outcome names, drift summary)
//                      src/backstep/adapters/postgres.py (revert detail)
//   benchmark          eval_results.json
//
// Session ids, action ids and timestamps are illustrative examples in the
// real format (uuid4, ISO 8601 UTC). They are not measurements.

// -- Scene 1 and 5 -----------------------------------------------------------

export type AccountRow = { id: number; owner: string; balance: number };

export const accounts: AccountRow[] = [
  { id: 1, owner: "alice", balance: 100 },
  { id: 2, owner: "bob", balance: 250 },
  { id: 3, owner: "carol", balance: 75 },
  { id: 4, owner: "dave", balance: 500 },
];

export const agentTask = "# task: set alice's balance to 0";
export const wrongSql = "UPDATE accounts SET balance = 0 WHERE id > 0";
// What Postgres reports for the statement above.
export const wrongSqlResult = "UPDATE 4";

export const hookCaption = [
  "Your agent meant to change one row.",
  "It changed all of them.",
];

// -- Scene 2 -----------------------------------------------------------------

export const problemLines = [
  "AI agents now have write access to real systems.",
  "Mistakes are not rare. Recovery usually is.",
];

// -- Scene 3 and 8 -----------------------------------------------------------

export const productName = "Backstep";
export const tagline = "An undo button for AI agents.";
export const repoUrl = "github.com/PeytonR72/backstep-ai";
export const stack = ["Python", "MCP", "Postgres", "asyncio", "Claude API"];

// -- Scene 4 -----------------------------------------------------------------

export const flowNodes = {
  agent: { title: "AI agent", sub: "any MCP client" },
  proxy: { title: "Backstep", sub: "proxy" },
  server: { title: "MCP server", sub: "your tools" },
  db: { title: "Database", sub: "Postgres" },
  journal: { title: "Journal", sub: "append only" },
};

export const howItWorksCaptions = [
  "Sits between your agent and its tools.",
  "Snapshots data before every write.",
  "Works with any MCP client. No agent changes.",
];

// -- Scene 5 -----------------------------------------------------------------

export const sessionId = "7f3c9a2e-41d6-4b8a-9e0f-2c5d81a6b743";
export const actionId = "3b1f9c4e-8a27-4d0e-9f61-52c7e0a1d8b3";

// _print_log_table: title and columns.
export const logTable = {
  title: `backstep log: session ${sessionId}`,
  columns: ["Seq", "Time", "Tool", "Class", "Status", "Args"],
  rows: [
    [
      "1",
      "2026-10-11T14:02:17.481203+00:00",
      "execute_sql",
      "R1",
      "committed",
      `{"sql":"${wrongSql}"}`,
    ],
  ],
};

// _print_action_detail: the first lines of the panel (the rest is elided).
export const actionPanel = (id: string, status = "committed") => ({
  title: `Action ${id}`,
  lines: [
    ["Tool:", "execute_sql (server postgres)"],
    ["Reversibility:", "R1"],
    ["Adapter:", "postgres"],
    ["Status:", status],
  ] as [string, string][],
});

// typer.confirm("Proceed?") and _print_revert_result for outcome "reverted".
export const confirmPrompt = "Proceed? [y/N]: ";
export const revertedLabel = "Reverted:";
export const revertedDetail = "restored 4 row(s) in public.accounts";

export const undoCaption = "One command. Exact original state.";

// -- Scene 6 -----------------------------------------------------------------

export const driftActionId = "9d2e6a10-5c3b-4f7e-a8d4-0b6e93f1c25a";

export const documentRow = { id: 1, title: "Q3 plan" };
export const driftSteps = [
  { label: "The agent archives it", status: "archived", note: "through Backstep, journaled" },
  { label: "A teammate edits it", status: "deleted", note: "directly in the database" },
];
export const documentInitialStatus = "draft";

// _print_revert_result (refusal branch) and _print_drift_table.
// Summary text comes from reverter._build_drift_detail.
export const driftSummary = "drift detected on public.documents: 1 row(s) changed";
export const refusedLabel = "Refused (refused_drift):";
export const driftLabel = "Drift detected:";
export const driftTable = {
  title: "Changed rows",
  columns: ["Primary key", "Column", "Old value", "New value"],
  rows: [['{"id": 1}', "status", "archived", "deleted"]],
};

export const refusalCaption = [
  "If someone else changed the data since,",
  "Backstep refuses rather than overwrite their work.",
];
export const keptLabel = "Teammate's edit kept";

// -- Scene 7 -----------------------------------------------------------------

export type Grade = "full" | "partial" | "refused";

// eval_results.json, in README order. Times are time_to_recovery_ms, rounded.
export const benchmark: { label: string; detail: string; grade: Grade; time: string }[] = [
  { label: "Wrong WHERE clause", detail: "UPDATE matched every row", grade: "full", time: "28 ms" },
  { label: "Runaway insert loop", detail: "200 INSERTs from a stuck loop", grade: "full", time: "2.7 s" },
  { label: "Mixed session", detail: "5 writes undone together", grade: "full", time: "87 ms" },
  { label: "Cascading delete", detail: "ON DELETE CASCADE children", grade: "partial", time: "37 ms" },
  { label: "Edited after the agent", detail: "Another process changed the row", grade: "refused", time: "3 ms" },
  { label: "Unparseable SQL", detail: "Could not be captured safely", grade: "refused", time: "17 ms" },
];

export const gradeLabel: Record<Grade, string> = {
  full: "Full recovery",
  partial: "Partial recovery",
  refused: "Refused correctly",
};

export const benchmarkHeading = "Recovery benchmark";
export const benchmarkSummary = "3 full. 1 partial. 2 correct refusals.";
export const benchmarkSub = "Graded against a real Postgres database.";
