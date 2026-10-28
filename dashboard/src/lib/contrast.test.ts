// Color contrast of the design tokens (M11b), read from app.css itself: text pairs meet
// WCAG AA (4.5:1), buy green and sell red included, as text and under white text, and the gold accent, used only as a line or fill, meets the 3:1 of
// non-text marks against white while text on gold (#111) still meets 4.5:1.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const css = readFileSync(fileURLToPath(new URL("../styles/app.css", import.meta.url)), "utf8");
const root = css.slice(css.indexOf(":root {"), css.indexOf("}", css.indexOf(":root {")));
function token(name: string): string {
  const m = root.match(new RegExp(`--${name}:\\s*#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})\\b`));
  if (!m) throw new Error(`--${name} not found in :root`);
  const h = m[1]!;
  return `#${h.length === 3 ? [...h].map((c) => c + c).join("") : h}`;
}
function luminance(hex: string): number {
  const [r, g, b] = [1, 3, 5].map((i) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}
function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

describe("design tokens", () => {
  it("computes contrast like WCAG", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#ffffff")).toBeCloseTo(4.48, 2);
  });
  it.each([["ink", "bg"], ["ink-2", "bg"], ["muted", "bg"], ["red", "bg"], ["bg", "red"], ["green", "bg"],
           ["bg", "green"], ["bg", "ink"], ["ink", "tint"], ["ink", "red-tint"], ["ink", "green-tint"]])("text %s on %s meets AA (4.5:1)", (fg, bg) => {
    expect(contrast(token(fg), token(bg))).toBeGreaterThanOrEqual(4.5);
  });
  it("gold is a visible line on white (3:1) and #111 text on gold meets AA", () => {
    expect(contrast(token("gold"), token("bg"))).toBeGreaterThanOrEqual(3);
    expect(contrast(token("ink"), token("gold"))).toBeGreaterThanOrEqual(4.5);
  });
  it("gold is never a text color", () => {
    const rules = css.replace(/\/\*[\s\S]*?\*\//g, "").split("}");
    for (const rule of rules) expect(rule, rule.trim()).not.toMatch(/(^|[;{\s])color:\s*var\(--gold\)/);
  });
});
