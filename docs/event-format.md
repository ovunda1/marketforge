# Canonical command and event formats

Shared byte for byte by the C++ engine (`engine/src/text_format.cpp`, `mf_driver`) and
the Python reference (`reference/`). Used by golden files and the differential test.

## Commands

One per line, positional, space separated. Blank lines and text after `#` are ignored.

```text
N <seq> <id> <acct> <B|S> <LMT|MKT> <GTC|IOC|FOK> <post_only:0|1> <CN|CO|CB> <px> <qty>
C <seq> <id>
R <seq> <id> <px> <qty>
```

`CN`/`CO`/`CB` are the STP modes cancel-newest, cancel-oldest and cancel-both.
Prices and quantities may be negative so invalid orders can be expressed.

## Output stream

For each command: its events, one per line, then one `BOOK` line. After the last
command: one `LVL` line per price level (bids best first, then asks best first).

```text
ACK  seq= id= side= type= tif= po= stp= px= qty=
REJ  seq= id= reason=
TRD  seq= trade= px= qty= maker= taker= aggr=
FILL seq= trade= id= acct= side= liq=M|T px= qty= leaves= cum=
CXL  seq= id= qty= reason=USER|UNFILLED|SELF_TRADE
RPL  seq= id= px= qty= leaves= prio=KEPT|LOST
BOOK seq= bid=<px>x<qty>|- ask=<px>x<qty>|-
LVL  side=B|S px= qty= orders=<id>:<leaves>,...
```

(Columns are aligned here for reading; the real format has one space after the tag.)

Within one command the order is fixed: `ACK`/`RPL`/`REJ`; then per match `TRD`, maker
`FILL`, taker `FILL`, with a resting order's STP `CXL` where it happens; then the
incoming order's own `CXL`. Reject reasons: `INVALID_PRICE`, `INVALID_QTY`,
`INVALID_ORDER_TYPE`, `DUPLICATE_ORDER_ID`, `UNKNOWN_ORDER`, `NO_LIQUIDITY`,
`FOK_NOT_FILLABLE`, `POST_ONLY_WOULD_CROSS`, `REPLACE_QTY_TOO_LOW`.

`BOOK` and `LVL` are written by the drivers, not emitted by the engine: comparing top of
book after every command pins a divergence to the exact command that caused it, and the
final `LVL` dump compares the whole book order by order.

## Differential test

`reference/generator.py` writes a command file once from a seed (`# seed=… n=…` header);
`mf_driver` and `reference/run.py` both consume that same file, so RNG differences
between languages cannot matter. `reference/difftest.py` compares the outputs line by
line, prints the first mismatch with its `seq_no`, the command and preceding context,
and saves the commands up to the failing `seq_no` to
`tests/difftest/regressions/seed-<seed>.cmds`. Saved files are replayed by pytest and
keep working even if the generator changes.

- `make test`: 100k commands (also in CI).
- `make difftest-long`: 1M commands (`SEED=` to override the seed).
