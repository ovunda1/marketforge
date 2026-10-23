# Backtesting (M8; costs and analytics M9)

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

## Date range and universe (M11)

A bar run's `[data]` may narrow its dataset: `start` and `end` (ISO dates, inclusive) and
`symbols` (a list). The filters are applied after the dataset is loaded and validated, so
its hash (and the manifest pin) is the whole file's; the run record's data entry carries a
`filter` with what was kept. An unknown symbol, or nothing left, is a config error. The
dashboard's backtest form (`services/common/backtest_form.py`) produces such configs: a
walk-forward of `train_years` x 252 trading days, then 252 of testing, with the
showcase's cost models when costs are on.

## Costs (M9)

`research/backtest/costs.py`. A `[costs]` table turns components on, one sub-table each
(`enabled = false` keeps one written down but off). Every cost is whole money units per
trade, rounded up; a rebate rounds toward zero. Fees given in dollars convert exactly
(the dataset's price unit: micro-dollars for bars, the instrument's tick for events).

| Component | Charge | Modes |
| --- | --- | --- |
| `commission` | `per_share` dollars + `bps` of notional | bar, event |
| `maker_taker` | per share and bps by the fill's liquidity flag; negative is a rebate; bar fills take | bar, event |
| `sec_fee` | Section 31 on sales, at the rate in force on the trade date (`schedule = "sec31"`: `research/backtest/schedules/sec31.json`, every row sourced from the SEC's fee advisories) | bar, event |
| `spread` | `half_spread_bps` of notional | bar |
| `slippage` | fixed `bps` | bar |
| `vol_slippage` | `k * vol * notional` | bar |
| `impact` | square root: `y * vol * sqrt(notional / ADV) * notional` | bar |
| `latency` | `k * vol * sqrt(latency / session) * notional` | bar |
| `financing` | `borrow_bps` a year on short value; `spread_bps` a year over the risk-free rate on a debit balance; actual/360 | bar |

**Not counting the spread twice.** An event-mode fill comes out of the engine's book: a
marketable buy pays the ask, and the open position is marked at the exit side. The
spread, the queue and the latency are already in gross. So event mode charges fees only,
and building a spread, slippage, impact, latency or financing model for it is an error.
What the book cost is *measured* instead, as implementation shortfall: `delay` (the mid
moving from the strategy's decision to the order's arrival) and `spread_paid` (each fill
against the arrival mid), labeled "measured, inside gross". Bar fills are at the open or
close print, which has no spread in it: bar mode charges the half-spread, and treats
every fill as a taker.

**Point-in-time impact inputs.** For a fill on bar `e`, `vol` (sample std of daily
total returns) and `ADV` (mean of `volume * close`, both unadjusted from the same bar,
so a split does not move it) come from bars `e - window .. e - 1` (`[costs.inputs]`,
default 20). An open fill cannot know its own bar's volume; `TradeRecord.volume` is
kept for the record and no cost reads it. With fewer than `min_periods` returns, those
components charge `fallback_bps` (default 50) and the trade is counted.
`tests/test_costs.py` changes the fill bar and later and checks the inputs do not move.

**Gross and net in the bar backtester.** Gross is frictionless: fills at the print,
dividends, splits, and every cash balance (short proceeds included) earning, or a debit
paying, the risk-free rate. Net subtracts per bar the trades' costs, `borrow`,
`financing`, and `carry_on_costs` (the risk-free interest the costs paid so far would
have earned), so `net = gross - sum of components` exactly. A window's open positions
are liquidated at its last close (`[execution] liquidate_at_end`, default on): each
window starts flat, so otherwise entries are charged and exits are not. Gross does not
change (the position was marked there).

**Selection is on net.** Walk-forward picks the parameters with the best net excess
Sharpe on the training window: costs are known before trading.

## Analytics and the tearsheet (M9)

`research/analytics/`: `metrics.py` (pure functions), `report.py` (a run's
`metrics.json`), `tearsheet.py` (`tearsheet.html`, rendered from `metrics.json` alone).
Every run writes both next to `results.json`; the record holds each file's hash and
`reproduce` compares all three.

Conventions, recorded in each tearsheet:
- returns are P&L over the fixed starting capital; 252 periods a year for daily bars
  (anything else must be given as `[analytics] periods_per_year`);
- annual return is the mean per-period return times 252 (arithmetic, no `pow`, so the
  numbers are the same on macOS and Linux); volatility is the sample std (ddof 1) times
  sqrt(252);
- Sharpe uses excess returns over the risk-free return of each bar; Sortino the target
  downside deviation over all periods; Calmar the annual return over the whole-period
  maximum drawdown;
- the Sharpe standard error is reported i.i.d. (Lo) and with skew and kurtosis
  (Mertens/Opdyke), with the probabilistic Sharpe P(SR > 0) and the deflated Sharpe,
  whose hurdle is the expected best of N zero-skill trials, N being the grid size.
  None corrects for autocorrelation;
- hit rate, average win and loss and profit factor are over *episodes*: a run of bars a
  symbol is held with one sign. Holding period is FIFO, share-weighted.

The trials table states the grid size, folds, backtests run and capacity points. The
capacity curve (`[analytics] capacity`, capital in micro-dollars) re-runs the whole
walk-forward at each capital, so selection is net of that size's costs. Fills are never
capped; participation (notional over ADV) is reported.

`metrics.json` is portfolio-level only: `tearsheet.check_portfolio_level` refuses a key
named after a symbol or any long array other than the portfolio series (dates,
cumulative gross and net return, net drawdown). Every run passes through it, so the
Tiingo showcase outputs can be committed without data that could rebuild prices.

## Showcase (M9)

`research/showcase/{tsmom,xsmom}.toml` on the Tiingo universe with DTB3, `make showcase`.
Parameters (grids, folds, costs, capacity points) were committed before the first real
run; `research/showcase/TRIALS.md` logs every run on the real data. Outputs committed:
`record.json`, `metrics.json`, `tearsheet.html`. Results and their reading are in
`docs/learning/m9.md`.

## Run records

`research/runs.py`, `python -m research run|reproduce`. A record stores:
- the canonical config, with defaults filled in;
- each dataset's content hash (generated data: its parameters too);
- the seed and the assumptions;
- the engine version, build type and invariant-check flag;
- the git commit and dirty flag, library versions and platform;
- the SHA-256 of each output: `results.json`, `metrics.json`, `tearsheet.html` (M9).

No wall-clock time is stored. Results are canonical JSON, with floats at 12 significant
digits.

`reproduce` refuses if a dataset's hash or the engine version differs, and notes any
other difference. It then reports whether the new results are byte-identical. The three
committed example runs (`research/examples/runs/`) were made with the release install.
The test suite reproduces them on the dev install, a different build type, byte for
byte.
