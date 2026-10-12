import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, ramp, riseStyle } from "../anim";
import { LogoMark } from "../components/Logo";
import { productName, tagline } from "../data";
import { colors, fonts } from "../theme";

export const TITLE_FRAMES = 120;

export const Title: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 36, ...riseStyle(enter(frame, 4), 20) }}>
        <LogoMark size={128} draw={ramp(frame, 8, 40)} />
        <div
          style={{
            fontFamily: fonts.sans,
            fontSize: 132,
            fontWeight: 700,
            letterSpacing: -4,
            color: colors.text,
          }}
        >
          {productName}
        </div>
      </div>
      <div
        style={{
          ...riseStyle(enter(frame, 26), 20),
          marginTop: 30,
          fontFamily: fonts.sans,
          fontSize: 48,
          fontWeight: 400,
          color: colors.textMuted,
          letterSpacing: -0.5,
        }}
      >
        {tagline}
      </div>
    </AbsoluteFill>
  );
};
