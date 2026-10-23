// The backtest form's rules (M11), mirroring services/common/backtest_form.py so mistakes
// show as you type. The gateway re-checks every submission and is the authority; both
// implementations run tests/fixtures/backtest_form/cases.json and must report the same
// fields.

export const DAYS_PER_YEAR = 252;
export const TEST_DAYS = DAYS_PER_YEAR;
export const TRAIN_YEARS = [1, 2, 3, 4, 5];
export const MAX_GRID = 12;

export const STRATEGIES: Record<string, { label: string; minSymbols: number; params: Record<string, number[]> }> = {
  tsmom: { label: "Time-series momentum", minSymbols: 1, params: {
    lookback: [20, 60, 120, 250], vol_window: [20, 60], target_vol: [0.05, 0.1, 0.15], max_leverage: [1.0, 2.0] } },
  xsmom: { label: "Cross-sectional momentum", minSymbols: 5, params: {
    lookback_months: [3, 6, 12], skip_months: [0, 1], quantile: [0.2, 0.3, 0.5] } },
};

export interface CatalogSymbol { symbol: string; first: string; last: string }
export interface Dataset {
  id: string;
  label: string;
  source: string;
  real: boolean;
  start: string;
  end: string;
  calendar: string[];
  symbols: CatalogSymbol[];
  benchmark?: string;
  rates?: string | null;
  survivorship?: string;
}
export interface Catalog { datasets: Dataset[] }

export interface Form {
  dataset: string;
  strategy: string;
  params: Record<string, number[]>;
  start: string;
  end: string;
  symbols: string[];
  train_years: number;
  costs: boolean;
}

export interface FieldError { field: string; message: string }

const FIELDS = ["dataset", "strategy", "params", "start", "end", "symbols", "train_years", "costs"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

export function lookbackDays(strategy: string, params: Record<string, number[]>): number {
  if (strategy === "tsmom") return Math.max(...params.lookback!) + Math.max(...params.vol_window!);
  let months = 0;
  for (const a of params.lookback_months!) for (const b of params.skip_months!) months = Math.max(months, a + b);
  return months * 21;
}

function lowerBound(xs: string[], x: string): number {
  let lo = 0, hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid]! < x) lo = mid + 1; else hi = mid;
  }
  return lo;
}

function upperBound(xs: string[], x: string): number {
  let lo = 0, hi = xs.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (xs[mid]! <= x) lo = mid + 1; else hi = mid;
  }
  return lo;
}

export function tradingDays(calendar: string[], start: string, end: string): number {
  return Math.max(0, upperBound(calendar, end) - lowerBound(calendar, start));
}

