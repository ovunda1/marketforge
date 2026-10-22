# Risk gate and sessions (M5)

Every order passes a pre-trade risk gate before it reaches the book. A failed check
produces a `Reject` with a specific reason code and never touches book state. The gate
runs on the live path and on replay alike, so **replaying a journal reproduces every
risk reject exactly**. That constraint drives the design below.

Implemented in `engine/src/risk.cpp` (`RiskGate`), `engine/src/engine.cpp` (`Engine`)
and `engine/src/risk_config.cpp` (YAML). Golden scenarios are in
`engine/tests/golden/risk/`.

## Where the gate sits

```text
client -> Sequencer -----------------> Engine::process(command, timestamp)
          seq_no + timestamp             1. seq_no check
          journal append (write-ahead)   2. RiskGate::check_new / check_replace
                                            (session, kill switch, rate, the book's
                                             static checks, duplicate id, limits)
                                         3. OrderBook::process (matching, dynamic checks)
                                         4. RiskGate::apply(events)  <- state from events
```

- **The sequencer still never rejects.** Every command is journaled before the engine
  sees it, including ones the gate rejects. A reject depends on engine state, so it is
  only known afterwards, and replay regenerates it from the same journal.
- **Admin commands are commands.** Limit changes, kill switches and session
  transitions get a `seq_no` and a journal record like orders, so replay sees each
  change at exactly the `seq_no` where it took effect. Each takes effect from the next
  command.
- **One engine per symbol.** Limits such as "account credit" and "open orders" are per
  account *on this symbol*. Aggregating across symbols would need a shared, ordered risk
  service across shards, which would break per-symbol determinism. Multi-asset
  margining is a SPEC non-goal.
- **A fresh engine is `CONTINUOUS` with no limits**, which is exactly M4 behavior. The M3
  goldens, the differential test and the committed `v1.mfj` hash are unchanged. A
  production journal starts with its config commands and an explicit session lifecycle.

## Time

The rate limiter is the only part of the engine that uses time. It takes the journal
record's timestamp, which the sequencer stamped from the injected `Clock`. On replay the
timestamp comes from the record, so no clock is read. The journal guarantees timestamps
never decrease; a decrease reaching the engine is a `SequenceError` (an
`InvariantViolation`), thrown by `Engine::process` before anything changes, for every
command and not only rate-limited ones (M7). An engine restored from a snapshot takes
the snapshot's timestamp as the bound.

In text command files, a `T <ns>` line sets the time for the commands after it. Time
starts at 0. `mf_driver` and `mf-journal` (step 0 by default) treat these lines
identically, so their hashes still match.

## Check order

**New order.** The first failure is reported.

| # | Check | Reason | Where |
| --- | --- | --- | --- |
| 1 | Session is not `CONTINUOUS` | `MARKET_CLOSED` | gate |
| 2 | Global kill switch, then the account's | `KILL_SWITCH` | gate |
| 3 | Token bucket (takes a token) | `RATE_LIMIT` | gate |
| 4 | Price shape | `INVALID_PRICE` | book, static |
| 5 | `qty > 0` | `INVALID_QTY` | book, static |
| 6 | Type and TIF combination | `INVALID_ORDER_TYPE` | book, static |
| 7 | Id already used this session | `DUPLICATE_ORDER_ID` | book |
| 8 | `qty <= max_qty` | `MAX_QTY` | gate, instrument |
| 9 | Price band | `PRICE_BAND` | gate, instrument |
| 10 | `notional <= max_notional` | `MAX_NOTIONAL` | gate, account |
| 11 | `open_orders + 1 <= max_open_orders` (orders that can rest) | `MAX_OPEN_ORDERS` | gate, account |
| 12 | Worst-case position within `+-position_limit` | `POSITION_LIMIT` | gate, account |
| 13 | `open_notional + notional <= credit` | `INSUFFICIENT_CREDIT` | gate, account |
| 14 | `NO_LIQUIDITY`, `POST_ONLY_WOULD_CROSS`, `FOK_NOT_FILLABLE` | | book, dynamic |

Why this order:
- **Market-wide and administrative blocks come first.** They are the cheapest checks
  and the most important to report.
- **The rate check precedes format checks**, so a flood of malformed orders still uses
  up the bucket.
- **Format checks precede limits.** The notional of a negative quantity means nothing.
- **Risk precedes the book's dynamic checks.** A risk reject must never touch the book,
  and the FOK walk is the most expensive check.

**Replace.**
1. `MARKET_CLOSED`.
2. `INVALID_PRICE`, `INVALID_QTY`, `UNKNOWN_ORDER`, `REPLACE_QTY_TOO_LOW`.
3. `KILL_SWITCH`, `RATE_LIMIT`.
4. Checks 8–13, on the new total qty and price, with the order's current leaves
   replaced by its new leaves.
5. `POST_ONLY_WOULD_CROSS`.

A replace names no account, so the order has to be found (step 2) before its account's
kill switch and bucket can be checked. As a result a malformed replace does not use up
a token. A replace that keeps its price and does not increase its quantity only reduces
risk, so it skips step 4.

