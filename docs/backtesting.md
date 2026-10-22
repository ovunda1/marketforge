# Backtesting (M8)

Code: `research/backtest/`, strategies in `research/strategies/`, runs in
`research/runs.py`. Two modes share the strategy-agnostic pieces: metrics, the
cost-model interface and run records.

```sh
make backtest CONFIG=research/examples/tsmom_walkforward.toml   # release install
make reproduce RECORD=runs/<run>/record.json                    # byte-identical or not
```

## Event-driven: strategy orders in the real engine

`run_event_backtest(quotes, trades, strategy, EventConfig(...))`.

**The loop** (`loop.py`). Every happening is an action at a simulated time:
- a data row;
- an agent waking up;
- a strategy order arriving;
- a timer.

Actions run in `(time, priority, scheduling order)`. A command gets the next `seq_no`
when it actually reaches the engine. It is journaled and processed by `mfengine.Engine`,
and its events are delivered at once: order events to the order's owner, and a
`MarketData` (top of book plus trades) to subscribers. At one timestamp, recorded
trades go first, then recorded quotes, then agents, then the strategy, then its timers.
The strategy losing ties to history is the conservative choice.

**Replay** (`historical.py`). The data has no order ids, so the replay keeps orders that
reproduce it:
- **Quotes:** orders of account 1 at the displayed price and size, reconciled with each
  quote row. A new price means cancel and repost; more size is a new order at the back;
  less size shrinks orders, which keeps their priority. These orders are post-only, so
  a quote never takes the strategy's liquidity.
- **Trades:** each print is an IOC from account 2, limited at the print price.

**The broker** (`broker.py`). Intents (`ctx.buy/sell/cancel/replace`) reach the engine
`latency_ns` plus seeded jitter later, never overtaking each other (one session).

| Assumption | Default (conservative) | Opt-in |
| --- | --- | --- |
| Queue position when displayed size shrinks without a trade (`queue`) | `pessimistic`: the newest orders (behind the strategy) were cancelled, so the queue ahead does not shrink | `optimistic`: the oldest were, so the strategy moves up |
| Liquidity the strategy took (`consumed_liquidity`) | `stays_removed`: what it took at a price stays taken until the level clears (price moves or the side empties) | `restore`: the next quote update puts it back |
| Same-timestamp ties | history before the strategy | none |
| Quotes vs strategy orders | a quote never trades with the strategy; a level that would is left out | none |
| Marking an open position | at the exit side (long at the bid, short at the ask) | none |

Every run record carries these, with the latency and jitter, under `assumptions`.

**What this cannot model:**
- **Nobody reacts to the strategy.** No impact on other participants' prices, no
  information leakage, no one stepping in front. Live mode (`run_live_backtest`) puts the
  strategy among the synthetic agents, who do react, but they are toys.
- **Depth beyond L1.** A sweep past the top level finds nothing, and tape trades that
  printed inside the replayed spread (the two files' clocks differ) find nothing to hit.
- **True queue position.** L1 data cannot say where cancels happened; hence the
  pessimistic default.
- **Hidden and iceberg liquidity, fees** (M9), and **market-data latency** (the
  strategy sees each update at the time it happened; only orders are delayed).

**Look-ahead** is prevented by construction. A strategy gets a `Context`:
- with no engine and no dataset (`__slots__`, no attribute to reach them);
- whose `view` holds only the market data already delivered, as read-only arrays;
- where `view.asof(t)` for `t > now` raises `LookAheadError`.

The future is not in the strategy's reach (`tests/test_backtest_event.py::Peeker`).

## Vectorized: bars for parameter sweeps

`run_vectorized(panel, weights, capital, BarExecution(lag, price))`. Weights are decided
at the close of bar `t`.

- **When a signal trades.** At bar `t + lag` with `lag >= 1`, at that bar's open
  (default) or close. `lag = 0`, trading on the bar that made the signal, cannot be
  configured.
- **Sizing.** `trunc(w * capital / close[t])` shares, off the fixed starting capital,
  toward zero. Shares are re-sized only when a symbol's weight changes.
- **Corporate actions.** Splits scale positions on the ex-date, with fractions paid in
  cash. Dividends are paid on shares held into the ex-date. Prices are unadjusted
  (`docs/data.md`).
- **Fills.** The whole order fills at the open with no impact; gaps and halts are taken
  at face value.
- **Integers.** Money and shares are int64; floats only size and summarize.

**Look-ahead** in a vectorized strategy is checked mechanically, since `weights(panel)`
sees the whole panel (`causality.py`). A strategy's weights up to bar `t` must be
bit-identical when everything after `t` is removed, and again when it is replaced with
garbage bars. The second catches full-sample statistics. Every bar run and every
walk-forward grid entry passes the check at five cut points before it is used. The
cross-sectional strategy's month-end rule exists because of it. Knowing "today is the
last trading day of the month" needs tomorrow's date, so rebalancing happens on the
first bar of the new month.

## Walk-forward

`walk_forward(panel, factory, grid, folds, capital)`. For each fold, parameters are
chosen on the training window and run on the test window after it. The selector is
handed `panel.upto(train_end - 1)`, so later bars do not exist for it. The result type
has fields only for the chosen parameters and the out-of-sample P&L. In-sample numbers
are never part of a result, so a sweep cannot report them. `tests/test_walkforward.py`
shows what that prevents:
- the best of 40 noise strategies averages an in-sample Sharpe above 1.5;
- the same selection out of sample stays within three standard errors of zero.

## Costs

`costs.py`: a `CostModel` maps `TradeRecord`s to integer costs. M8 ships `ZeroCost`, and
results are gross. M9 adds the real models behind the same interface. The bar backtester
already subtracts whatever the model returns (net equity), and configs select the model
by name (`[costs] model`).

## Run records

`research/runs.py`, `python -m research run|reproduce`. A record stores:
- the canonical config, with defaults filled in;
- each dataset's content hash (generated data: its parameters too);
- the seed and the assumptions;
- the engine version, build type and invariant-check flag;
- the git commit and dirty flag, library versions and platform;
- the SHA-256 of `results.json`.

No wall-clock time is stored. Results are canonical JSON, with floats at 12 significant
digits.

`reproduce` refuses if a dataset's hash or the engine version differs, and notes any
other difference. It then reports whether the new results are byte-identical. The three
committed example runs (`research/examples/runs/`) were made with the release install.
The test suite reproduces them on the dev install, a different build type, byte for
byte.
