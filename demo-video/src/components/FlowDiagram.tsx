import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { enter, mixColor, ramp, riseStyle } from "../anim";
import { flowNodes } from "../data";
import { colors, fonts } from "../theme";
import { LogoMark } from "./Logo";

// Diagram space, in px.
export const DIAGRAM_W = 1680;
export const DIAGRAM_H = 560;

const BOX_W = 290;
const BOX_H = 136;
const ROW_Y = 40;
const COLS = [0, 463, 926, 1389]; // left edges, evenly spaced
const JOURNAL = { x: COLS[1], y: 350, w: BOX_W, h: 176 };

const cx = (i: number) => COLS[i] + BOX_W / 2;
const midY = ROW_Y + BOX_H / 2;

/** Frame schedule, relative to the diagram's own start. */
export const FLOW = {
  nodesAt: 0,
  arrowsAt: 30,
  pulseAt: 104,
  snapshotAt: 136,
  pulseResumeAt: 160,
  pulseEnd: 222,
};

type NodeKey = keyof typeof flowNodes;

const Node: React.FC<{
  k: NodeKey;
  x: number;
  y: number;
  w?: number;
  h?: number;
  appear: number;
  glow?: number;
  highlight?: boolean;
  /** Pin the label to the bottom, leaving the top free for dropped cards. */
  labelBottom?: boolean;
}> = ({ k, x, y, w = BOX_W, h = BOX_H, appear, glow = 0, highlight = false, labelBottom = false }) => {
  const frame = useCurrentFrame();
  const p = enter(frame, appear);
  const n = flowNodes[k];
  const base = highlight ? "rgba(61,220,151,0.55)" : colors.borderStrong;
  return (
    <div
      style={{
        position: "absolute",
        left: x,
        top: y,
        width: w,
        height: h,
        borderRadius: 16,
        background: highlight ? "#0F1A16" : colors.surface,
        border: `1.5px solid ${glow > 0 ? mixColor(highlight ? "#2F8F66" : colors.borderStrong, colors.green, glow) : base}`,
        boxShadow: highlight
          ? `0 0 ${30 + glow * 40}px rgba(61,220,151,${0.1 + glow * 0.25})`
          : "0 20px 50px rgba(0,0,0,0.35)",
        display: "flex",
        flexDirection: "column",
        justifyContent: labelBottom ? "flex-end" : "center",
        padding: labelBottom ? "0 28px 22px" : "0 28px",
        ...riseStyle(p, 24),
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        {highlight ? <LogoMark size={40} /> : null}
        <div style={{ fontFamily: fonts.sans, fontSize: 34, fontWeight: 600, color: colors.text }}>
          {n.title}
        </div>
      </div>
      <div style={{ fontFamily: fonts.mono, fontSize: 22, color: colors.textMuted, marginTop: 6 }}>
        {n.sub}
      </div>
    </div>
  );
};

const Arrow: React.FC<{ d: string; draw: number }> = ({ d, draw }) => (
  <path
    d={d}
    pathLength={1}
    stroke={colors.borderStrong}
    strokeWidth={2.5}
    fill="none"
    strokeDasharray="1 1"
    strokeDashoffset={1 - draw}
    markerEnd={draw > 0.95 ? "url(#arrowhead)" : undefined}
  />
);

/** Agent -> Backstep -> MCP server -> Database, with the Journal under Backstep. */
export const FlowDiagram: React.FC<{ style?: React.CSSProperties }> = ({ style }) => {
  const frame = useCurrentFrame();
  const { arrowsAt, pulseAt, snapshotAt, pulseResumeAt, pulseEnd } = FLOW;

  const gapStart = (i: number) => COLS[i] + BOX_W + 6;
  const gapEnd = (i: number) => COLS[i + 1] - 10;
  const segs = [0, 1, 2].map((i) => `M ${gapStart(i)} ${midY} L ${gapEnd(i)} ${midY}`);
  const journalArrow = `M ${cx(1)} ${ROW_Y + BOX_H + 6} L ${cx(1)} ${JOURNAL.y - 10}`;

  // Pulse path: agent -> proxy (pause while snapshotting) -> server -> db.
  const pulseX = (() => {
    if (frame < pulseResumeAt) {
      return interpolate(frame, [pulseAt, snapshotAt - 6], [COLS[0] + BOX_W, cx(1)], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });
    }
    return interpolate(frame, [pulseResumeAt, pulseEnd], [cx(1), COLS[3] + 30], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  })();
  const pulseOpacity =
    interpolate(frame, [pulseAt, pulseAt + 6], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) *
    interpolate(frame, [pulseEnd - 8, pulseEnd], [1, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
  // Hidden while "inside" a box so it reads as passing through.
  const insideBox = COLS.some((l) => pulseX > l + 4 && pulseX < l + BOX_W - 4);

  const proxyGlow =
    interpolate(frame, [snapshotAt - 10, snapshotAt, pulseResumeAt + 4, pulseResumeAt + 24], [0, 1, 1, 0], {
      extrapolateLeft: "clamp",
      extrapolateRight: "clamp",
    });
  const dbGlow = interpolate(frame, [pulseEnd - 14, pulseEnd - 4, pulseEnd + 20], [0, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  // Snapshot card falls from the proxy into the journal.
  const drop = ramp(frame, snapshotAt, snapshotAt + 26);
  const cardStartY = ROW_Y + BOX_H + 10;
  const cardEndY = JOURNAL.y + 22;
  const cardY = cardStartY + (cardEndY - cardStartY) * drop;
  const cardOpacity = interpolate(frame, [snapshotAt, snapshotAt + 6], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <div style={{ position: "relative", width: DIAGRAM_W, height: DIAGRAM_H, ...style }}>
      <svg width={DIAGRAM_W} height={DIAGRAM_H} style={{ position: "absolute", inset: 0 }}>
        <defs>
          <marker id="arrowhead" viewBox="0 0 10 10" refX="7" refY="5" markerWidth="7" markerHeight="7" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" fill={colors.borderStrong} />
          </marker>
          <radialGradient id="pulse">
            <stop offset="0%" stopColor={colors.green} stopOpacity="1" />
            <stop offset="45%" stopColor={colors.green} stopOpacity="0.5" />
            <stop offset="100%" stopColor={colors.green} stopOpacity="0" />
          </radialGradient>
        </defs>
        {segs.map((d, i) => (
          <Arrow key={i} d={d} draw={ramp(frame, arrowsAt + i * 8, arrowsAt + i * 8 + 22)} />
        ))}
        <Arrow d={journalArrow} draw={ramp(frame, arrowsAt + 30, arrowsAt + 50)} />
      </svg>

      <Node k="agent" x={COLS[0]} y={ROW_Y} appear={0} />
      <Node k="proxy" x={COLS[1]} y={ROW_Y} appear={7} highlight glow={proxyGlow} />
      <Node k="server" x={COLS[2]} y={ROW_Y} appear={14} />
      <Node k="db" x={COLS[3]} y={ROW_Y} appear={21} glow={dbGlow} />
      <Node k="journal" x={JOURNAL.x} y={JOURNAL.y} h={JOURNAL.h} appear={34} labelBottom />

      {/* Snapshot card */}
      <div
        style={{
          position: "absolute",
          left: JOURNAL.x + 22,
          top: cardY,
          width: JOURNAL.w - 44,
          padding: "8px 14px",
          borderRadius: 10,
          background: "#13221C",
          border: "1px solid rgba(61,220,151,0.5)",
          fontFamily: fonts.mono,
          fontSize: 20,
          color: colors.green,
          opacity: cardOpacity,
          display: "flex",
          justifyContent: "space-between",
          transform: `scale(${0.92 + 0.08 * drop})`,
          zIndex: 2,
        }}
      >
        <span>pre-state</span>
        <span style={{ color: colors.textMuted }}>4 rows</span>
      </div>

      {/* Traveling pulse */}
      <svg width={DIAGRAM_W} height={DIAGRAM_H} style={{ position: "absolute", inset: 0, zIndex: 3 }}>
        <circle
          cx={pulseX}
          cy={midY}
          r={16}
          fill="url(#pulse)"
          opacity={insideBox ? 0 : pulseOpacity}
        />
        <circle
          cx={pulseX}
          cy={midY}
          r={5}
          fill={colors.green}
          opacity={insideBox ? 0 : pulseOpacity}
        />
      </svg>
    </div>
  );
};
