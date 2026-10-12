import React from "react";
import { colors } from "../theme";

/** Backstep mark: an undo arrow in a rounded square. `draw` 0 to 1 strokes it in. */
export const LogoMark: React.FC<{ size: number; draw?: number }> = ({ size, draw = 1 }) => (
  <svg width={size} height={size} viewBox="0 0 64 64" fill="none">
    <rect x="1.5" y="1.5" width="61" height="61" rx="15" fill={colors.greenSoft} stroke="rgba(61,220,151,0.45)" strokeWidth="1.5" />
    <path
      d="M15 27 H38 A11 11 0 0 1 38 49 H27"
      pathLength={1}
      stroke={colors.green}
      strokeWidth="5"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray="1 1"
      strokeDashoffset={1 - draw}
    />
    <path
      d="M25 17 L15 27 L25 37"
      pathLength={1}
      stroke={colors.green}
      strokeWidth="5"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeDasharray="1 1"
      strokeDashoffset={1 - Math.max(0, Math.min(1, (draw - 0.55) / 0.45))}
    />
  </svg>
);
