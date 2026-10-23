import { describe, expect, it } from "vitest";

import { fixture } from "./fixtures";
import { summarizeRun, type RunMetrics } from "./runSummary";

const showcase = fixture<RunMetrics>("../../research/showcase/tsmom/metrics.json");

describe("the results page's first sentence", () => {
  it("says when a result is not statistically significant (the showcase)", () => {
    const { sentence, significant } = summarizeRun(showcase);
    expect(significant).toBe(false);
    expect(sentence).toBe("After costs, this strategy returned 2.19% a year (2.88% before costs) with a Sharpe ratio " +
      "of 0.24 (0.38 before costs). That is not statistically significant: allowing for the 6 parameter combinations " +
      "tried, the deflated Sharpe ratio is 0.35, below the 0.95 needed, so it could well be luck.");
  });
  it("says when it is", () => {
    const m: RunMetrics = { performance: { sharpe: { gross: 2.1, net: 1.9 }, annual_return: { gross: 0.12, net: 0.1 },
                                           dsr: { gross: 0.99, net: 0.97 } }, trials: { dsr_trials: 1 } };
    const s = summarizeRun(m);
    expect(s.significant).toBe(true);
    expect(s.sentence).toMatch(/statistically significant even allowing for the 1 parameter combination tried \(deflated Sharpe ratio 0\.97/);
  });
  it("handles losses and missing numbers", () => {
    const m: RunMetrics = { performance: { sharpe: { gross: -0.1, net: -0.3 }, annual_return: { gross: -0.01, net: -0.02 } } };
    expect(summarizeRun(m).sentence).toMatch(/^After costs, this strategy lost 2\.00% a year .* could not be assessed\.$/);
    expect(summarizeRun({}).significant).toBeNull();
  });
});
