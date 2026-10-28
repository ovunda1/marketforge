import { describe, expect, it } from "vitest";

import { type Catalog, lookbackDays, tradingDays, validate } from "./backtestForm";
import { fixture } from "./fixtures";

const catalog = fixture<Catalog>("backtest_form/catalog.json");
const cases = fixture<{ cases: { name: string; form: unknown; error_fields: string[] }[] }>("backtest_form/cases.json").cases;

describe("backtestForm.ts agrees with services/common/backtest_form.py on the shared cases", () => {
  for (const c of cases) {
    it(c.name, () => {
      const errors = validate(c.form, catalog);
      expect([...new Set(errors.map((e) => e.field))].sort()).toEqual(c.error_fields);
      for (const e of errors) expect(e.message.length).toBeGreaterThan(10);
    });
  }
  it("counts trading days in the calendar, inclusive", () => {
    const cal = catalog.datasets[0]!.calendar;
    expect(tradingDays(cal, "2015-01-01", "2020-10-16")).toBe(1512);
    expect(tradingDays(cal, "2015-01-03", "2015-01-04")).toBe(0); // a weekend
  });
  it("lookback in days", () => {
    expect(lookbackDays("tsmom", { lookback: [60, 250], vol_window: [20, 60] })).toBe(310);
    expect(lookbackDays("xsmom", { lookback_months: [6, 12], skip_months: [0, 1] })).toBe(273);
  });
});
