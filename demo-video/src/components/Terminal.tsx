import React from "react";
import { interpolate, useCurrentFrame } from "remotion";
import { enter } from "../anim";
import { colors, fonts, sizes } from "../theme";

/** A styled run of monospace text. */
export type Seg = { t: string; c?: string; b?: boolean; i?: boolean };

export type TermEntry =
  | {
      kind: "cmd";
      at: number;
      /** Typed one character at a time. */
      text: string;
      /** Appears all at once after typing, like a paste. */
      paste?: string;
      /** Static text shown before the typed part (defaults to the shell prompt). */
      prefix?: Seg[];
      /** Frames to wait after `at` before typing starts. */
      typeDelay?: number;
      /** Frames per character. */
      speed?: number;
    }
  | { kind: "line"; at: number; segs: Seg[] };

export const typingEnd = (e: Extract<TermEntry, { kind: "cmd" }>) =>
  e.at + (e.typeDelay ?? 0) + e.text.length * (e.speed ?? 2) + (e.paste ? 3 : 0);

const PROMPT: Seg[] = [
  { t: "❯ ", c: colors.green, b: true },
];

// Box drawing glyphs are not in the JetBrains Mono web subset, so they are
// drawn as one-character CSS cells to keep the monospace grid exact.
// Arms: [left, right, up, down]; 0 none, 1 light, 2 heavy.
const BOX: Record<string, [number, number, number, number]> = {
  "─": [1, 1, 0, 0], "━": [2, 2, 0, 0], "│": [0, 0, 1, 1], "┃": [0, 0, 2, 2],
  "┏": [0, 2, 0, 2], "┓": [2, 0, 0, 2], "┳": [2, 2, 0, 2],
  "┡": [0, 2, 2, 1], "╇": [2, 2, 2, 1], "┩": [2, 0, 2, 1],
  "└": [0, 1, 1, 0], "┘": [1, 0, 1, 0], "┴": [1, 1, 1, 0],
  "╭": [0, 1, 0, 1], "╮": [1, 0, 0, 1], "╰": [0, 1, 1, 0], "╯": [1, 0, 1, 0],
};
const BOX_RE = new RegExp(`([${Object.keys(BOX).join("")}]+)`);

const BoxChar: React.FC<{ ch: string; color: string }> = ({ ch, color }) => {
  const [l, r, u, d] = BOX[ch];
  const w = (n: number) => (n === 2 ? 3.5 : 2);
  const arm = (style: React.CSSProperties) => (
    <span style={{ position: "absolute", background: color, ...style }} />
  );
  const hw = Math.max(w(u || 1), w(d || 1)) / 2;
  const vw = Math.max(w(l || 1), w(r || 1)) / 2;
  return (
    <span style={{ position: "relative", display: "inline-block", width: "0.6em", height: "1lh", verticalAlign: "top" }}>
      {l ? arm({ left: 0, right: `calc(50% - ${hw}px)`, top: `calc(50% - ${w(l) / 2}px)`, height: w(l) }) : null}
      {r ? arm({ left: `calc(50% - ${hw}px)`, right: 0, top: `calc(50% - ${w(r) / 2}px)`, height: w(r) }) : null}
      {u ? arm({ top: 0, bottom: `calc(50% - ${vw}px)`, left: `calc(50% - ${w(u) / 2}px)`, width: w(u) }) : null}
      {d ? arm({ top: `calc(50% - ${vw}px)`, bottom: 0, left: `calc(50% - ${w(d) / 2}px)`, width: w(d) }) : null}
    </span>
  );
};

const SegRun: React.FC<{ segs: Seg[] }> = ({ segs }) => (
  <>
    {segs.map((s, i) => {
      const color = s.c ?? colors.text;
      const style: React.CSSProperties = {
        color,
        fontWeight: s.b ? 700 : 400,
        fontStyle: s.i ? "italic" : "normal",
      };
      return (
        <span key={i} style={style}>
          {s.t.split(BOX_RE).map((part, j) =>
            BOX[part[0]] ? (
              [...part].map((ch, k) => <BoxChar key={`${j}-${k}`} ch={ch} color={color} />)
            ) : (
              <React.Fragment key={j}>{part}</React.Fragment>
            ),
          )}
        </span>
      );
    })}
  </>
);

