// Decimal text <-> integer ticks (M11), the dashboard's side of services/common/money.py.
// Both run tests/fixtures/ticks/cases.json and must agree case for case.
//
// No floating point anywhere: the text is split into digits and handled as BigInt, so
// "0.1" is exactly one tick of 0.1. A price off the tick grid is an error naming the two
// nearest valid prices, never rounded. Results are checked against the gateway's bounds
// before they become JSON numbers. The messages are in copy.ts.

import { inputErrors as E } from "../copy";

export const MAX_PRICE_TICKS = 1_000_000_000n; // services/common/safe_json.py
export const MAX_ORDER_QTY = 1_000_000_000n;

const PRICE = /^[0-9]+(\.[0-9]+)?$/;
const QTY = /^[0-9]+$/;

export type InputErrorKind = "empty" | "format" | "off_grid" | "not_positive" | "too_large";

export class InputError extends Error {
  constructor(public readonly kind: InputErrorKind, message: string, public readonly suggestions: string[] = []) {
    super(message);
    this.name = "InputError";
  }
}

/** "12.50" -> [1250n, 2]: the digits as an integer and the number of decimals. */
function scaled(text: string): [bigint, number] {
  const [whole, frac = ""] = text.split(".");
  return [BigInt(whole! + frac), frac.length];
}

function tick(tickSize: string): [bigint, number] {
  if (!PRICE.test(tickSize)) throw new Error(`bad tick size ${tickSize}`);
  const [units, places] = scaled(tickSize);
  if (units <= 0n) throw new Error(`tick size must be positive, not ${tickSize}`);
  return [units, places];
}

/** Ticks (number, bigint, or a decimal integer string) as text with the tick's decimals. */
export function formatTicks(ticks: number | bigint | string, tickSize: string): string {
  let n: bigint;
  if (typeof ticks === "number") {
    if (!Number.isSafeInteger(ticks)) throw new Error(`not a safe integer: ${ticks}`);
    n = BigInt(ticks);
  } else if (typeof ticks === "string") {
    if (!/^-?[0-9]+$/.test(ticks)) throw new Error(`not an integer: ${ticks}`);
    n = BigInt(ticks);
  } else n = ticks;
  const [units, places] = tick(tickSize);
  const neg = n < 0n;
  let digits = ((neg ? -n : n) * units).toString();
  const sign = neg ? "-" : "";
  if (places === 0) return sign + digits;
  digits = digits.padStart(places + 1, "0");
  return `${sign}${digits.slice(0, -places)}.${digits.slice(-places)}`;
}

/** A typed price -> ticks (a safe JS number), or an InputError. */
export function parsePrice(text: string, tickSize: string): number {
  const t = text.trim();
  if (!t) throw new InputError("empty", E.enterPrice);
  if (!PRICE.test(t)) throw new InputError("format", E.priceFormat);
  const [units, places] = tick(tickSize);
  const [value, decimals] = scaled(t);
  const scale = Math.max(decimals, places);
  const v = value * 10n ** BigInt(scale - decimals);
  const u = units * 10n ** BigInt(scale - places);
  if (v === 0n) throw new InputError("not_positive", E.pricePositive);
  if (v % u !== 0n) {
    const lower = v / u;
    const options = [lower, lower + 1n].filter((x) => x > 0n && x <= MAX_PRICE_TICKS).map((x) => formatTicks(x, tickSize));
    throw new InputError("off_grid", E.offGrid(tickSize, options), options);
  }
  const ticks = v / u;
  if (ticks > MAX_PRICE_TICKS) {
    throw new InputError("too_large", E.priceMax(formatTicks(MAX_PRICE_TICKS, tickSize)));
  }
  return Number(ticks);
}

/** A typed quantity (whole units) -> a safe JS number, or an InputError. */
export function parseQty(text: string): number {
  const t = text.trim();
  if (!t) throw new InputError("empty", E.enterQty);
  if (!QTY.test(t)) throw new InputError("format", E.qtyFormat);
  const q = BigInt(t);
  if (q === 0n) throw new InputError("not_positive", E.qtyPositive);
  if (q > MAX_ORDER_QTY) {
    throw new InputError("too_large", E.qtyMax(MAX_ORDER_QTY.toLocaleString("en-US")));
  }
  return Number(q);
}

/** Integers for display with thousands separators; strings (big values) via BigInt. */
export function formatInt(n: number | bigint | string): string {
  const b = typeof n === "number" ? BigInt(n) : typeof n === "string" ? BigInt(n) : n;
  const neg = b < 0n;
  const s = (neg ? -b : b).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return neg ? `-${s}` : s;
}
