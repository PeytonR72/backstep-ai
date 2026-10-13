# Backstep landing page — awwwards brief

Run with:

```
/awwwards Build the Backstep front page. Follow the brief in docs/landing-page-brief.md exactly, it is the signed-off Phase 1 brief.
```

---

## Phase 1 — Brief (signed off, do not re-elicit)

**Purpose.** Explain in five seconds what Backstep is ("an undo button for AI agents"), then prove it is real, careful engineering.

**Audience.** Two readers, in this order:
1. **A technical recruiter or hiring engineer** scanning a portfolio. They give it 30 seconds. They must leave thinking "this person ships thoughtful, rigorous work." They look for clarity, honesty, real numbers, and a link to the code.
2. **A curious non-expert.** They do not know what MCP or a WHERE clause is. They must still get it and think it looks cool.

**Who screenshots this and why.** A recruiter forwards it to the hiring manager because the hero demo explains the whole product without a word of jargon.

**Register.** Brand: the page is a showcase, but for a developer tool. "Polished and precise" beats "loud." Think Linear, Vercel or Raycast product pages, not an agency portfolio.

**Aesthetic direction.** *Precision instrument, dark.* A calm, near-black surface. One confident green signal colour. Crisp hairline borders. Monospace detail where the product is literally code. Generous negative space. Motion is rare and meaningful, so that when something moves it is the product doing its job. Commit to restraint. No gradient soup, no floating blobs, no stock 3D shapes.

**The unforgettable thing: the rewind.** The hero is a live, self-playing mini demo:
1. A small `accounts` table shows four rows: alice 100, bob 250, carol 75, dave 500.
2. An "agent" line types out a bad `UPDATE accounts SET balance = 0` with no WHERE clause. Every row flips to 0 and flashes the error red.
3. A beat later `backstep undo` runs. The rows **rewind** back to their exact original values, and the number counters roll backwards. The rewind motion follows the curve of the Backstep logo's arrow: the logo's arrow path draws itself and the rows follow it.
4. It settles on a caption: "One command. Exact original state."

It loops gently, pauses when off-screen, and has a "Replay" control. With `prefers-reduced-motion`, it shows the before, broken and restored states as three static frames. The demo must be readable by the non-expert: plain labels, with the money obvious.

**Constraints.**
- **Hard rule: use the existing logo.** Do not redraw, restyle or invent a new mark. Use the files in `docs/media/brand/`:
  - `backstep-logo.svg`: translucent tile, for the nav and dark surfaces
  - `backstep-logo-solid.svg`: favicon and any light surface
  - `backstep-arrow.svg`: bare arrow, the source for the hero path-draw animation. Animate its existing path; do not alter its geometry.
  - `backstep-logo-512.png`, `-180.png`, `-32.png`: the Open Graph image, apple-touch-icon and favicon fallback
