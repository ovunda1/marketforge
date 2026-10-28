# Dashboard (M11)

The web UI of the compose stack: <http://127.0.0.1:8080> after `make up`. React 18 and
TypeScript (strict), built by Vite into a static site that nginx serves (`deploy/
dashboard.Dockerfile`, `deploy/nginx.conf`); nginx also proxies `/api/*` (prefix
stripped) and `/ws/*` to the gateway, so the site and the API share one origin. The design
and its reasons are in `docs/learning/m11.md`.

```sh
make up                 # the stack, dashboard included
make test-dashboard     # tsc + Vitest (unit tests, including the shared fixtures)
make e2e                # Playwright (headless Chromium) on a fresh test stack; KEEP=1 keeps it
make facts              # results/tests.json: the test counts the Overview shows
cd dashboard && npm run dev   # Vite on :5173, proxying to a running gateway (MF_API_URL)
```

## Pages

| Route | What | Data |
| --- | --- | --- |
| `/` | Overview: architecture, engine throughput and latency, test counts, the 1M-command differential test, the showcase result | built in from results files (below) |
| `/market/:symbol` | Live market: status strip, price ladder, order form, trade tape, your account | `/ws/marketdata`, `/ws/executions`, REST |
| `/backtests` | every run on this server and the showcase runs | `GET /backtests` |
| `/backtests/new` | the backtest form | `GET /backtests/catalog`, `POST /backtests` |
| `/backtests/:id` | one run: summary sentence, progress, equity curve, drawdown, costs, gross vs net | `GET /backtests/{id}`, `/ws/backtests/{id}` |
| `/how-it-works` | the life of an order, links to the code and docs | static |

Every page carries the banner "Simulated market · fictional symbols · practice money
only". Live market lists only the instruments the exchange lists, all fictional (ACME,
GLOBEX, INITECH, SMOKE). Real tickers appear only on backtest pages, marked "historical
data".

## The Overview's numbers

Read at build time by `dashboard/facts.config.ts` (the `virtual:facts` module), never
typed in: `bench/results/replay.json` (`make bench`), `results/tests.json` (`make facts`),
`results/difftest.json` (`make difftest-long`), `research/showcase/*/metrics.json` and the
Tiingo manifest. Only the fields the page shows reach the bundle. A missing file shows
"Not recorded yet: run make …". An end-to-end test compares the rendered numbers with the
files.

## Live market

- `MarketFeed` (`src/lib/feeds.ts`) runs `/ws/marketdata/{symbol}` through `BookClient`
  (`src/lib/bookClient.ts`), a line-for-line port of `services/client.py`. A gap in
  `md_seq` or a new epoch (the matcher restarted) closes the socket and opens a new one,
  which starts with a snapshot; after `resnapshot` the server sends the snapshot by itself;
  a dropped connection reconnects with backoff. Renders are batched to one per frame.
- The status strip shows the connection state (an icon and a word), epoch, the journal's
  `seq_no`, `md_seq` and the round-trip time of `{"type": "ping"}` messages. "Show
  technical details" adds the feed's counters, close codes and the last requests (status,
  latency, API worker, request id), a `seq_no` column on the tape, and error codes.
- Orders: `POST /orders` with a fresh `client_order_id`; the result is a sentence
  (`src/lib/orderResult.ts`). Open orders, fills and positions refresh after each order
  and on every execution report, reading with `min_seq_no` so the projection has caught up.

## Prices: text to ticks

`src/lib/money.ts`, the twin of `services/common/money.py`. Input must be digits with an
optional fraction (`^[0-9]+(\.[0-9]+)?$`). The text and the tick size are both scaled to
`BigInt` integers with the same number of decimals; the price must be an exact multiple of
the tick, or the error names the two nearest valid prices ("Try 41.99 or 42.00"). The
result must be positive and at most 10^9 ticks (the gateway's bound). Formatting
multiplies ticks by the tick's digits and inserts the point as text; it accepts decimal
strings for money beyond 2^53. No `parseFloat`, `toFixed` or float arithmetic touches a
price (a source test checks). Both implementations run `tests/fixtures/ticks/cases.json`.

## Big integers

Every response is parsed by `parseJson` (`src/lib/json.ts`), which throws
`UnsafeIntegerError` on any integer beyond 2^53 - 1 instead of letting it round. The
gateway sends money aggregates and nanosecond timestamps as strings and refuses to send an
unsafe integer at all (docs/services.md). A source test forbids `Response.json()`.

## Accounts and keys

"Try trading" calls `POST /practice-accounts` (demo mode only) and keeps the key in memory
and `sessionStorage` (`mf.session`): it survives a reload, not closing the tab, and is not
shared with other tabs. Never `localStorage` (a source test and an end-to-end test check).
A 401 forgets the key. A pasted key is checked with `GET /accounts/me`. Practice accounts
are traders; they may queue backtests only through the form, two at a time.

## Backtests

The form validates as you type (`src/lib/backtestForm.ts`, the twin of
`services/common/backtest_form.py`, both run `tests/fixtures/backtest_form/cases.json`)
against the catalog: dates inside the data, enough trading days for one training plus one
test window, the longest lookback inside the training window, a universe the strategy can
use, at most 12 parameter combinations. A run page leads with one sentence
(`src/lib/runSummary.ts`) that says, from the deflated Sharpe ratio, whether the result is
statistically significant (at least 0.95) or could well be luck, and shows how many runs
this server has made, failed ones included, with a note on overfitting.

Charts are hand-written SVG (`src/components/Charts.tsx`): 2 px lines, a recessive grid,
one axis, a legend plus end labels for two series, a crosshair tooltip on hover and on the
arrow keys, and a year-end data table under each chart. The series colors (net `#c07a24`,
gross `#4f8ef0`) pass the dataviz palette validator on the dark surface.

## Accessibility

Buy and sell always carry an arrow and a word, never color alone; the side toggle and the
segmented controls are real radio buttons (arrow keys work); every ⓘ is a button that
opens on focus and closes on Esc; the account tabs follow the tab pattern; the tour is a
non-modal dialog that takes focus and closes on Esc. Axe (WCAG 2.1 A/AA, serious and
critical) runs on every page at desktop and phone width (375 px, no sideways scroll) in the
end-to-end suite.

## Tests

| Suite | What |
| --- | --- |
| Vitest (`src/lib/*.test.ts`) | the book client and money parser against the shared fixtures; the order sentence; the form rules (shared cases); reject messages; the run summary; key storage; source rules |
| pytest | the same fixtures for the Python side (`tests/services/test_client_fixtures.py`, `test_backtest_form.py`); the gateway's M11 behavior (`test_dashboard_api.py`); content (`test_dashboard_content.py`) |
| Playwright (`dashboard/e2e/`) | the Overview against the results files; ladder vs `GET /book`; a matcher restart; trading end to end; reject messages; keyboard-only trading; key storage; a backtest from the form to its result; the practice cap; axe and phone width on every page; the tour; tooltips; the docs' Authorize button. Screenshots: `dashboard/test-results/screenshots/` |
