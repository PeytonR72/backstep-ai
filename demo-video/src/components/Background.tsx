import React from "react";
import { AbsoluteFill } from "remotion";
import { colors } from "../theme";

/** Flat dark canvas with a faint dot grid and a soft top glow. Static. */
export const Background: React.FC = () => (
  <AbsoluteFill style={{ background: colors.bg }}>
    <AbsoluteFill
      style={{
        backgroundImage: "radial-gradient(rgba(255,255,255,0.055) 1.2px, transparent 1.2px)",
        backgroundSize: "36px 36px",
        maskImage: "radial-gradient(ellipse 75% 70% at 50% 45%, black 20%, transparent 100%)",
        WebkitMaskImage: "radial-gradient(ellipse 75% 70% at 50% 45%, black 20%, transparent 100%)",
      }}
    />
    <AbsoluteFill
      style={{
        background:
          "radial-gradient(ellipse 60% 45% at 50% 0%, rgba(61,220,151,0.05), transparent 70%)",
      }}
    />
  </AbsoluteFill>
);
