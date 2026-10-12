import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, mixColor, ramp, riseStyle } from "../anim";
import { FLOW, FlowDiagram } from "../components/FlowDiagram";
import { howItWorksCaptions } from "../data";
import { colors, fonts } from "../theme";

export const HOW_FRAMES = 345;

const CAPTION_AT = [62, FLOW.snapshotAt + 8, 246];

export const HowItWorks: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center" }}>
      <div
        style={{
          marginTop: 128,
          fontFamily: fonts.mono,
          fontSize: 24,
          letterSpacing: 3,
          color: colors.green,
          ...riseStyle(enter(frame, 0), 16),
        }}
      >
        HOW IT WORKS
      </div>
      <FlowDiagram style={{ marginTop: 56 }} />
      <div style={{ marginTop: 10, display: "flex", flexDirection: "column", gap: 14, alignItems: "center" }}>
        {howItWorksCaptions.map((text, i) => {
          const p = enter(frame, CAPTION_AT[i]);
          const next = CAPTION_AT[i + 1];
          const dim = next === undefined ? 0 : ramp(frame, next, next + 14);
          return (
            <div
              key={i}
              style={{
                ...riseStyle(p, 20),
                fontFamily: fonts.sans,
                fontSize: 42,
                fontWeight: 500,
                letterSpacing: -0.4,
                color: mixColor(colors.text, colors.textDim, dim),
              }}
            >
              {text}
            </div>
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
