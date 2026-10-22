# Showcase trials ledger

Every run of a strategy on the real (Tiingo) data, in order, including the ones that
are not reported. Parameters are fixed in `tsmom.toml` and `xsmom.toml` and committed
before the first run; nothing below changes them. Within a run, the walk-forward grid
is the only search: its size and the number of folds are in each tearsheet's trials
table, and the deflated Sharpe uses the grid size as N.

| # | Date | Config | Commit of the config | What | Outcome |
|---|------|--------|----------------------|------|---------|
