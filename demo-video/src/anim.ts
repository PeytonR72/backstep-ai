import type { CSSProperties } from "react";
import { Easing, interpolate, spring } from "remotion";
import { calmSpring, VIDEO } from "./theme";

/** 0 to 1 calm spring progress that starts at `start`. */
export const enter = (frame: number, start: number, durationInFrames = 24) =>
  spring({
    frame: frame - start,
    fps: VIDEO.fps,
    config: calmSpring,
    durationInFrames,
  });

/** Fade and slide up by `distance` px as `progress` goes 0 to 1. */
export const riseStyle = (progress: number, distance = 28): CSSProperties => ({
  opacity: progress,
  transform: `translateY(${(1 - progress) * distance}px)`,
});

/** Eased 0 to 1 ramp between two frames, clamped. */
export const ramp = (frame: number, from: number, to: number) =>
  interpolate(frame, [from, to], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.33, 0, 0.2, 1),
  });

/** Linear interpolation between two colors. */
export const mixColor = (a: string, b: string, t: number) => {
  const pa = parseHex(a);
  const pb = parseHex(b);
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
};

/** Accepts "#rrggbb" or the "rgb(r, g, b)" strings mixColor returns. */
const parseHex = (color: string) => {
  if (color.startsWith("rgb")) return (color.match(/\d+/g) ?? []).slice(0, 3).map(Number);
  const h = color.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
};
