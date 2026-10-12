import React from "react";
import { useCurrentFrame } from "remotion";
import { mixColor, ramp } from "../anim";
import { accounts } from "../data";
import { colors, fonts } from "../theme";

export type RowState = "normal" | "damaged" | "restored";

const STAGGER = 5;

/**
 * The 4-row `accounts` table, laid out as a strip of row cards.
 * Balances count down to 0 from `damageAt` and back up from `restoreAt`.
 */
export const AccountsTable: React.FC<{
  width: number;
  damageAt?: number;
  restoreAt?: number;
  /** Mark alice as the row the agent meant to change. */
  showTarget?: boolean;
  style?: React.CSSProperties;
}> = ({ width, damageAt, restoreAt, showTarget = false, style }) => {
  const frame = useCurrentFrame();
  const labelW = 250;
  const gap = 20;
  const cardW = (width - labelW - gap * accounts.length) / accounts.length;

  return (
    <div style={{ display: "flex", alignItems: "stretch", gap, width, ...style }}>
      <div
        style={{
          width: labelW,
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          fontFamily: fonts.mono,
        }}
      >
        <div style={{ fontSize: 28, color: colors.text }}>accounts</div>
        <div style={{ fontSize: 22, color: colors.textDim, marginTop: 6 }}>
          public · Postgres
        </div>
      </div>
      {accounts.map((row, i) => {
        const d = damageAt === undefined ? 0 : ramp(frame, damageAt + i * STAGGER, damageAt + i * STAGGER + 20);
        const r = restoreAt === undefined ? 0 : ramp(frame, restoreAt + i * STAGGER, restoreAt + i * STAGGER + 22);
        const damaged = row.balance * (1 - d);
        const value = Math.round(damaged + (row.balance - damaged) * r);

        // normal -> red as damage lands, red -> green as it is restored.
        const tone = mixColor(mixColor(colors.text, colors.red, d), colors.green, r);
        const accent = r > 0 ? colors.green : colors.red;
        const strength = r > 0 ? r : d;
        const isTarget = showTarget && i === 0;

        return (
          <div
            key={row.id}
            style={{
              width: cardW,
              position: "relative",
              borderRadius: 12,
              padding: "22px 26px",
              background: colors.surface,
              border: `1px solid ${mixColor(colors.border, accent, strength * 0.75)}`,
              boxShadow: `inset 0 0 0 9999px ${accent}${Math.round(strength * 22)
                .toString(16)
                .padStart(2, "0")}`,
            }}
          >
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "baseline",
                fontFamily: fonts.sans,
                fontSize: 28,
                color: colors.textMuted,
              }}
            >
              <span>{row.owner}</span>
              <span style={{ fontFamily: fonts.mono, fontSize: 20, color: colors.textDim }}>
                id {row.id}
              </span>
            </div>
            <div
              style={{
                fontFamily: fonts.mono,
                fontSize: 64,
                fontWeight: 500,
                color: tone,
                marginTop: 8,
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {value}
            </div>
            {isTarget ? (
              <div
                style={{
                  position: "absolute",
                  top: -16,
                  right: 18,
                  padding: "4px 12px",
                  borderRadius: 999,
                  background: colors.bg,
                  border: `1px solid ${colors.borderStrong}`,
                  fontFamily: fonts.sans,
                  fontSize: 18,
                  fontWeight: 500,
                  color: colors.textMuted,
                }}
              >
                intended target
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
};
