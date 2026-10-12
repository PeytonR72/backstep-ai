import { loadFont as loadInter } from "@remotion/google-fonts/Inter";
import { loadFont as loadMono } from "@remotion/google-fonts/JetBrainsMono";
import type { SpringConfig } from "remotion";

const inter = loadInter("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
});
const mono = loadMono("normal", {
  weights: ["400", "500", "700"],
  subsets: ["latin"],
});

export const fonts = {
  sans: inter.fontFamily,
  mono: mono.fontFamily,
};

export const colors = {
  bg: "#0B0D10",
  surface: "#111419",
  surfaceRaised: "#161A21",
  border: "#232832",
  borderStrong: "#323946",
  text: "#E8EAED",
  textMuted: "#8B94A3",
  textDim: "#5A6270",
  // The one "safe / restored" accent.
  green: "#3DDC97",
  greenSoft: "rgba(61, 220, 151, 0.12)",
  // The one "damage" accent.
  red: "#FF5C5C",
  redSoft: "rgba(255, 92, 92, 0.12)",
  // Rich prints refusals and drift in yellow. Used only inside the terminal,
  // desaturated so it reads as terminal output rather than a third accent.
  termYellow: "#E3C27A",
};

export const VIDEO = {
  width: 1920,
  height: 1080,
  fps: 30,
};

export const sizes = {
  terminal: 28,
  terminalLine: 42,
  body: 40,
  caption: 44,
  heading: 72,
};

/** Calm, no-overshoot spring used for every entrance. */
export const calmSpring: Partial<SpringConfig> = {
  damping: 200,
  stiffness: 120,
  mass: 1,
};

export const TRANSITION_FRAMES = 12;