const Cursor: React.FC<{ blink: boolean; frame: number }> = ({ blink, frame }) => {
  const on = !blink || Math.floor(frame / 15) % 2 === 0;
  return (
    <span
      style={{
        display: "inline-block",
        width: "0.6em",
        height: "1.15em",
        marginLeft: 2,
        verticalAlign: "text-bottom",
        background: colors.text,
        opacity: on ? 0.85 : 0,
      }}
    />
  );
};

export const Terminal: React.FC<{
  entries: TermEntry[];
  title?: string;
  width: number;
  /** Visible body height in lines. Older lines scroll up out of view. */
  rows: number;
  fontSize?: number;
  lineHeight?: number;
  style?: React.CSSProperties;
}> = ({
  entries,
  title = "backstep",
  width,
  rows,
  fontSize = sizes.terminal,
  lineHeight = sizes.terminalLine,
  style,
}) => {
  const frame = useCurrentFrame();
  const sorted = [...entries].sort((a, b) => a.at - b.at);

  // Smooth scroll. Each entry, as it appears, may push the required offset
  // up by one line; that push eases in with the entry's own spring.
  let offset = 0;
  sorted.forEach((e, i) => {
    const overflowBefore = Math.max(0, i - rows);
    const overflowAfter = Math.max(0, i + 1 - rows);
    const delta = overflowAfter - overflowBefore;
    if (delta > 0) offset += delta * lineHeight * enter(frame, e.at, 10);
  });

  // The cursor sits on the last command that has started and has no newer
  // entry visible yet.
  const visible = sorted.filter((e) => frame >= e.at);
  const last = visible[visible.length - 1];

  const padX = 36;
  const padY = 26;
  const barH = 52;

  return (
    <div
      style={{
        width,
        borderRadius: 14,
        background: colors.surface,
        border: `1px solid ${colors.border}`,
        boxShadow: "0 30px 80px rgba(0,0,0,0.45), 0 0 0 1px rgba(255,255,255,0.02) inset",
        overflow: "hidden",
        ...style,
      }}
    >
      <div
        style={{
          height: barH,
          display: "flex",
          alignItems: "center",
          padding: "0 20px",
          borderBottom: `1px solid ${colors.border}`,
          background: colors.surfaceRaised,
          position: "relative",
        }}
      >
        {[0, 1, 2].map((i) => (
          <div
            key={i}
            style={{
              width: 14,
              height: 14,
              borderRadius: 7,
              background: "#2E343F",
              marginRight: 9,
            }}
          />
        ))}
        <div
          style={{
            position: "absolute",
            left: 0,
            right: 0,
            textAlign: "center",
            fontFamily: fonts.mono,
            fontSize: 20,
            color: colors.textMuted,
            letterSpacing: 0.3,
          }}
        >
          {title}
        </div>
      </div>
      <div
        style={{
          padding: `${padY}px ${padX}px`,
        }}
      >
        <div style={{ height: rows * lineHeight, overflow: "hidden" }}>
        <div
          style={{
            transform: `translateY(${-offset}px)`,
            fontFamily: fonts.mono,
            fontSize,
            lineHeight: `${lineHeight}px`,
            whiteSpace: "pre",
            fontVariantLigatures: "none",
          }}
        >
          {sorted.map((e, i) => {
            if (frame < e.at) return <div key={i} style={{ height: lineHeight }} />;
            const fade = interpolate(frame, [e.at, e.at + 5], [0, 1], {
              extrapolateRight: "clamp",
            });
            if (e.kind === "line") {
              return (
                <div key={i} style={{ height: lineHeight, opacity: fade }}>
                  <SegRun segs={e.segs} />
                </div>
              );
            }
            const typeStart = e.at + (e.typeDelay ?? 0);
            const speed = e.speed ?? 2;
            const typed = Math.max(
              0,
              Math.min(e.text.length, Math.floor((frame - typeStart) / speed)),
            );
            const doneTyping = typed >= e.text.length;
            const pasted = e.paste && frame >= typeStart + e.text.length * speed + 3;
            const isLast = e === last;
            const typing = !doneTyping || (e.paste && !pasted);
            return (
              <div key={i} style={{ height: lineHeight }}>
                <SegRun segs={e.prefix ?? PROMPT} />
                <span style={{ color: colors.text }}>{e.text.slice(0, typed)}</span>
                {pasted ? <span style={{ color: colors.text }}>{e.paste}</span> : null}
                {isLast ? <Cursor blink={!typing} frame={frame} /> : null}
              </div>
            );
          })}
        </div>
        </div>
      </div>
    </div>
  );
};
