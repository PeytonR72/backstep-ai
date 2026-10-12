import React from "react";
import { useCurrentFrame } from "remotion";
import { enter, riseStyle } from "../anim";
import { colors, fonts, sizes } from "../theme";

export type CaptionLine = { text: string; color?: string };

/** Centered caption whose lines fade and rise in one after another. */
export const Caption: React.FC<{
  lines: (string | CaptionLine)[];
  at: number;
  stagger?: number;
  fontSize?: number;
  style?: React.CSSProperties;
}> = ({ lines, at, stagger = 10, fontSize = sizes.caption, style }) => {
  const frame = useCurrentFrame();
  return (
    <div
      style={{
        fontFamily: fonts.sans,
        fontSize,
        fontWeight: 500,
        letterSpacing: -0.4,
        lineHeight: 1.3,
        textAlign: "center",
        color: colors.text,
        ...style,
      }}
    >
      {lines.map((l, i) => {
        const line = typeof l === "string" ? { text: l } : l;
        const p = enter(frame, at + i * stagger);
        return (
          <div key={i} style={{ ...riseStyle(p, 24), color: line.color ?? colors.text }}>
            {line.text}
          </div>
        );
      })}
    </div>
  );
};
