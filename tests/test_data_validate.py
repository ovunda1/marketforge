"""Normalized datasets (M8): exact decimal parsing, validation on load, Parquet store."""

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from research.data import schema, store
from research.data.schema import ValidationError
from research.ticks import OffGridError, to_units


# --- Exact decimal -> integer units ----------------------------------------------------

def test_decimal_strings_convert_exactly() -> None:
    assert to_units("0.97030000", "0.0001") == 9703
    assert to_units("0.1", "0.000001") == 100_000
    assert to_units("100", "0.01") == 10_000
    assert to_units("-1.5", "0.5") == -3
    assert to_units("1e-4", "0.0001") == 1


def test_off_grid_values_are_rejected_not_rounded() -> None:
    with pytest.raises(OffGridError):
        to_units("0.97035", "0.0001")
    with pytest.raises(OffGridError):
        to_units("0.0000005", "0.000001")


def test_floats_are_refused() -> None:
    # 0.1 has no exact binary representation: a float never reaches a price.
    with pytest.raises(TypeError):
        to_units(0.1, "0.01")  # type: ignore[arg-type]


def test_garbage_is_a_value_error() -> None:
    with pytest.raises(ValueError):
        to_units("abc", "0.01")
    with pytest.raises(ValueError):
        to_units("NaN", "0.01")


# --- Validation ------------------------------------------------------------------------

def trades(**override: list) -> pd.DataFrame:
    cols = {"ts": [1, 2, 2, 5], "trade_id": [1, 2, 3, 4], "price": [100, 101, 101, 99],
            "qty": [1, 2, 3, 4], "aggressor": [0, 1, 0, 1]}
    cols.update(override)
    df = pd.DataFrame(cols)
    return df.astype({c: schema.COLUMNS["trades"][c] for c in df.columns})


def quotes(**override: list) -> pd.DataFrame:
    cols = {"ts": [1, 2, 3], "bid_px": [99, 100, 100], "bid_qty": [5, 5, 1],
            "ask_px": [101, 101, 102], "ask_qty": [3, 4, 4]}
    cols.update(override)
    df = pd.DataFrame(cols)
    return df.astype({c: schema.COLUMNS["quotes"][c] for c in df.columns})


def bars(**override: list) -> pd.DataFrame:
    cols = {"symbol": ["A", "A", "B", "B"],
            "date": pd.to_datetime(["2020-01-02", "2020-01-03", "2020-01-02", "2020-01-03"]),
            "open": [10, 11, 20, 21], "high": [12, 12, 22, 22], "low": [9, 10, 19, 20],
            "close": [11, 11, 21, 20], "volume": [100, 200, 300, 0],
            "dividend": [0, 0, 0, 1], "split_num": [1, 1, 1, 1], "split_den": [1, 1, 1, 1]}
    cols.update(override)
    return schema.normalize_bars_frame(pd.DataFrame(cols))


def test_valid_datasets_pass() -> None:
    schema.validate(trades(), "trades")
    schema.validate(quotes(), "quotes")
    schema.validate(bars(), "bars")


def test_timestamps_going_backwards_are_rejected() -> None:
    with pytest.raises(ValidationError, match=r"timestamp.*row 2"):
        schema.validate(trades(ts=[1, 3, 2, 5]), "trades")
    with pytest.raises(ValidationError, match="timestamp"):
        schema.validate(quotes(ts=[1, 3, 2]), "quotes")


def test_negative_sizes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="negative"):
        schema.validate(trades(qty=[1, -2, 3, 4]), "trades")
    with pytest.raises(ValidationError, match="negative"):
        schema.validate(quotes(ask_qty=[3, -4, 4]), "quotes")
    with pytest.raises(ValidationError, match="negative"):
        schema.validate(bars(volume=[100, -1, 300, 0]), "bars")


def test_zero_trade_size_and_nonpositive_price_are_rejected() -> None:
    with pytest.raises(ValidationError, match="qty"):
        schema.validate(trades(qty=[1, 0, 3, 4]), "trades")
    with pytest.raises(ValidationError, match="price"):
        schema.validate(trades(price=[100, 0, 101, 99]), "trades")


def test_trade_fields_are_checked() -> None:
    with pytest.raises(ValidationError, match="aggressor"):
        schema.validate(trades(aggressor=[0, 2, 0, 1]), "trades")
    with pytest.raises(ValidationError, match="trade_id"):
        schema.validate(trades(trade_id=[1, 2, 2, 4]), "trades")


def test_crossed_and_locked_quotes_are_rejected() -> None:
    with pytest.raises(ValidationError, match="crossed"):
        schema.validate(quotes(bid_px=[99, 102, 100]), "quotes")
    # Locked (bid == ask): the engine would match the two historical orders.
    with pytest.raises(ValidationError, match="crossed"):
        schema.validate(quotes(bid_px=[99, 101, 100]), "quotes")


