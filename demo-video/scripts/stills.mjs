// Renders review stills at the given frames, bundling only once.
// Usage: node scripts/stills.mjs 30 120 240 [--scale=0.5]
import { bundle } from "@remotion/bundler";
import { renderStill, selectComposition } from "@remotion/renderer";
import path from "node:path";

const args = process.argv.slice(2);
const scale = Number((args.find((a) => a.startsWith("--scale=")) ?? "--scale=0.5").split("=")[1]);
const frames = args.filter((a) => !a.startsWith("--")).map(Number);

const serveUrl = await bundle({ entryPoint: path.resolve("src/index.ts") });
const composition = await selectComposition({ serveUrl, id: "BackstepDemo" });
console.log(`duration ${composition.durationInFrames} frames`);
for (const frame of frames) {
  const output = path.resolve(`out/stills/f${String(frame).padStart(4, "0")}.png`);
  await renderStill({ serveUrl, composition, frame, output, scale });
  console.log(output);
}