const isInt = (v: unknown): v is number => typeof v === "number" && Number.isInteger(v);
const isNum = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function validate(raw: unknown, catalog: Catalog): FieldError[] {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return [{ field: "dataset", message: "The form is missing." }];
  const form = raw as Record<string, unknown>;
  const errors: FieldError[] = [];
  const err = (field: string, message: string) => errors.push({ field, message });
  const unknown = Object.keys(form).filter((k) => !FIELDS.includes(k)).sort();
  if (unknown.length) err("dataset", `Unknown field(s): ${unknown.join(", ")}.`);
  const ds = catalog.datasets.find((d) => d.id === form.dataset);
  if (!ds) err("dataset", "Choose one of the available datasets.");
  const strategy = typeof form.strategy === "string" ? form.strategy : null;
  const spec = strategy !== null && Object.prototype.hasOwnProperty.call(STRATEGIES, strategy) ? STRATEGIES[strategy] : undefined;
  if (!spec) err("strategy", "Choose a strategy.");

  const params = form.params;
  let paramsOk = false;
  if (spec) {
    if (typeof params !== "object" || params === null || Array.isArray(params)) {
      err("params", "Choose at least one value for every parameter.");
    } else {
      const p = params as Record<string, unknown>;
      paramsOk = true;
      for (const extra of Object.keys(p).filter((k) => !(k in spec.params)).sort()) {
        err(`params.${extra}`, `${strategy} has no parameter ${extra}.`);
        paramsOk = false;
      }
      for (const [name, allowed] of Object.entries(spec.params)) {
        const chosen = p[name];
        if (!Array.isArray(chosen) || chosen.length === 0) {
          err(`params.${name}`, "Choose at least one value.");
          paramsOk = false;
        } else if (chosen.some((v) => !isNum(v) || !allowed.includes(v)) || new Set(chosen).size !== chosen.length) {
          err(`params.${name}`, `Choose from ${allowed.join(", ")}.`);
          paramsOk = false;
        }
      }
      if (paramsOk) {
        const size = Object.keys(spec.params).reduce((n, k) => n * (p[k] as unknown[]).length, 1);
        if (size > MAX_GRID) {
          err("params", `That is ${size} combinations; choose at most ${MAX_GRID}.`);
          paramsOk = false;
        }
      }
    }
  }

  const start = form.start, end = form.end;
  let datesOk = true;
  for (const [name, value] of [["start", start], ["end", end]] as const) {
    if (typeof value !== "string" || !DATE.test(value)) {
      err(name, "Use a date like 2015-01-02.");
      datesOk = false;
    }
  }
  if (datesOk && ds) {
    if ((start as string) < ds.start) {
      err("start", `The data starts on ${ds.start}.`);
      datesOk = false;
    }
    if ((end as string) > ds.end) {
      err("end", `The data ends on ${ds.end}.`);
      datesOk = false;
    }
    if (datesOk && (start as string) >= (end as string)) {
      err("end", "The end must be after the start.");
      datesOk = false;
    }
  }

  const symbols = form.symbols;
  if (ds) {
    const bySymbol = new Map(ds.symbols.map((s) => [s.symbol, s]));
    if (!Array.isArray(symbols) || symbols.length === 0 || !symbols.every((s) => typeof s === "string")) {
      err("symbols", "Choose at least one symbol.");
    } else if (new Set(symbols).size !== symbols.length) {
      err("symbols", "A symbol is listed twice.");
    } else if (symbols.some((s) => !bySymbol.has(s))) {
      err("symbols", `Not in this dataset: ${symbols.filter((s) => !bySymbol.has(s)).join(", ")}.`);
    } else {
      if (spec && symbols.length < spec.minSymbols) {
        err("symbols", `${spec.label} ranks symbols against each other: choose at least ${spec.minSymbols}.`);
      }
      if (datesOk) {
        const empty = (symbols as string[]).filter((s) => bySymbol.get(s)!.last < (start as string)
                                                      || bySymbol.get(s)!.first > (end as string));
        if (empty.length) err("symbols", `No data in the chosen dates for ${empty.join(", ")}.`);
      }
    }
  }

  const trainYears: number | null = isInt(form.train_years) && TRAIN_YEARS.includes(form.train_years) ? form.train_years : null;
  if (trainYears === null) err("train_years", "Choose 1 to 5 years of training.");
  if (typeof form.costs !== "boolean") err("costs", "Choose whether to include costs.");

  if (ds && datesOk && trainYears !== null) {
    const have = tradingDays(ds.calendar, start as string, end as string);
    const need = trainYears * DAYS_PER_YEAR + TEST_DAYS;
    if (have < need) {
      err("range", `These dates hold ${have} trading days; walk-forward with ${trainYears} year(s) of training and ` +
        `1 year of testing needs at least ${need}. Start earlier or train for fewer years.`);
    }
    if (paramsOk && spec) {
      const needed = lookbackDays(strategy!, params as Record<string, number[]>);
      if (needed >= trainYears * DAYS_PER_YEAR) {
        err("train_years", `The longest lookback needs ${needed} trading days of history, more than ` +
          `${trainYears} year(s) of training (${trainYears * DAYS_PER_YEAR} days).`);
      }
    }
  }
  return errors;
}
