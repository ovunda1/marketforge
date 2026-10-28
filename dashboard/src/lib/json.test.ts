import { describe, expect, it } from "vitest";

import { parseJson, UnsafeIntegerError } from "./json";

describe("parseJson refuses to round integers", () => {
  it("parses ordinary JSON", () => {
    expect(parseJson('{"a": [1, 2.5, "x", null, true], "b": 9007199254740991}')).toEqual(
      { a: [1, 2.5, "x", null, true], b: 9007199254740991 });
  });
  it("throws on an integer beyond 2**53 - 1, naming where", () => {
    expect(() => parseJson('{"cash": 9007199254740993}')).toThrow(UnsafeIntegerError);
    expect(() => parseJson('{"x": [1, -9007199254740993]}')).toThrow(/beyond 2\*\*53/);
    expect(() => parseJson("1e300")).toThrow(UnsafeIntegerError);
  });
  it("leaves big integers sent as strings alone", () => {
    expect(parseJson<{ cash: string }>('{"cash": "-123456789012345678901"}').cash).toBe("-123456789012345678901");
  });
  it("accepts floats that are not integers", () => {
    expect(parseJson("[0.1, 1339584197.05]")).toEqual([0.1, 1339584197.05]);
  });
});
