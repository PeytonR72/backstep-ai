# Backstep Demo Video (Remotion) Implementation Plan

> **For agentic workers:** Work task by task. Steps use checkbox (`- [ ]`) syntax for tracking. Render and look at stills before claiming any scene is done.

**Goal:** Build a 45 to 60 second landscape demo video of Backstep, entirely in code with [Remotion](https://www.remotion.dev/), for the project README, LinkedIn and job applications. The audience is technical recruiters and hiring managers for AI engineering and AI systems roles. In under a minute it must show what Backstep is, why it matters and that it works.

**Architecture:** A standalone Remotion project in `demo-video/` at the repo root. It is TypeScript and React, has its own `package.json`, and is fully separate from the Python package. There is no screen recording. Every terminal, diagram and table is built as a React component and animated with `useCurrentFrame()`, `interpolate()` and `spring()`. One root `<Composition>` uses `<Series>` to play one component per scene.

**Tech stack:** Node 18+, Remotion 4.x, React 18, TypeScript, `@remotion/google-fonts`. Every `remotion` and `@remotion/*` package must be pinned to the **same exact version** (no `^`), or Remotion refuses to run.

## Background you need (you start with no context)

Backstep is a Python tool in this repo. Read `README.md` first, all of it. In short:

- It is an MCP (Model Context Protocol) proxy that sits between an AI agent and the tools it calls.
- Before a write runs, it snapshots the affected data into an append only SQLite journal.
- `backstep undo <action>` restores the old data, but first it checks for **drift**, meaning someone else changed those rows after the agent did. If they did, it **refuses** instead of overwriting their work. This "refuse instead of guess" idea is the strongest selling point, so give it its own scene.
- `backstep eval` is a recovery benchmark. The real numbers are in `eval_results.json`: 3 full recoveries, 1 partial, 2 correct refusals, across 6 scenarios.

Before writing any scene, also read these files. The content on screen must come from them.

- `tests/scenarios/wrong_where_update.py` is the hook scenario. The agent meant to zero out alice's balance, but `UPDATE accounts SET balance = 0 WHERE id > 0` zeroed all four accounts (alice 100, bob 250, carol 75, dave 500).
- `tests/scenarios/partial_drift.py` is the refusal scenario.
- `src/backstep/cli.py`, specifically `_print_log_table`, `_print_action_detail`, `_print_drift_table` and `_print_revert_result`. Copy their real column names, labels and wording into the fake terminal output so it matches what the tool actually prints.

## Global constraints

- **Be accurate.** Never invent features, metrics, users or integrations. The benchmark numbers on screen must match `eval_results.json` exactly. Values that are only examples, like action ids or timestamps, are fine if they look realistic, but don't present them as measurements.
- **On screen text style.** Plain English, short lines, no em dashes and no colons in prose (the owner's style preference, as in the README). Code, SQL and CLI output may use whatever characters they really contain.
- **Format.** 1920x1080, 30 fps, H.264 MP4. Total length between 1350 and 1800 frames (45 to 60 s). Aim for about 1650.
- **Silent by default.** Most viewers on LinkedIn and in README players watch muted, so all meaning must come from on screen text. Don't add music or voiceover unless the owner gives you a licensed track or asks for one.
- **Legibility.** Body text at least 36px, terminal text at least 28px. A line must stay on screen long enough to be read twice (roughly 1 second per 4 words, at least 1.5 s).
- **Stay inside `demo-video/`.** Don't touch the Python package, tests or README, except for the optional step in Task 6.
- **No commits or pushes** without asking the owner first.

## Visual direction

- Dark background (`#0B0D10` or similar) with one accent color for "safe" and "restored" (a green such as `#3DDC97`) and one warning color for damage (a red such as `#FF5C5C`). Use no other saturated colors.
- Fonts are Inter for headings and body and JetBrains Mono for terminals and code, both loaded with `@remotion/google-fonts`.
- The terminal component has a window bar with three dots, a title such as `backstep`, a monospace body, a typed text effect for commands (about 2 to 3 frames per character), and output that appears line by line.
- Motion should be calm and precise. Use springs with no overshoot or bounce (`damping` around 200). Fade and slide in by 20 to 40px. Cross fade about 10 frames between scenes. No spinning, no zoom punches and no particle effects. It should feel like infrastructure, not an ad.

## Storyboard

Frame counts are at 30 fps. Exact timings may shift by about 15% if a scene reads better that way.

| # | Scene | Frames | What is on screen |
|---|---|---|---|
| 1 | Hook | 0 to 180 (6 s) | Terminal. An agent runs `UPDATE accounts SET balance = 0 WHERE id > 0`. Next to it, a 4 row `accounts` table where every balance animates to 0 and turns red. Caption: "Your agent meant to change one row. It changed all of them." |
| 2 | Problem | 180 to 300 (4 s) | Two short lines, one after the other. "AI agents now have write access to real systems." "Mistakes are not rare. Recovery usually is." |
| 3 | Title | 300 to 420 (4 s) | "Backstep" wordmark with the subtitle "An undo button for AI agents." |
| 4 | How it works | 420 to 780 (12 s) | An animated flow diagram drawn left to right: Agent, then Backstep proxy, then MCP server, then Database, with a Journal box below the proxy. A pulse travels along the arrows. When it passes the proxy, a small "snapshot" card drops into the Journal. Three captions build in turn: "Sits between your agent and its tools." "Snapshots data before every write." "Works with any MCP client. No agent changes." |
| 5 | The undo | 780 to 1110 (11 s) | Back to the scene 1 terminal. Type `backstep log <session>` and show a log table that matches the real format. Then type `backstep undo <action>` and show a confirmation and success line in the real wording. The accounts table animates back to 100, 250, 75 and 500 and turns green. Caption: "One command. Exact original state." |
| 6 | The refusal | 1110 to 1380 (9 s) | A new situation. A teammate edits bob's row after the agent did. `backstep undo` now prints a drift table (real format) and refuses. Caption: "If someone else changed the data since, Backstep refuses rather than overwrite their work." |
| 7 | Proof | 1380 to 1560 (6 s) | Benchmark results from `eval_results.json`. Six scenario rows appear one by one, each with a result chip (Full, Partial or Refused correctly). Then the summary appears: "3 full. 1 partial. 2 correct refusals." Small subtitle: "Graded against a real Postgres database." |
| 8 | Outro | 1560 to 1650 (3 s) | "Backstep" wordmark, `github.com/PeytonR72/backstep-ai`, and a row of stack chips (Python, MCP, Postgres, asyncio, Claude API). Hold the final frame. |

Scene 1 is the most important. The first 3 seconds decide whether anyone keeps watching. Show the damage happening, not a logo.

## Project structure

```
demo-video/
  package.json
  tsconfig.json
  remotion.config.ts
  .gitignore              node_modules, out
  src/
    index.ts              registerRoot(Root)
    Root.tsx              one <Composition id="BackstepDemo">
    BackstepDemo.tsx      <Series> of all scenes
    theme.ts              colors, fonts, sizes, spring presets
    data.ts               accounts rows, SQL, CLI output, benchmark rows (from eval_results.json)
    components/
      Terminal.tsx        window chrome, typed command, line by line output
      AccountsTable.tsx   4 rows, animatable balance and color per row
      FlowDiagram.tsx     boxes, arrows, traveling pulse
      Caption.tsx         fade and slide caption
      ResultChip.tsx
    scenes/
      Hook.tsx  Problem.tsx  Title.tsx  HowItWorks.tsx
      Undo.tsx  Refusal.tsx  Proof.tsx  Outro.tsx
```

Put all copy and data in `data.ts` so the owner can edit wording without touching animation code.

---

## Task 1. Scaffold the Remotion project

- [x] Create `demo-video/` by hand rather than with the interactive `npx create-video` wizard, which prompts and may hang in a non interactive shell. Look up the current stable Remotion 4.x version with `npm view remotion version` and pin `remotion`, `@remotion/cli` and `@remotion/google-fonts` to it exactly. Add `react`, `react-dom`, `typescript`, `@types/react`.
- [x] Add scripts `"studio": "remotion studio"` and `"render": "remotion render src/index.ts BackstepDemo out/backstep-demo.mp4"`.
- [x] Write `index.ts`, `Root.tsx` (1920x1080, 30 fps, `durationInFrames` 1650) and a placeholder scene.
- [x] Verify with `npx remotion still src/index.ts BackstepDemo out/check.png --frame=0` and open the PNG. It should render without errors.

## Task 2. Theme and shared components

- [x] `theme.ts` with the palette, fonts and a `calmSpring` preset.
- [x] `Terminal` takes `command`, `output: string[]`, `startFrame`, `typingSpeed`. Commands type out, then output lines appear about 4 frames apart. Show a blinking cursor while typing.
- [x] `AccountsTable` takes per row balances and a per row state (`normal | damaged | restored`). It interpolates numbers and colors between states.
- [x] `Caption` and `ResultChip`.
- [x] Render a test still of each component and look at it.

## Task 3. Scenes 1 to 3 (hook, problem, title)

- [x] Build them using the storyboard and the real SQL and data from `wrong_where_update.py`.
- [x] Render stills at frames 30, 120, 240 and 360 and check legibility and composition.

## Task 4. Scenes 4 to 6 (how it works, undo, refusal)

- [x] Before writing any terminal output, read the print functions in `src/backstep/cli.py` and the `partial_drift` scenario. Mirror the real column headers and messages. Write them into `data.ts` with a comment naming the source function.
- [x] `FlowDiagram` draws its boxes in sequence, then the pulse. Keep the labels short.
- [x] Render stills at frames 600, 950, 1100 and 1300.

## Task 5. Scenes 7 and 8 (proof, outro) and full render

- [x] Import or copy the six scenarios and the summary from `eval_results.json` into `data.ts`. Use friendly labels such as "Wrong WHERE clause", "Runaway insert loop" and "Cascading delete", with the real grade for each.
- [x] Run `npm run render`. Confirm the length with `ffprobe -v error -show_entries format=duration out/backstep-demo.mp4`, which should give 45 to 60 s.
- [x] Watch the full render start to finish, or check stills every 2 seconds if you can't play video. Fix text that disappears too fast, overlapping elements and abrupt cuts.

## Task 6. Deliverables and handoff

- [x] Final video at `demo-video/out/backstep-demo.mp4`. The `out/` folder is gitignored, so the owner decides where to host the file.
- [x] Render a poster still (a strong frame from scene 5 or 7) to `demo-video/out/poster.png` for README or LinkedIn thumbnails.
- [ ] Optional, only if the owner agrees. Add a GIF or poster link near the top of `README.md`.
- [x] Report back with the final duration, file size, a list of scenes with their frame ranges, and anything from this plan you changed or skipped and why. Don't commit or push without asking.

## Done when

- The MP4 renders cleanly at 1920x1080, 30 fps, 45 to 60 s.
- Every claim on screen can be traced to `README.md`, `eval_results.json`, a scenario file or `cli.py`.
- A muted viewer who knows nothing about Backstep could say what it does and why it is safe after one watch.
