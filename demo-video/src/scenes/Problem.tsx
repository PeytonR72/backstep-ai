import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, riseStyle } from "../anim";
import { problemLines } from "../data";
import { colors, fonts } from "../theme";

export const PROBLEM_FRAMES = 140;

export const Problem: React.FC = () => {
  const frame = useCurrentFrame();
  const [first, second] = problemLines;
  const [a, b] = second.split(". ");
  return (
    <AbsoluteFill
      style={{
        alignItems: "center",
        justifyContent: "center",
        fontFamily: fonts.sans,
        fontSize: 60,
        fontWeight: 600,
        letterSpacing: -1,
        textAlign: "center",
        gap: 34,
      }}
    >
      <div style={{ ...riseStyle(enter(frame, 6)), color: colors.text }}>{first}</div>
      <div style={{ ...riseStyle(enter(frame, 46)), color: colors.textMuted }}>
        {a}. <span style={{ color: colors.text }}>{b}</span>
      </div>
    </AbsoluteFill>
  );
};
