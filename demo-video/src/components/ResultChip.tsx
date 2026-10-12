import React from "react";
import { gradeLabel, type Grade } from "../data";
import { colors, fonts } from "../theme";

const Check: React.FC<{ color: string }> = ({ color }) => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path d="M5 12.5l4.5 4.5L19 7.5" stroke={color} strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" />
  </svg>
);

const Shield: React.FC<{ color: string }> = ({ color }) => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
    <path
      d="M12 3l7 3v6c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-3z"
      stroke={color}
      strokeWidth="2.2"
      strokeLinejoin="round"
    />
  </svg>
);

const Half: React.FC<{ color: string }> = ({ color }) => (
  <svg width="22" height="22" viewBox="0 0 24 24" fill="none">
    <circle cx="12" cy="12" r="8" stroke={color} strokeWidth="2.2" />
    <path d="M12 4a8 8 0 0 1 0 16z" fill={color} />
  </svg>
);

/** Benchmark grade pill. Full and refused are both passing outcomes. */
export const ResultChip: React.FC<{ grade: Grade; style?: React.CSSProperties }> = ({
  grade,
  style,
}) => {
  const filled = grade === "full";
  const tone = grade === "partial" ? colors.textMuted : colors.green;
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 10,
        padding: "8px 18px 8px 14px",
        borderRadius: 999,
        fontFamily: fonts.sans,
        fontSize: 28,
        fontWeight: 600,
        color: tone,
        background: filled ? colors.greenSoft : "transparent",
        border: `1.5px solid ${grade === "partial" ? colors.borderStrong : "rgba(61,220,151,0.55)"}`,
        whiteSpace: "nowrap",
        ...style,
      }}
    >
      {grade === "full" ? <Check color={tone} /> : grade === "refused" ? <Shield color={tone} /> : <Half color={tone} />}
      {gradeLabel[grade]}
    </div>
  );
};
