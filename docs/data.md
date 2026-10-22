# Market data (M8)

Code: `research/data/`. Everything here also runs offline: tests use small made-up
fixtures (`tests/fixtures/m8/`) and synthetic data (`research/sim/`), and the suite
fails any test that opens a network connection (`tests/conftest.py`).

## Sources and their terms

| Source | What | How it is pinned | Terms | Committed? |
| --- | --- | --- | --- | --- |
| Binance public archive, `data.binance.vision` | USD-M futures `bookTicker` (every top-of-book change) and `trades`, one day of KNCUSDT (2024-03-12) | SHA-256 per zip, as Binance publishes it next to each file (`research/data/manifests/binance-um-kncusdt-2024-03-12.json`) | Binance Terms of Use. Free to download; no licence to redistribute is granted | No: download script and checksums only |
| Tiingo end-of-day API | Daily OHLCV, dividends and split factors for 22 US ETFs, 2007-01-03 to 2024-12-31 | Content hash of each ticker's normalized bars, written by the first fetch (`--update-pins`) into `research/data/manifests/tiingo-etf-daily.json`, then committed | Tiingo terms: personal use, no redistribution. Needs a free API key | No: download script and pins only |
| Synthetic agent flow, `research/sim/market.py` | L1 quotes and trades from noise traders, a market maker and momentum traders trading through the engine | Seed (data regenerated and hash-checked on reproduce) | Ours | Generated on demand |
| Synthetic daily bars, `research/sim/bars.py` | OHLCV with splits and dividends for any symbol list | Seed | Ours | Generated on demand |

Considered and not used:

- **Stooq**: now serves a JavaScript proof-of-work page to scripts, so downloads cannot
  be automated.
- **LOBSTER samples**: their download links now lead to a JavaScript app. Their terms
  are academic, with no redistribution either.

The fixtures in `tests/fixtures/m8/` imitate each vendor's format. Their values are made
up, not excerpts.

### Getting the data

```sh
make data-binance                          # ~13 MB, checked, normalized
export TIINGO_API_KEY=...                  # environment only, never a file in the repo
make data-tiingo UPDATE_PINS=1             # first time: writes the pins; commit the manifest
make data-tiingo                           # later: fails if Tiingo revised anything
```

The Tiingo key is read from `TIINGO_API_KEY` and only sent in the `Authorization`
header. There is no key argument and no key file. It never appears in a URL, an
error message, a dataset or a run record (`tests/test_data_adapters.py`,
`tests/test_runs.py`).

## Normalized datasets

Parquet files written and read by `research.data.store`. The dataset kind and metadata
(instrument, source, seed, survivorship note) are in the schema metadata. All values are
integers (`research.data.schema`):

| Kind | Columns |
| --- | --- |
| `trades` | `ts` (ns), `trade_id`, `price` (ticks), `qty` (lots), `aggressor` (0 buy, 1 sell) |
| `quotes` | `ts`, `bid_px`, `bid_qty`, `ask_px`, `ask_qty` (an empty side is price 0, size 0) |
| `bars` | `symbol`, `date`, `open/high/low/close` (micro-dollars), `volume`, `dividend` (ex-date cash per share), `split_num/split_den` (new shares per old) |

Decimal text becomes integers through `Decimal`, never a float (`research.ticks`). A
value off the instrument's grid is rejected, not rounded. Tiingo's JSON numbers are
parsed as text for the same reason.

**Bars are unadjusted.** Vendors' adjusted prices are rescaled every time a later split
or dividend happens. A 2010 adjusted close therefore depends on 2020's dividends, which
is look-ahead for anything that uses price levels. We store prices as traded, with each
day's actions. `BarPanel.total_return_index()` builds total returns forward in time
from them.

## Validation (on every write and every load)

| Kind | Rejected |
| --- | --- |
| all | wrong or missing columns, wrong dtypes |
| `trades` | a timestamp going backwards; a negative or zero size; a non-positive price; an aggressor other than 0/1; a `trade_id` that does not increase |
| `quotes` | a timestamp going backwards; negative sizes or prices; a price without a size (or the reverse); **crossed or locked** quotes (`bid >= ask`). Replayed into the engine, a locked quote's two historical orders would trade with each other |
| `bars` | dates not strictly increasing within a symbol; unsorted symbols; non-positive prices; `high` below `open`/`close`; `low` above them; negative volume or dividend; a non-positive split ratio |

`store.load` validates even a file that was written some other way.

The content hash (`store.content_hash`) covers column names, dtypes, values and
metadata. It does not cover the Parquet bytes, so it doesn't depend on the writer's
version or compression. Run records pin datasets with it.

## Survivorship bias

The Tiingo universe is ETFs that still trade today, chosen today. Funds that closed
before today are missing. For momentum this flatters both legs:

- **Long leg:** names that were bought on strength and later collapsed and delisted are
  absent.
- **Short leg:** weak names that went to zero, where the short would have paid, are
  absent too.

ETFs suffer less than single stocks: closures are rarer, and sector and asset-class
funds rarely go to zero. The bias is still there. Members that list after the start
date (HYG in 2007-04) join the universe when their first bar appears: the panel's
`valid` mask is point-in-time membership. The manifest's `survivorship` note travels
into the dataset's metadata and every run record. Free data cannot remove this bias:
that needs delisted securities, which free sources do not carry.
