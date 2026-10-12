import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, riseStyle } from "../anim";
import { AccountsTable } from "../components/AccountsTable";
import { Caption } from "../components/Caption";
import { Terminal, typingEnd, type Seg, type TermEntry } from "../components/Terminal";
import {
  actionId,
  actionPanel,
  confirmPrompt,
  logTable,
  revertedDetail,
  revertedLabel,
  undoCaption,
} from "../data";
import { richPanel, richTable } from "../rich";
import { colors } from "../theme";

export const UNDO_FRAMES = 320;
export const TERM_WIDTH = 1776;
export const TERM_COLS = 100;
export const TERM_ROWS = 11;

/** Lines of the Rich panel _print_action_detail prints, with the long JSON fields elided. */
export const panelLines = (id: string): Seg[][] => {
  const p = actionPanel(id);
  return richPanel(
    p.title,
    [
      ...p.lines.map(([k, v]): Seg[] => [{ t: k, b: true }, { t: ` ${v}` }]),
      [{ t: "... args, pre-state, post-state, fingerprint", c: colors.textDim }],
    ],
    TERM_COLS,
  );
};

/** Turns a list of lines into entries that appear `gap` frames apart. */
export const stream = (lines: Seg[][], at: number, gap = 2): TermEntry[] =>
  lines.map((segs, i) => ({ kind: "line", at: at + i * gap, segs }));

// Cells pre-wrapped the way Rich wraps them at 100 columns.
const logLines = richTable(
  logTable.title,
  logTable.columns,
  [
    [
      ["1"],
      ["2026-10-11T14:02:17", ".481203+00:00"],
      ["execute_sql"],
      ["R1"],
      ["committed"],
      ['{"sql":"UPDATE accounts SET', 'balance = 0 WHERE id > 0"}'],
    ],
  ],
  (c) => (c === 0 || c === 1 ? colors.textMuted : undefined),
);

const logCmd = { kind: "cmd" as const, at: 8, text: "backstep log" };
const undoCmd = { kind: "cmd" as const, at: 105, text: "backstep undo ", paste: actionId };
const undoTyped = typingEnd(undoCmd);
const panelAt = undoTyped + 6;
const confirmAt = panelAt + 7 * 2 + 6;
const confirmCmd = {
  kind: "cmd" as const,
  at: confirmAt,
  text: "y",
  typeDelay: 12,
  speed: 1,
  prefix: [{ t: confirmPrompt }],
};
const revertedAt = typingEnd(confirmCmd) + 8;
export const RESTORE_AT = revertedAt + 6;

const entries: TermEntry[] = [
  logCmd,
  ...stream(logLines, typingEnd(logCmd) + 6),
  { kind: "line", at: undoCmd.at - 1, segs: [{ t: "" }] },
  undoCmd,
  ...stream(panelLines(actionId), panelAt),
  confirmCmd,
  {
    kind: "line",
    at: revertedAt,
    segs: [
      { t: revertedLabel, c: colors.green, b: true },
      { t: ` ${revertedDetail}` },
    ],
  },
];

export const Undo: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <Terminal
        entries={entries}
        width={TERM_WIDTH}
        rows={TERM_ROWS}
        style={{ marginTop: 64, ...riseStyle(enter(frame, 0), 24) }}
      />
      <AccountsTable
        width={TERM_WIDTH}
        damageAt={-120}
        restoreAt={RESTORE_AT}
        style={{ marginTop: 40, ...riseStyle(enter(frame, 5), 24) }}
      />
      <Caption lines={[undoCaption]} at={RESTORE_AT + 22} style={{ marginTop: 48 }} />
    </AbsoluteFill>
  );
};
