import { describe, expect, it } from "vitest";

import { microDollars, pct } from "./format";

describe("formatting research numbers", () => {
  it("percentages, never a negative zero", () => {
    expect(pct(0.0219297999168)).toBe("2.19%");
    expect(pct(-0.0000001, 1)).toBe("0.0%");
    expect(pct(-0)).toBe("0.00%");
    expect(pct(-0.084)).toBe("-8.40%");
    expect(pct(null)).toBe("–");
  });
  it("micro-dollars, exactly, even beyond 2**53", () => {
    expect(microDollars(14906703000)).toBe("$14,906.70");
    expect(microDollars(5_000)).toBe("$0.01");
    expect(microDollars(4_999)).toBe("$0.00");
    expect(microDollars("-123456789012345678901")).toBe("-$123,456,789,012,345.68");
  });
});
