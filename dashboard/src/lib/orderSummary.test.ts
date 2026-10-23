import { describe, expect, it } from "vitest";

import { summarize, type Draft, type Top } from "./orderSummary";

const base: Draft = { symbol: "ACME", tickSize: "0.01", side: "buy", type: "limit", tif: "gtc", price: 4218, qty: 10,
                      postOnly: false };
const top: Top = { bestBid: 4216, bestAsk: 4218 };
const say = (d: Partial<Draft>, t: Top = top) => summarize({ ...base, ...d }, t);

describe("the order sentence", () => {
  it("asks for what is missing", () => {
    expect(say({ qty: null })).toMatch(/Enter a quantity/);
    expect(say({ price: null })).toMatch(/Enter a price/);
  });
  it("crossing limit orders fill now, the rest by time in force", () => {
    expect(say({})).toBe("Buy 10 shares of ACME at $42.18 or less. Sellers are offering $42.18, so it should start " +
      "filling right away; anything left waits in the book until you cancel it.");
    expect(say({ tif: "day" })).toMatch(/until you cancel it or the session closes\.$/);
    expect(say({ tif: "ioc" })).toMatch(/anything not filled immediately is cancelled\.$/);
    expect(say({ tif: "fok" })).toMatch(/only if all 10 shares can fill at once; otherwise nothing trades\.$/);
    expect(say({ side: "sell", price: 4216 })).toMatch(/^Sell 10 shares of ACME at \$42\.16 or more\. Buyers are bidding \$42\.16/);
  });
  it("resting limit orders wait, and IOC/FOK ones would do nothing", () => {
    expect(say({ price: 4210 })).toMatch(/The best offer is \$42\.18, so it won't fill yet: it waits in the book until you cancel it, or until a seller comes down to your price\./);
    expect(say({ side: "sell", price: 4300 })).toMatch(/until a buyer comes up to your price\./);
    expect(say({ price: 4210, tif: "ioc" })).toMatch(/so it would be cancelled without trading\.$/);
    expect(say({ price: 4210, tif: "fok" })).toMatch(/so it would be rejected without trading\.$/);
    expect(say({ price: 4210 }, { bestBid: null, bestAsk: null })).toMatch(/There are no sellers right now/);
  });
  it("post-only explains the reject before it happens", () => {
    expect(say({ postOnly: true })).toMatch(/Post-only: at this price it would trade .* so the exchange would reject it\. Move the price below \$42\.18\./);
    expect(say({ postOnly: true, price: 4217 })).toMatch(/Post-only: it only adds to the book/);
  });
  it("market orders never rest and need a counterparty", () => {
    expect(say({ type: "market", price: null, tif: "ioc" })).toBe("Buy 10 shares of ACME at the best prices " +
      "available now. Starting at $42.18; whatever can't fill immediately is cancelled, nothing waits in the book.");
    expect(say({ type: "market", side: "sell" }, { bestBid: null, bestAsk: 5 })).toMatch(/no buyers right now, so the exchange would reject it/);
  });
  it("uses the instrument's tick size and singular share", () => {
    expect(summarize({ ...base, tickSize: "0.05", price: 21, qty: 1 }, { bestBid: null, bestAsk: null }))
      .toMatch(/^Buy 1 share of ACME at \$1\.05 or less\./);
  });
});
