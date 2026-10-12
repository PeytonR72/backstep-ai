import React from "react";
import { Composition } from "remotion";
import { BackstepDemo, TOTAL_FRAMES } from "./BackstepDemo";
import { VIDEO } from "./theme";

export const Root: React.FC = () => (
  <Composition
    id="BackstepDemo"
    component={BackstepDemo}
    durationInFrames={TOTAL_FRAMES}
    fps={VIDEO.fps}
    width={VIDEO.width}
    height={VIDEO.height}
  />
);
