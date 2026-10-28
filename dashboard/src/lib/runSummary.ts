// The one-sentence summary that leads every backtest results page (M11), including when the
// result is not statistically significant. Significance uses the deflated Sharpe ratio
// (metrics.performance.dsr, research/analytics): the probability that the strategy's true
// Sharpe ratio beats the best of `dsr_trials` zero-skill strategies. It must reach 0.95.

import { runSummary as R } from "../copy";
import { pct } from "./format";

export const DSR_THRESHOLD = 0.95;

interface GrossNet { gross: number | null; net: number | null }
export interface RunMetrics {
  performance?: { sharpe?: GrossNet; annual_return?: GrossNet; dsr?: GrossNet };
  trials?: { dsr_trials?: number; backtests?: number; grid_size?: number };
  period?: { start?: string; end?: string };
}

export function summarizeRun(m: RunMetrics): { sentence: string; significant: boolean | null } {
  const p = m.performance;
  const sharpe = p?.sharpe, ann = p?.annual_return;
  if (!sharpe || sharpe.net === null || sharpe.net === undefined || !ann || ann.net === null || ann.net === undefined) {
    return { sentence: R.noNumbers, significant: null };
  }
  const trials = m.trials?.dsr_trials ?? 1;
  const known = (x: number | null | undefined): x is number => x !== null && x !== undefined;
  const head = R.head(ann.net >= 0 ? R.returned(pct(ann.net)) : R.lost(pct(-ann.net)),
                      known(ann.gross) ? pct(ann.gross) : null, sharpe.net.toFixed(2),
                      known(sharpe.gross) ? sharpe.gross.toFixed(2) : null);
  const dsr = p?.dsr?.net;
  if (dsr === null || dsr === undefined || !Number.isFinite(dsr)) return { sentence: R.noDsr(head), significant: null };
  const tried = R.tried(trials);
  return dsr >= DSR_THRESHOLD
    ? { sentence: R.significant(head, tried, dsr.toFixed(2), DSR_THRESHOLD), significant: true }
    : { sentence: R.notSignificant(head, tried, dsr.toFixed(2), DSR_THRESHOLD), significant: false };
}
