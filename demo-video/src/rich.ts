// Builds terminal lines that look like what Rich prints for the real CLI:
// Table (HEAVY_HEAD box, italic centered title) and Panel (ROUNDED box).
import type { Seg } from "./components/Terminal";
import { colors } from "./theme";

const B = colors.textDim; // box drawing
const pad = (s: string, w: number) => s + " ".repeat(Math.max(0, w - s.length));

/**
 * `rows[r][c]` is a list of physical lines for that cell, already wrapped the
 * way Rich would wrap it at the terminal width used in the video.
 * `cellColor(c, value)` optionally colors a body cell.
 */
export const richTable = (
  title: string,
  columns: string[],
  rows: string[][][],
  cellColor?: (col: number, value: string) => string | undefined,
): Seg[][] => {
  const widths = columns.map((h, c) =>
    Math.max(h.length, ...rows.flatMap((r) => r[c].map((l) => l.length))),
  );
  const total = widths.reduce((a, w) => a + w + 3, 1);
  const rule = (l: string, m: string, r: string, f: string) =>
    l + widths.map((w) => f.repeat(w + 2)).join(m) + r;

  const out: Seg[][] = [];
  const lead = Math.max(0, Math.floor((total - title.length) / 2));
  out.push([{ t: " ".repeat(lead) }, { t: title, i: true, c: colors.text }]);
  out.push([{ t: rule("┏", "┳", "┓", "━"), c: B }]);
  const head: Seg[] = [{ t: "┃", c: B }];
  columns.forEach((h, c) => {
    head.push({ t: ` ${pad(h, widths[c])} `, b: true, c: colors.text });
    head.push({ t: "┃", c: B });
  });
  out.push(head);
  out.push([{ t: rule("┡", "╇", "┩", "━"), c: B }]);
  rows.forEach((r) => {
    const height = Math.max(...r.map((cell) => cell.length));
    for (let k = 0; k < height; k++) {
      const line: Seg[] = [{ t: "│", c: B }];
      r.forEach((cell, c) => {
        const v = cell[k] ?? "";
        line.push({ t: ` ${pad(v, widths[c])} `, c: cellColor?.(c, cell[0]) ?? colors.text });
        line.push({ t: "│", c: B });
      });
      out.push(line);
    }
  });
  out.push([{ t: rule("└", "┴", "┘", "─"), c: B }]);
  return out;
};

/** Rich Panel with a centered title, expanded to `width` characters. */
export const richPanel = (title: string, body: Seg[][], width: number): Seg[][] => {
  const inner = width - 2;
  const label = ` ${title} `;
  const left = Math.floor((inner - label.length) / 2);
  const right = inner - label.length - left;
  const out: Seg[][] = [
    [
      { t: "╭" + "─".repeat(left), c: B },
      { t: label, c: colors.text },
      { t: "─".repeat(right) + "╮", c: B },
    ],
  ];
  body.forEach((segs) => {
    const len = segs.reduce((a, s) => a + s.t.length, 0);
    out.push([
      { t: "│ ", c: B },
      ...segs,
      { t: " ".repeat(Math.max(0, inner - 2 - len)) + " │", c: B },
    ]);
  });
  out.push([{ t: "╰" + "─".repeat(inner) + "╯", c: B }]);
  return out;
};
