import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, riseStyle } from "../anim";
import { ResultChip } from "../components/ResultChip";
import { benchmark, benchmarkHeading, benchmarkSub, benchmarkSummary } from "../data";
import { colors, fonts } from "../theme";

export const PROOF_FRAMES = 215;

const ROWS_AT = 16;
const ROW_GAP = 9;
const SUMMARY_AT = ROWS_AT + benchmark.length * ROW_GAP + 26;
const WIDTH = 1440;

export const Proof: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div style={{ width: WIDTH, marginTop: 118 }}>
        <div
          style={{
            fontFamily: fonts.mono,
            fontSize: 24,
            letterSpacing: 3,
            color: colors.green,
            ...riseStyle(enter(frame, 0), 16),
          }}
        >
          {benchmarkHeading.toUpperCase()}
        </div>
        <div
          style={{
            marginTop: 14,
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            ...riseStyle(enter(frame, 4), 16),
          }}
        >
          <div style={{ fontFamily: fonts.sans, fontSize: 52, fontWeight: 600, letterSpacing: -1, color: colors.text }}>
            Six failure scenarios, end to end
          </div>
          <div style={{ fontFamily: fonts.mono, fontSize: 24, color: colors.textDim }}>backstep eval</div>
        </div>

        <div style={{ marginTop: 34, borderTop: `1px solid ${colors.border}` }}>
          {benchmark.map((row, i) => {
            const p = enter(frame, ROWS_AT + i * ROW_GAP);
            return (
              <div
                key={row.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  height: 88,
                  borderBottom: `1px solid ${colors.border}`,
                  ...riseStyle(p, 16),
                }}
              >
                <div style={{ flex: 1 }}>
                  <span style={{ fontFamily: fonts.sans, fontSize: 36, fontWeight: 600, color: colors.text }}>
                    {row.label}
                  </span>
                  <span style={{ fontFamily: fonts.sans, fontSize: 28, color: colors.textMuted, marginLeft: 20 }}>
                    {row.detail}
                  </span>
                </div>
                <div
                  style={{
                    width: 140,
                    textAlign: "right",
                    fontFamily: fonts.mono,
                    fontSize: 28,
                    color: colors.textDim,
                    marginRight: 32,
                  }}
                >
                  {row.time}
                </div>
                <div style={{ width: 320, display: "flex", justifyContent: "flex-end" }}>
                  <ResultChip grade={row.grade} />
                </div>
              </div>
            );
          })}
        </div>

        <div
          style={{
            marginTop: 40,
            display: "flex",
            alignItems: "baseline",
            justifyContent: "space-between",
            ...riseStyle(enter(frame, SUMMARY_AT), 20),
          }}
        >
          <div style={{ fontFamily: fonts.sans, fontSize: 50, fontWeight: 600, letterSpacing: -1, color: colors.text }}>
            {benchmarkSummary.split(". ").map((part, i, all) => (
              <span key={i} style={{ color: i === 1 ? colors.textMuted : colors.green }}>
                {part}
                {i < all.length - 1 ? ". " : ""}
              </span>
            ))}
          </div>
          <div
            style={{
              fontFamily: fonts.sans,
              fontSize: 28,
              color: colors.textMuted,
              ...riseStyle(enter(frame, SUMMARY_AT + 10), 12),
            }}
          >
            {benchmarkSub}
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
