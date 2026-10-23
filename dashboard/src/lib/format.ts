// Display formatting for research numbers (M11). Prices and quantities use money.ts
// (integer ticks); these are for metrics, which are floats by nature (returns, ratios),
// and money in integer micro-dollars (numbers, or strings when beyond 2**53).

export function pct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  const s = (x * 100).toFixed(digits);
  return `${/^-0\.?0*$/.test(s) ? s.slice(1) : s}%`; // never "-0.0%"
}

export function signedPct(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  return `${x > 0 ? "+" : ""}${(x * 100).toFixed(digits)}%`;
}

export function num(x: number | null | undefined, digits = 2): string {
  if (x === null || x === undefined || !Number.isFinite(x)) return "–";
  return x.toFixed(digits);
}

/** Integer micro-dollars as dollars and cents, rounded half away from zero, exactly (BigInt). */
export function microDollars(v: number | string | null | undefined): string {
  if (v === null || v === undefined) return "–";
  const n = BigInt(typeof v === "number" ? Math.trunc(v) : v);
  const neg = n < 0n;
  const abs = neg ? -n : n;
  const cents = (abs + 5_000n) / 10_000n;
  const dollars = (cents / 100n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg && cents > 0n ? "-" : ""}$${dollars}.${(cents % 100n).toString().padStart(2, "0")}`;
}

export function year(date: string | null | undefined): string {
  return date ? date.slice(0, 4) : "–";
}
