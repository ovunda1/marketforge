// Rules checked on the source itself (M11).
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

const SRC = fileURLToPath(new URL("..", import.meta.url));
function files(dir: string): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? files(p) : /\.(ts|tsx)$/.test(f) && !/\.test\.ts$/.test(f) ? [p] : [];
  });
}
/** The code without comments (a comment may well say "never localStorage"). */
const code = (f: string) => readFileSync(f, "utf8").replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
const sources = files(SRC).filter((f) => !f.includes("/design/") && !f.endsWith("lib/fixtures.ts"));

describe("source rules", () => {
  it("never touches localStorage (API keys: memory or sessionStorage only)", () => {
    for (const f of sources) expect(code(f), f).not.toMatch(/localStorage/);
  });
  it("does no floating-point math on prices or quantities", () => {
    for (const name of ["lib/money.ts", "lib/orderSummary.ts", "components/OrderForm.tsx"]) {
      expect(code(join(SRC, name)), name).not.toMatch(/parseFloat|toFixed|Number\(\s*(text|value|price|qty)|\* ?0\.01|Math\.round/);
    }
  });
  it("parses every gateway response with the safe JSON parser", () => {
    for (const f of sources) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/\.json\(\)/); // fetch's Response.json() would round big integers
      if (!f.endsWith("lib/json.ts") && !f.endsWith("lib/session.tsx")) expect(text, f).not.toMatch(/JSON\.parse\(/);
    }
  });
});