**Cancel.** Never rate-limited, risk-checked or blocked by a kill switch. It is
accepted in every session state.

## The checks

All products and sums are computed in `__int128` (`Notional`), so `price x qty` can
never overflow; an oversized order is rejected cleanly. An unset limit is not checked.
An account without limits of its own uses the defaults; if there are none, no account
checks run.

- **`MAX_QTY`**: per instrument.
- **`MAX_NOTIONAL`**: `price x qty` per order, per account. For a market order, see
  below.
- **`MAX_OPEN_ORDERS`**: counts the account's resting orders. It applies only to orders
  that can rest (a limit GTC or DAY order). IOC, FOK and market orders never add a
  resting order, and rejecting them could block an order that reduces risk.
- **`POSITION_LIMIT`**: worst case, counting open orders. A buy needs
  `position + open_buy_qty + qty <= limit`; a sell needs
  `position - open_sell_qty - qty >= -limit`. Without the open orders, many small
  orders could each pass on their own.
- **`INSUFFICIENT_CREDIT`**: `open_notional + notional <= credit`, where `open_notional`
  is the sum of `leaves x price` over the account's resting orders on both sides.
  **Credit covers open orders only.** Once an order fills, its notional leaves the
  credit check, and the exposure it created is bounded by the position limit instead.
  A filled position therefore never uses up credit: credit limits how much an account
  can have *working*, and the position limit bounds what it *holds*.

## Price band

The **reference** is chosen in this order:

1. **The last trade price.** It is a real execution and survives into the next session
   as the previous close. One account cannot move it by posting a wide quote on a
   thin side; it could move a mid that way.
2. **The mid**, if there has been no trade and both sides have orders. It is compared
   as `2 x price` against `bid + ask`, so there is no half tick.
3. **The instrument's `reference_price`** (for example, the previous close).
4. **Nothing.** A limit order then skips the band check. `MAX_QTY` and `MAX_NOTIONAL`
   still bound a fat finger.

The reference is a fraction `num / den` (`den` is 2 for a mid). A price is in band when
`|price x den - num| x 10000 <= num x band_bps`. Everything stays integer.

### Market orders

A market order has no price, so it gets two protections instead:

- **Capped at the band edge.** With a band configured, a market order trades only up
  to `floor(ref x (1 + band))` (buy) or down to `ceil(ref x (1 - band))` (sell, at
  least 1). The remainder is cancelled as `UNFILLED`. The book receives the edge as the
  order's limit; the `Ack` still shows `type=MKT px=0`. See
  `golden/risk/market_band_cap`.
- **Rejected with no reference**, but only when a band is configured: without a
  reference there is nothing to cap the order at. With no band, a market order is
  never rejected for lacking a reference, so the M3 goldens are unchanged.

For `MAX_NOTIONAL` and credit, a market order is **valued by a read-only walk** of the
opposite side, up to the band edge when a band is set. The walk gives its true maximum
cost:
- it counts only quantity that exists, since the remainder would be cancelled;
- it skips the account's own orders, because self-trade prevention can only cancel
  them, which lets the sweep go deeper.

The walk runs only when a notional or credit limit is set. See
`golden/risk/market_walk`.

## Rate limit (token bucket)

- **Tokens are fixed point:** `kTokenScale = 1e9` units per order.
- **Refill:** a bucket refills `elapsed_ns x per_sec` units, capped at
  `burst x kTokenScale`. The multiply is done in 128 bits, so a gap of years cannot
  overflow.
- **Start:** a bucket starts full the first time the account sends an order while it
  has a rate limit.
- **Equal timestamps** add nothing, so a burst at one instant is limited to `burst`.
- **What costs a token:** a New or Replace that reaches the check, even if a later
  check rejects it. The limit is on messages. Cancels and admin commands cost nothing.
- **Limit changes:** lowering the burst clamps the current tokens. Removing the rate
  limit drops the bucket, so it starts full if a limit returns.
- **Valid ranges:** `per_sec` and `burst` are 1 to 1e9, so `burst x kTokenScale` fits
  in int64.

## Kill switch

- **Scope:** per account or global (`*`).
- **While engaged:** new orders and replaces are rejected with `KILL_SWITCH`; cancels
  still work.
- **`cancel_open`** (only valid together with engaging) also cancels every resting
  order in scope. The cancels go bids best price first, then asks, FIFO within a level,
  each as `CXL reason=KILL_SWITCH` after the `ADM` event. `CANCEL` without `ON` is
  refused (`AREJ INVALID_KILL_SWITCH`).
- **Independent switches:** releasing the global switch leaves account switches as
  they are.

## Sessions

| From \ To | PRE_OPEN | CONTINUOUS | HALTED | CLOSED |
| --- | --- | --- | --- | --- |
| PRE_OPEN | | yes | | yes |
| CONTINUOUS | | | yes | yes |
| HALTED | | yes | | yes |
| CLOSED | yes (session open) | | | |

Any other transition, including to the same state, gives `AREJ INVALID_TRANSITION` and
changes nothing.