- **Architecture:** Branch A, a single HTML file with no build step, at the repo root as `index.html` (so GitHub Pages serves it instead of the README), with assets referenced relatively from `docs/media/`. It must work when opened from disk and on GitHub Pages.
- **Performance:** a fast first paint, with nothing in the first viewport waiting on a heavy library. Do not use Three.js unless it clearly earns its weight (it probably doesn't here). Lazy-load the demo video.
- **Accessibility:** WCAG AA contrast, visible focus rings, a sensible tab order, touch targets of at least 44 px, `prefers-reduced-motion` respected everywhere, and real text (not text baked into images).
- **Viewports:** 320, 768, 1280 and 1920 px. At 320 px the hero demo stacks and stays legible.
- **Honesty:** no fake logos, testimonials, user counts or "trusted by" strips. Every number on the page comes from the facts list below.

---

## Phase 2 — Palette (existing brand, Branch A: extract, don't invent)

The brand already exists, so use these colours, taken from the logo and demo video, and convert them to OKLCH tokens:

| Role | Hex | Token |
|---|---|---|
| Background | `#0B0D10` | `--bg` |
| Raised surface | `#111419` / `#161A21` | `--surface`, `--surface-2` |
| Hairline border | `#232832` / `#2E343F` | `--line` |
| Primary text | `#E8EAED` | `--ink` |
| Secondary text | `#8B94A3` | `--ink-2` |
| Muted text | `#5A6270` | `--ink-3` |
| Signal green (logo) | `#3DDC97` | `--accent` |
| Deep green | `#2F8F66`, tint `#13221C` | `--accent-deep`, `--accent-tint` |
| Error (the agent's mistake) | `#FF5C5C` | `--danger` |
| Warning / partial | `#E3C27A` | `--warn` |

Commitment level: **restrained.** Green appears on 10% of the page or less: the logo, the "restored" state, primary CTAs and key numbers. Red appears only in the mistake moment of the demo.

## Typography direction

- **Code, numbers and terminal: JetBrains Mono**, which matches the demo video.
- **Display and body:** the video used Inter, but the skill rejects Inter. Pick one characterful but sober sans for headings, paired with a highly readable body face, and justify the pick in a single sentence. It should feel engineered, not quirky.
- **Hero h1, locked:** "An undo button for AI agents."

## Phase 3 — Inspiration (21st.dev is on the free tier)

- Use `mcp__21st__search` and `mcp__21st__get_inspiration` freely for patterns: terminal or code-window heroes, bento feature grids, benchmark/results tables, a sticky minimal nav.
- `mcp__21st__get_component` allows **at most 2 retrievals a day.** Spend them only on the hero code window and the bento grid.
- `generate` is **not available**, so write all component code yourself.
- `search_logo` is only for real tech-stack logos (Python, Postgres, Claude/Anthropic), and only if used in an honest "Built with" row.

## Phase 5 — Motion lens

**Polish**, everywhere. Use measured easing, short durations (150–400 ms for UI, longer only for the hero rewind), and scroll-reveals that are subtle and run once. The single expressive moment is the hero rewind; nothing else should compete with it.

---

## Page structure

1. **Nav.** The logo (`backstep-logo.svg`) and "Backstep" wordmark in live text; links to How it works, Benchmark and GitHub; a "View on GitHub" button.
2. **Hero.** The h1, then this subhead: "Backstep sits between an AI agent and the tools it calls. Before the agent changes any data, it records what that data looked like, so any mistake can be reviewed and rolled back with one command." CTAs: **View on GitHub** (primary) and **Watch the 1-min demo** (secondary, which opens the video). The live rewind demo sits beside the copy on desktop and below it on mobile.
3. **The problem, in plain English.** Three short beats: agents now have write access to real databases; they still make mistakes (a wrong WHERE clause, an insert loop that runs 200 times); and today's options are to keep them read-only or hope for the best. Backstep is the third option.
4. **How it works.** Five steps, Intercept → Classify → Capture → Journal → Revert, as a horizontal flow that animates step-by-step on scroll. Each step has one plain-English line, with the technical detail shown on hover or expand for engineers.
5. **Reversibility classes R0–R4.** A clean scale, from green (R0 read-only) through to red (R4 irreversible, blocked). This is a natural visual: a gauge or graded bar.
6. **Benchmark: "Measured, not just tested."** Show the real six-scenario table, with count-up numbers for the recovery times. Headline: **3 full recoveries, 1 partial, 2 correct refusals.** Explicitly highlight that the refusals are the passing outcome, and give the honest partial result for `cascading_delete`. This honesty is the recruiter hook, so make it a feature, not fine print.
7. **Engineering approach.** A bento grid: Refuse instead of guess (10 revert outcomes, only 1 modifies data) · Mutation guard tests · Lossless serialization · Append-only journal · Real Postgres tests (110+ tests, mypy, ruff).
8. **Demo video.** `docs/media/backstep-demo.mp4`, with `backstep-demo-poster.png` as the poster. Use `preload="none"`, native controls, and captions if possible.
9. **Getting started.** Four commands in a copyable terminal block: `uv sync`, `uv run backstep init`, `uv run backstep doctor` and `uv run backstep serve`.
10. **Footer.** Logo, MIT license, GitHub link, and "Built with Python, MCP, Postgres, sqlglot."

## Facts list (the only numbers allowed)

- Works with any MCP client (Claude Desktop, Claude Code, Cursor), with no changes to the agent or the upstream server.
- Benchmark recovery times: wrong_where_update 28 ms full; runaway_insert_loop (200 INSERTs) 2.7 s full; mixed_session 87 ms full; cascading_delete 37 ms partial; partial_drift 3 ms correctly refused; unparseable_sql 17 ms correctly refused.
- 10 distinct revert outcomes, only one of which modifies data.
- Over 110 tests, fully typed with mypy, linted with ruff.
- Python 3.11+, MIT license.

---

## Overrides to the skill defaults

- **Ignore the Switchyard and Sam references** in the skill: the Railyard rules, the sodium amber colour, `~/skills-showcase` as the reference build, and the agency pricing copy. They belong to the skill author's projects, not to this one.
- **Do not copy the "Calibration Lab" look.** No iridescent icosahedron, orbit text rings or audit-theatre pins. Our unforgettable thing is the rewind demo.
- **Skills that are not installed:** `color-expert`, `color-palette-extractor`, `design-motion-principles`, `agent-browser` and `remotion-best-practices`. The palette and motion lens are already decided above. For Phase 9 verification, use the built-in browser pane: screenshots at all four viewports, a keyboard tab-through, and a check that the demo loops, pauses and replays.
- **Copy voice:** plain, confident and short. No hype words ("revolutionary", "supercharge"). No em-dash-heavy marketing tone.

## Definition of done

- [ ] It opens from disk and every asset loads
- [ ] The logo is used unaltered in the nav, favicon and OG image
- [ ] The hero rewind demo loops, pauses off-screen, has Replay, and has a reduced-motion fallback
- [ ] Every number matches the facts list
- [ ] It passes AA contrast, keyboard navigation works, and there is no horizontal scroll at 320 px
- [ ] `impeccable` audit pass done
- [ ] Browser screenshots at 320, 768, 1280 and 1920 reviewed
