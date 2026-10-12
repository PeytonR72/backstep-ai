import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, mixColor, ramp, riseStyle } from "../anim";
import { Caption } from "../components/Caption";
import { Terminal, typingEnd, type TermEntry } from "../components/Terminal";
import {
  documentInitialStatus,
  documentRow,
  driftActionId,
  driftLabel,
  driftSteps,
  driftSummary,
  driftTable,
  keptLabel,
  refusalCaption,
  refusedLabel,
} from "../data";
import { richTable } from "../rich";
import { colors, fonts } from "../theme";
import { panelLines, stream, TERM_ROWS, TERM_WIDTH } from "./Undo";

export const REFUSAL_FRAMES = 290;

const STEP_AT = [4, 26, 54];
const undoCmd = { kind: "cmd" as const, at: 80, text: "backstep undo -y ", paste: driftActionId };
const panelAt = typingEnd(undoCmd) + 6;
const refusedAt = panelAt + 7 * 2 + 6;
const tableAt = refusedAt + 10;
const VERDICT_AT = tableAt + 14;

const driftLines = richTable(
  driftTable.title,
  driftTable.columns,
  driftTable.rows.map((r) => r.map((cell) => [cell])),
);

const entries: TermEntry[] = [
  undoCmd,
  ...stream(panelLines(driftActionId), panelAt),
  {
    kind: "line",
    at: refusedAt,
    segs: [{ t: refusedLabel, c: colors.termYellow, b: true }, { t: ` ${driftSummary}` }],
  },
  {
    kind: "line",
    at: refusedAt + 5,
    segs: [{ t: driftLabel, c: colors.termYellow, b: true }, { t: ` ${driftSummary}` }],
  },
  ...stream(driftLines, tableAt),
];

const CARD_H = 160;

const Card: React.FC<{
  at: number;
  eyebrow: string;
  value: React.ReactNode;
  note?: string;
  tone?: string;
  active?: number;
}> = ({ at, eyebrow, value, note, tone = colors.text, active = 0 }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        flex: 1,
        height: CARD_H,
        borderRadius: 12,
        padding: "18px 24px",
        background: colors.surface,
        border: `1px solid ${mixColor(colors.border, tone, active * 0.7)}`,
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        ...riseStyle(enter(frame, at), 20),
      }}
    >
      <div style={{ fontFamily: fonts.sans, fontSize: 26, color: colors.textMuted }}>{eyebrow}</div>
      <div style={{ fontFamily: fonts.mono, fontSize: 42, fontWeight: 500, color: tone, lineHeight: 1 }}>
        {value}
      </div>
      <div style={{ fontFamily: fonts.sans, fontSize: 22, color: colors.textDim }}>{note ?? " "}</div>
    </div>
  );
};

const Chevron: React.FC<{ at: number }> = ({ at }) => {
  const frame = useCurrentFrame();
  return (
    <svg width="28" height="28" viewBox="0 0 24 24" style={{ opacity: ramp(frame, at, at + 10), flexShrink: 0 }}>
      <path d="M9 5l7 7-7 7" stroke={colors.textDim} strokeWidth="2.4" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
};

export const Refusal: React.FC = () => {
  const frame = useCurrentFrame();
  const verdict = enter(frame, VERDICT_AT);
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div style={{ width: TERM_WIDTH, marginTop: 52, display: "flex", alignItems: "center", gap: 14 }}>
        <Card
          at={STEP_AT[0]}
          eyebrow={`documents · ${documentRow.title}`}
          value={documentInitialStatus}
          note="status column"
        />
        <Chevron at={STEP_AT[1]} />
        <Card
          at={STEP_AT[1]}
          eyebrow={driftSteps[0].label}
          value={driftSteps[0].status}
          note={driftSteps[0].note}
        />
        <Chevron at={STEP_AT[2]} />
        <Card
          at={STEP_AT[2]}
          eyebrow={driftSteps[1].label}
          value={driftSteps[1].status}
          note={driftSteps[1].note}
        />
        <Chevron at={VERDICT_AT} />
        <Card
          at={VERDICT_AT}
          eyebrow="You undo the agent"
          value={
            <span style={{ display: "inline-flex", alignItems: "center", gap: 12 }}>
              <svg width="36" height="36" viewBox="0 0 24 24" fill="none">
                <path
                  d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-3z"
                  stroke={colors.green}
                  strokeWidth="2.2"
                  strokeLinejoin="round"
                />
                <path
                  d="M8.5 12.2l2.4 2.4 4.6-4.8"
                  stroke={colors.green}
                  strokeWidth="2.2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              Refused
            </span>
          }
          note={keptLabel}
          tone={colors.green}
          active={verdict}
        />
      </div>
      <Terminal entries={entries} width={TERM_WIDTH} rows={TERM_ROWS}
        style={{ marginTop: 34, ...riseStyle(enter(frame, 8), 24) }}
      />
      <Caption lines={refusalCaption} at={VERDICT_AT + 8} stagger={12} fontSize={40} style={{ marginTop: 30 }} />
    </AbsoluteFill>
  );
};