def test_an_empty_side_is_allowed_but_a_price_needs_a_size() -> None:
    schema.validate(quotes(bid_px=[0, 100, 100], bid_qty=[0, 5, 1]), "quotes")
    with pytest.raises(ValidationError, match="empty side"):
        schema.validate(quotes(bid_px=[99, 100, 100], bid_qty=[0, 5, 1]), "quotes")
    with pytest.raises(ValidationError, match="empty side"):
        schema.validate(quotes(ask_px=[0, 101, 102], ask_qty=[3, 4, 4]), "quotes")


def test_bar_dates_must_strictly_increase_per_symbol() -> None:
    dup = pd.to_datetime(["2020-01-02", "2020-01-02", "2020-01-02", "2020-01-03"])
    with pytest.raises(ValidationError, match="date"):
        schema.validate(bars(date=dup), "bars")
    back = pd.to_datetime(["2020-01-03", "2020-01-02", "2020-01-02", "2020-01-03"])
    with pytest.raises(ValidationError, match="date"):
        schema.validate(bars(date=back), "bars")


def test_bar_prices_must_be_consistent() -> None:
    with pytest.raises(ValidationError, match="high"):
        schema.validate(bars(high=[10, 12, 22, 22]), "bars")  # high < close 11
    with pytest.raises(ValidationError, match="low"):
        schema.validate(bars(low=[9, 10, 19, 21]), "bars")  # low > close 20
    with pytest.raises(ValidationError, match="price"):
        schema.validate(bars(open=[0, 11, 20, 21], low=[0, 10, 19, 20]), "bars")


def test_corporate_actions_must_be_sane() -> None:
    with pytest.raises(ValidationError, match="dividend"):
        schema.validate(bars(dividend=[0, -1, 0, 0]), "bars")
    with pytest.raises(ValidationError, match="split"):
        schema.validate(bars(split_num=[1, 0, 1, 1]), "bars")


def test_wrong_dtype_or_missing_column_is_rejected() -> None:
    df = trades()
    df["price"] = df["price"].astype("float64")
    with pytest.raises(ValidationError, match="int64"):
        schema.validate(df, "trades")
    with pytest.raises(ValidationError, match="aggressor"):
        schema.validate(trades().drop(columns=["aggressor"]), "trades")


# --- Parquet store ----------------------------------------------------------------------

META = {"instrument": {"symbol": "KNCUSDT", "tick_size": "0.0001", "lot_size": "1"},
        "source": "test"}


def test_round_trip_keeps_int64_columns_and_metadata(tmp_path) -> None:
    path = tmp_path / "t.parquet"
    digest = store.write(trades(), path, "trades", META)
    ds = store.load(path)
    assert ds.kind == "trades"
    assert ds.meta == META
    assert ds.sha256 == digest
    for col in ("ts", "trade_id", "price", "qty"):
        assert ds.df[col].dtype == np.int64
    pd.testing.assert_frame_equal(ds.df, trades())


def test_bars_round_trip(tmp_path) -> None:
    path = tmp_path / "b.parquet"
    store.write(bars(), path, "bars", {"source": "test"})
    pd.testing.assert_frame_equal(store.load(path, "bars").df, bars())


def test_write_validates(tmp_path) -> None:
    with pytest.raises(ValidationError):
        store.write(trades(qty=[1, -1, 1, 1]), tmp_path / "bad.parquet", "trades", META)
    assert not (tmp_path / "bad.parquet").exists()


def test_load_validates_a_file_written_behind_its_back(tmp_path) -> None:
    # A file produced without store.write (another tool, a hand edit) is still checked.
    path = tmp_path / "raw.parquet"
    store.write(trades(), path, "trades", META)
    table = pq.read_table(path)
    qty = table.column("qty").to_pylist()
    qty[1] = -7
    table = table.set_column(table.schema.get_field_index("qty"), "qty", pa.array(qty, pa.int64()))
    pq.write_table(table, path)
    with pytest.raises(ValidationError, match="negative"):
        store.load(path)


def test_load_checks_the_expected_kind(tmp_path) -> None:
    path = tmp_path / "t.parquet"
    store.write(trades(), path, "trades", META)
    with pytest.raises(ValidationError, match="kind"):
        store.load(path, "quotes")


def test_content_hash_is_stable_and_sensitive(tmp_path) -> None:
    a = store.write(trades(), tmp_path / "a.parquet", "trades", META)
    b = store.write(trades(), tmp_path / "b.parquet", "trades", META)
    assert a == b == store.content_hash(trades(), "trades", META)
    assert store.content_hash(trades(qty=[1, 2, 3, 5]), "trades", META) != a
    other = {**META, "source": "elsewhere"}
    assert store.content_hash(trades(), "trades", other) != a