- **Only `CONTINUOUS` accepts new orders and replaces.** Pre-open, halted and closed
  reject them with `MARKET_CLOSED`. Pre-open rejects them because there is no opening
  auction (a SPEC stretch goal), and resting orders without matching could cross the
  book.
- **Cancels are accepted in every state.** GTC orders persist through a halt and the
  close.
- **Close:** after the `SES` event, every DAY order is cancelled as
  `CXL reason=EXPIRED`, bids best first, then asks, FIFO within a level. A halt
  expires nothing.
- **Session open (`CLOSED -> PRE_OPEN`)** resets `used_ids` to the ids of orders still
  resting. A new order must not take a live GTC order's id. This bounds `used_ids`, and
  every snapshot, to one session.
- **Carried across sessions:** positions, limits, kill switches, buckets and the last
  trade price.

## Risk state

**Updated only from the engine's own events**, after the book runs:

| Event | Effect |
| --- | --- |
| `Ack` | adds the order (account from the command) to `open_`; adds `qty x price` to exposure, plus one open order |
| `Fill` | moves the account's position; reduces the order's leaves |
| `Cancelled` (any reason) | reduces the order's leaves |
| `Replaced` | removes the old `leaves x price` and adds the new |
| `Trade` | sets the last trade price |

An order whose leaves reach 0 leaves `open_`. A market or IOC order is open only within
its own command.

**Consistency check.** With `MF_CHECK_INVARIANTS`, after every command the per-account
exposure is recomputed by walking the book and compared with the incremental totals.

**Snapshots** store only what the book cannot rebuild (`docs/snapshot-format.md`):
- session state;
- instrument, default and per-account limits;
- kill switches;
- non-zero positions;
- rate buckets;
- the last trade price.

Open-order exposure is **rebuilt from the book's resting orders** on restore, so the
two cannot disagree. `Engine::from_state` rejects inconsistent risk state: unsorted or
duplicate accounts, invalid limits, a zero stored position, a bucket without a rate
limit or outside `0..burst`, a non-positive last trade price.

## Admin commands

| Command | Text | Event |
| --- | --- | --- |
| Session transition | `S <seq> <PRE_OPEN\|CONTINUOUS\|HALTED\|CLOSED>` | `SES seq= state=` |
| Instrument limits | `L <seq> <max_qty> <band_bps> <ref_px>` | `ADM seq= kind=INSTRUMENT_LIMITS` |
| Account limits | `A <seq> <acct\|*> <max_notional> <position> <credit> <max_open> <rate> <burst>` | `ADM seq= kind=ACCOUNT_LIMITS` |
| Kill switch | `K <seq> <acct\|*> <ON\|OFF> <CANCEL\|KEEP>` | `ADM seq= kind=KILL_SWITCH` |

- **Unset fields:** `-` marks a limit as unset. `rate` and `burst` are set together or
  both `-`.
- **Whole-set replacement:** a limits command replaces the whole set for its scope.
  One record therefore states the limits completely, and tightening a limit never
  touches orders already open.
- **Out-of-range values** give `AREJ INVALID_LIMITS` and change nothing. The ranges:
  - `max_qty >= 1`, `0 <= band_bps <= 10000`, `ref_px >= 1`;
  - the account limits `>= 0`;
  - `per_sec` and `burst` 1 to 1e9.

## Configuration (YAML)

```yaml
instrument:
  max_qty: 1000
  price_band_bps: 500        # 5%
  reference_price: 10000     # used until there is a trade or a two-sided book
accounts:
  default:                   # every account without its own entry
    max_notional: 1000000
    position_limit: 500
    credit: 5000000
    max_open_orders: 50
    rate: {per_sec: 100, burst: 20}
  1001:
    max_open_orders: 5
```

`load_risk_config` (library `mf_config`, built on yaml-cpp 0.9.0) turns the file into
admin commands, in a fixed order:
1. the instrument limits;
2. the defaults;
3. each account in ascending id, whatever its order in the file.

**Errors.** Unknown keys, non-integers, out-of-range values, a rate without a burst,
bad or duplicate account ids and malformed YAML all raise `ConfigError` naming the key.

**The engine never reads YAML.** `mf_driver --config` runs the commands first as
`seq_no` 1..k at time 0. `mf-journal --config` journals them the same way, so the
command file continues at `k + 1`. The live gateway (M10) will sequence the same
commands at startup, and the runtime admin endpoint will send the same command types.

## What is not here

- **Cross-symbol limits.** There is one engine per symbol (see above).
- **A `client_order_id` field.** `order_id` is client-assigned and already unique per
  session, so `DUPLICATE_ORDER_ID` covers the SPEC's `DUPLICATE_ID` (it is stricter: per
  session, not per account).
- **Opening auction** in pre-open.
- **Risk in the Python reference engine.** The reference stays book-only, so the
  differential test covers matching. The risk gate is covered by unit tests, C++-only
  goldens and the determinism tests.
- **Throttling malformed replaces.** See the replace check order above.
