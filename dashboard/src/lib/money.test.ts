import { describe, expect, it } from "vitest";

import { fixture } from "./fixtures";
import { formatInt, formatTicks, InputError, parsePrice, parseQty } from "./money";

interface Cases {
  parse_price: { text: string; tick_size: string; ticks?: number; error?: string; suggestions?: string[] }[];
  parse_qty: { text: string; qty?: number; error?: string }[];
  format: { ticks: number | string; tick_size: string; text: string }[];
}
const cases = fixture<Cases>("ticks/cases.json");

function caught(fn: () => unknown): InputError {
  try {
    fn();
  } catch (e) {
    if (e instanceof InputError) return e;
    throw e;
  }
  throw new Error("expected an InputError");
}

describe("money.ts agrees with services/common/money.py on the shared cases", () => {
  for (const c of cases.parse_price) {
    it(`price ${JSON.stringify(c.text)} at ${c.tick_size}`, () => {
      if (c.ticks !== undefined) expect(parsePrice(c.text, c.tick_size)).toBe(c.ticks);
      else {
        const e = caught(() => parsePrice(c.text, c.tick_size));
        expect(e.kind).toBe(c.error);
        if (c.suggestions) expect(e.suggestions).toEqual(c.suggestions);
      }
    });
  }
  for (const c of cases.parse_qty) {
    it(`qty ${JSON.stringify(c.text)}`, () => {
      if (c.qty !== undefined) expect(parseQty(c.text)).toBe(c.qty);
      else expect(caught(() => parseQty(c.text)).kind).toBe(c.error);
    });
  }
  for (const c of cases.format) {
    it(`format ${c.ticks} at ${c.tick_size}`, () => expect(formatTicks(c.ticks, c.tick_size)).toBe(c.text));
  }
});

describe("no rounding anywhere", () => {
  it("never produces a float or an unsafe integer", () => {
    for (const c of cases.parse_price.filter((x) => x.ticks !== undefined)) {
      expect(Number.isSafeInteger(parsePrice(c.text, c.tick_size))).toBe(true);
    }
  });
  it("refuses unsafe numbers when formatting", () => {
    expect(() => formatTicks(2 ** 53 + 2, "0.01")).toThrow();
    expect(formatTicks("9007199254740993", "1")).toBe("9007199254740993");
  });
  it("0.1 + 0.2 style inputs are exact", () => {
    expect(parsePrice("0.3", "0.1")).toBe(3);
    expect(caught(() => parsePrice("0.30000000000000004", "0.01")).kind).toBe("off_grid");
    expect(parsePrice("1.15", "0.05")).toBe(23); // 1.15 / 0.05 in floating point is 22.999999999999996
  });
  it("groups digits, big values included", () => {
    expect(formatInt(1234567)).toBe("1,234,567");
    expect(formatInt("-123456789012345678901")).toBe("-123,456,789,012,345,678,901");
  });
});
