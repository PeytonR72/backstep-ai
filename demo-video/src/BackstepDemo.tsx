import { fade } from "@remotion/transitions/fade";
import { linearTiming, TransitionSeries } from "@remotion/transitions";
import React from "react";
import { AbsoluteFill, Sequence } from "remotion";
import { Background } from "./components/Background";
import { Hook, HOOK_FRAMES } from "./scenes/Hook";
import { HOW_FRAMES, HowItWorks } from "./scenes/HowItWorks";
import { Outro, OUTRO_FRAMES } from "./scenes/Outro";
import { Problem, PROBLEM_FRAMES } from "./scenes/Problem";
import { Proof, PROOF_FRAMES } from "./scenes/Proof";
import { Refusal, REFUSAL_FRAMES } from "./scenes/Refusal";
import { Title, TITLE_FRAMES } from "./scenes/Title";
import { Undo, UNDO_FRAMES } from "./scenes/Undo";
import { TRANSITION_FRAMES } from "./theme";

export const SCENES: { name: string; frames: number; Component: React.FC }[] = [
  { name: "Hook", frames: HOOK_FRAMES, Component: Hook },
  { name: "Problem", frames: PROBLEM_FRAMES, Component: Problem },
  { name: "Title", frames: TITLE_FRAMES, Component: Title },
  { name: "How it works", frames: HOW_FRAMES, Component: HowItWorks },
  { name: "Undo", frames: UNDO_FRAMES, Component: Undo },
  { name: "Refusal", frames: REFUSAL_FRAMES, Component: Refusal },
  { name: "Proof", frames: PROOF_FRAMES, Component: Proof },
  { name: "Outro", frames: OUTRO_FRAMES, Component: Outro },
];

// Every scene after the first is padded by the transition length and its
// content starts only once the previous scene has fully faded out, so
// entrance animations never overlap the outgoing scene. The padding and the
// transition overlap cancel out, so the total is just the sum of the scenes.
export const TOTAL_FRAMES = SCENES.reduce((a, s) => a + s.frames, 0);

export const BackstepDemo: React.FC = () => (
  <AbsoluteFill>
    <Background />
    <TransitionSeries>
      {SCENES.flatMap(({ name, frames, Component }, i) => {
        if (i === 0) {
          return [
            <TransitionSeries.Sequence key={name} durationInFrames={frames} name={name}>
              <Component />
            </TransitionSeries.Sequence>,
          ];
        }
        const seq = (
          <TransitionSeries.Sequence key={name} durationInFrames={frames + TRANSITION_FRAMES} name={name}>
            <Sequence from={TRANSITION_FRAMES} name={`${name} content`}>
              <Component />
            </Sequence>
          </TransitionSeries.Sequence>
        );
        return [
          <TransitionSeries.Transition
            key={`${name}-in`}
            presentation={fade({ shouldFadeOutExitingScene: true })}
            timing={linearTiming({ durationInFrames: TRANSITION_FRAMES })}
          />,
          seq,
        ];
      })}
    </TransitionSeries>
  </AbsoluteFill>
);
