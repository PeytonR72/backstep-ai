import React from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { enter, riseStyle } from "../anim";
import { LogoMark } from "../components/Logo";
import { productName, repoUrl, stack, tagline } from "../data";
import { colors, fonts } from "../theme";

export const OUTRO_FRAMES = 135;

const GitHubMark: React.FC = () => (
  <svg width="34" height="34" viewBox="0 0 16 16" fill={colors.text}>
    <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
  </svg>
);

export const Outro: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "center" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 28, ...riseStyle(enter(frame, 0), 20) }}>
        <LogoMark size={96} />
        <div style={{ fontFamily: fonts.sans, fontSize: 104, fontWeight: 700, letterSpacing: -3, color: colors.text }}>
          {productName}
        </div>
      </div>
      <div
        style={{
          marginTop: 18,
          fontFamily: fonts.sans,
          fontSize: 40,
          color: colors.textMuted,
          ...riseStyle(enter(frame, 6), 16),
        }}
      >
        {tagline}
      </div>
      <div
        style={{
          marginTop: 56,
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "16px 28px",
          borderRadius: 14,
          background: colors.surface,
          border: `1px solid ${colors.borderStrong}`,
          fontFamily: fonts.mono,
          fontSize: 34,
          color: colors.text,
          ...riseStyle(enter(frame, 14), 16),
        }}
      >
        <GitHubMark />
        {repoUrl}
      </div>
      <div style={{ marginTop: 44, display: "flex", gap: 14 }}>
        {stack.map((s, i) => (
          <div
            key={s}
            style={{
              padding: "8px 20px",
              borderRadius: 999,
              border: `1px solid ${colors.border}`,
              fontFamily: fonts.sans,
              fontSize: 26,
              fontWeight: 500,
              color: colors.textMuted,
              ...riseStyle(enter(frame, 22 + i * 4), 12),
            }}
          >
            {s}
          </div>
        ))}
      </div>
    </AbsoluteFill>
  );
};
