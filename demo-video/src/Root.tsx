import React from "react";
import { AbsoluteFill, Composition } from "remotion";
import { BackstepDemo, TOTAL_FRAMES } from "./BackstepDemo";
import { LogoMark } from "./components/Logo";
import { VIDEO } from "./theme";

// The logo mark alone on a transparent background, for exporting PNGs.
const Logo: React.FC = () => (
  <AbsoluteFill>
    <LogoMark size={512} />
  </AbsoluteFill>
);

export const Root: React.FC = () => (
  <>
  <Composition
    id="BackstepDemo"
    component={BackstepDemo}
    durationInFrames={TOTAL_FRAMES}
    fps={VIDEO.fps}
    width={VIDEO.width}
    height={VIDEO.height}
  />
  <Composition id="Logo" component={Logo} durationInFrames={1} fps={VIDEO.fps} width={512} height={512} />
  </>
);
