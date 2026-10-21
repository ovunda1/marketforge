# Order types, cancel-replace and self-trade prevention (M3, DAY in M5)

Rules implemented by `engine/src/order_book.cpp` and, independently, by the Python
reference engine in `reference/book.py`. Golden scenarios in `engine/tests/golden/`
show each rule as a concrete event stream.

## Reject vs. cancel

> **Reject when the order's contract can't be honored; Ack + Cancel when the outcome is
> within its contract.**

A `Reject` means the order was never accepted: it is the command's only event and the
book is unchanged. `Ack` followed by `Cancelled` means the order was accepted and some
or all of its quantity was then removed, for a reason carried on the event:

| `Cancelled.reason` | Meaning |
| --- | --- |
| `USER` | A `Cancel` command |
| `UNFILLED` | IOC or market remainder: it promised "fill what you can, cancel the rest" |
| `SELF_TRADE` | Self-trade prevention removed it |

So a market order into an empty side is rejected (`NO_LIQUIDITY`: there is nothing it
could do), but an IOC that finds nothing to fill is acked and cancelled in full (doing
nothing is a valid outcome of IOC). An unfillable FOK and a crossing post-only order are
rejects: honoring them is impossible by definition.

## Validation order

The risk gate (M5) adds checks around these. The full order, book and risk combined,
is in `docs/risk.md`. Within the book, both engines check a new order in this order and
report the first failure:

1. Price: a limit order needs `price > 0`; a market order must have `price == 0`
   (`INVALID_PRICE`).
2. Quantity: `qty > 0` (`INVALID_QTY`).
3. Type combination: a market order must be IOC and not post-only; a post-only order
   must be a GTC or DAY limit (`INVALID_ORDER_TYPE`).
4. Duplicate id: an order with this id was acked earlier in the session, even if it has
   since filled or been cancelled (`DUPLICATE_ORDER_ID`). A rejected order was never
   accepted, so its id stays free. The check lives in the book, not the sequencer, so
   replay reproduces it (M4). The set of used ids is reset at session open, keeping the
   ids of orders still resting (M5), which bounds its memory to one session.
5. Type-specific: market into an empty side (`NO_LIQUIDITY`), crossing post-only
   (`POST_ONLY_WOULD_CROSS`), unfillable FOK (`FOK_NOT_FILLABLE`).

A replace checks price, then quantity, then that the order is resting
(`UNKNOWN_ORDER`), then `qty > filled` (`REPLACE_QTY_TOO_LOW`), then post-only crossing.

## DAY (M5)

A DAY limit order behaves exactly like GTC (it rests, keeps priority, can be replaced
and stays DAY when re-priced) until the session closes. At the close it is cancelled
with `CXL reason=EXPIRED` (`docs/risk.md`). DAY is not valid on a market order.

## Market

No price; sweeps the opposite side best price first, FIFO within a level, with no price
limit. The remainder is cancelled (`UNFILLED`); a market order never rests. "Empty side"
means no resting orders on the opposite side at all. If the only liquidity belongs to
the same account the order is accepted and self-trade prevention handles it.

With a price band configured (M5), the risk gate caps a market order at the band edge:
it trades only within the band and the rest is cancelled as `UNFILLED`
(`docs/risk.md`).

## IOC

Matches up to its limit; the remainder is cancelled (`UNFILLED`). Not crossing, or an
empty side, gives Ack + `Cancelled` for the full quantity.

## FOK

Before touching the book, a read-only walk in priority order sums the quantity the
sweep would really get within the limit. If it is short, `FOK_NOT_FILLABLE` with no
other events and no book change. With self-trade prevention the walk mirrors the sweep:
other accounts' orders count; an own-account order is skipped under cancel-oldest (the
sweep would cancel it and continue) and ends the walk under cancel-newest or
cancel-both (the sweep would cancel the FOK there). So a FOK never partially fills, and
a rejected FOK never cancels the account's resting orders.

## Post-only

GTC limit only. If its price crosses the best opposite price, including an equal price,
it is rejected with `POST_ONLY_WOULD_CROSS`; there is no re-pricing. Crossing is about
the book, so it is rejected even when the crossing orders are its own account's.

## Cancel-replace

`Replace{order_id, price, qty}` keeps the order id. `qty` is the new **total**
quantity including what has already filled, so the new open quantity is
`qty - filled`; `qty <= filled` is rejected (`REPLACE_QTY_TOO_LOW`).

| Change | Time priority |
| --- | --- |
| Same price, quantity down or unchanged | Kept (a no-op modify never costs priority) |
| Same price, quantity up | Lost: moves to the back of its level |
| Price change | Lost: cancel + new; can match, remainder joins the back of the new level |

A price change matches as an aggressor using the order's stored STP mode; fills
continue its `cum_qty`. A resting post-only order replaced to a crossing price is
rejected and left as it was. The `Replaced` event reports `prio=KEPT` or `prio=LOST`.

## Self-trade prevention

The incoming order's mode decides (the gateway sets it from account configuration).
It is checked one maker at a time, when the sweep reaches a resting order of the same
account at the front of the queue:

| Mode | Resting own order | Incoming remainder | Sweep |
| --- | --- | --- | --- |
| Cancel-newest (default) | untouched | `Cancelled(SELF_TRADE)` | stops |
| Cancel-oldest | `Cancelled(SELF_TRADE)`, full open qty | continues | next maker; may fill, rest or be `UNFILLED` |
| Cancel-both | `Cancelled(SELF_TRADE)` | `Cancelled(SELF_TRADE)` | stops |

Fills before the self-match stand. A GTC limit cancelled by STP does not rest. Own
orders beyond the limit price are never touched. The resting order is cancelled in
full, not decremented by the overlapping quantity (decrement is a possible later mode).
