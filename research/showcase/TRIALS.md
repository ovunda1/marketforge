# Showcase trials ledger

Every run of a strategy on the real (Tiingo) data, in order, including the ones that
are not reported. Parameters are fixed in `tsmom.toml` and `xsmom.toml` and committed
before the first run; nothing below changes them. Within a run, the walk-forward grid
is the only search: its size and the number of folds are in each tearsheet's trials
table, and the deflated Sharpe uses the grid size as N.

| # | Date | Config | Commit of the config | What | Outcome |
|---|------|--------|----------------------|------|---------|
| 1 | 2026-10-22 | `tsmom.toml` | `0c8f73a` | first and only run (`make showcase`): walk-forward, 6 parameter sets x 14 folds, plus the 5-point capacity curve | reported as it came out (`tsmom/`) |
| 2 | 2026-10-22 | `xsmom.toml` | `0c8f73a` | first and only run (`make showcase`): walk-forward, 4 parameter sets x 14 folds, plus the 5-point capacity curve | reported as it came out (`xsmom/`) |

| - | 2026-10-22 | both | `0c8f73a` | `python -m research reproduce` of runs 1 and 2: the same configs re-executed to check determinism, not new trials | identical (results, metrics, tearsheet) |

No other run touched the real data. Before these, the Tiingo download was only parsed
and validated (decimal places per field, row counts, splits and dividends per ticker);
no prices were looked at as returns.
