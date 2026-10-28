// The one-sentence summary that leads every backtest results page (M11), including when the
// result is not statistically significant. Significance uses the deflated Sharpe ratio
// (metrics.performance.dsr, research/analytics): the probability that the strategy's true
// Sharpe ratio beats the best of `dsr_trials` zero-skill strategies. It must reach 0.95.

import { pct } from "./format";

export const DSR_THRESHOLD = 0.95;

interface GrossNet { gross: number | null; net: number | null }
export interface RunMetrics {
  performance?: { sharpe?: GrossNet; annual_return?: GrossNet; dsr?: GrossNet };
  trials?: { dsr_trials?: number; backtests?: number; grid_size?: number };
  period?: { start?: string; end?: string };
}

function returned(x: number): string {
  return x >= 0 ? `returned ${pct(x)} a year` : `lost ${pct(-x)} a year`;
}

export function summarizeRun(m: RunMetrics): { sentence: string; significant: boolean | null } {
  const p = m.performance;
  const sharpe = p?.sharpe, ann = p?.annual_return;
  if (!sharpe || sharpe.net === null || sharpe.net === undefined || !ann || ann.net === null || ann.net === undefined) {
    return { sentence: "This run has no performance numbers yet.", significant: null };
  }
  const trials = m.trials?.dsr_trials ?? 1;
  const head = `After costs, this strategy ${returned(ann.net)}` +
    (ann.gross !== null && ann.gross !== undefined ? ` (${pct(ann.gross)} before costs)` : "") +
    ` with a Sharpe ratio of ${sharpe.net.toFixed(2)}` +
    (sharpe.gross !== null && sharpe.gross !== undefined ? ` (${sharpe.gross.toFixed(2)} before costs)` : "") + ".";
  const dsr = p?.dsr?.net;
  if (dsr === null || dsr === undefined || !Number.isFinite(dsr)) {
    return { sentence: `${head} Its statistical significance could not be assessed.`, significant: null };
  }
  const tried = `${trials} parameter combination${trials === 1 ? "" : "s"} tried`;
  if (dsr >= DSR_THRESHOLD) {
    return { sentence: `${head} That is statistically significant even allowing for the ${tried} ` +
      `(deflated Sharpe ratio ${dsr.toFixed(2)}, at least ${DSR_THRESHOLD} needed).`, significant: true };
  }
  return { sentence: `${head} That is not statistically significant: allowing for the ${tried}, the deflated ` +
    `Sharpe ratio is ${dsr.toFixed(2)}, below the ${DSR_THRESHOLD} needed, so it could well be luck.`,
           significant: false };
}
