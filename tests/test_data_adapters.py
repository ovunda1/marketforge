"""Ingestion adapters (M8): Binance trades and book ticker, Tiingo daily bars, CSV bars.
Fixtures are made-up files in each vendor's format (tests/fixtures/m8/README.md)."""

import io
import json
import zipfile

import pandas as pd
import pytest

from conftest import REPO_ROOT
from research.data import binance, csvbars, schema, tiingo
from research.data.schema import Instrument, ValidationError
from research.ticks import OffGridError

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "m8"
KNC = Instrument(symbol="KNCUSDT", tick_size="0.0001", lot_size="1")


# --- Binance ---------------------------------------------------------------------------

def test_futures_trades_with_header_and_milliseconds() -> None:
    df = binance.read_trades_csv(FIXTURES / "binance_um_trades.csv", KNC)
    schema.validate(df, "trades")
    assert df["ts"].tolist() == [1710201600146_000000, 1710201600146_000000, 1710201600200_000000]
    assert df["trade_id"].tolist() == [1001, 1002, 1003]
    assert df["price"].tolist() == [9704, 9705, 9703]
    assert df["qty"].tolist() == [41, 13, 5]
    # is_buyer_maker=true means the seller was the aggressor.
    assert df["aggressor"].tolist() == [schema.BUY, schema.SELL, schema.BUY]


def test_spot_trades_without_header_and_microseconds() -> None:
    df = binance.read_trades_csv(FIXTURES / "binance_spot_trades_us.csv", KNC)
    assert df["ts"].tolist() == [1735689600146123_000, 1735689600146999_000]
    assert df["aggressor"].tolist() == [schema.BUY, schema.SELL]


def test_timestamp_units() -> None:
    assert binance.to_ns(1710201600146) == 1710201600146_000000
    assert binance.to_ns(1710201600146123) == 1710201600146123_000
    assert binance.to_ns(1710201600146123456) == 1710201600146123456
    with pytest.raises(ValueError, match="unit"):
        binance.to_ns(1710201600)  # seconds: not a format Binance publishes


def test_book_ticker_uses_the_transaction_time() -> None:
    df = binance.read_book_ticker_csv(FIXTURES / "binance_um_bookticker.csv", KNC)
    schema.validate(df, "quotes")
    assert df["ts"].tolist() == [1710201600146_000000, 1710201600150_000000, 1710201600150_000000]
    assert df.iloc[0][["bid_px", "bid_qty", "ask_px", "ask_qty"]].tolist() == [9703, 143, 9707, 572]


def test_crossed_book_ticker_fails_validation() -> None:
    with pytest.raises(ValidationError, match="crossed"):
        binance.read_book_ticker_csv(FIXTURES / "binance_um_bookticker_crossed.csv", KNC)


def test_off_grid_price_is_rejected(tmp_path) -> None:
    path = tmp_path / "t.csv"
    path.write_text("id,price,qty,quote_qty,time,is_buyer_maker\n1,0.97035,1,1,1710201600146,false\n")
    with pytest.raises(OffGridError):
        binance.read_trades_csv(path, KNC)


def test_reads_the_csv_inside_a_zip(tmp_path) -> None:
    path = tmp_path / "KNCUSDT-trades-2024-03-12.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("KNCUSDT-trades-2024-03-12.csv", (FIXTURES / "binance_um_trades.csv").read_text())
    df = binance.read_trades_csv(path, KNC)
    assert len(df) == 3


# --- Tiingo ----------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self.body, self.status, self.calls = body, status, []

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        self.calls.append((url, headers))
        return self.status, self.body


def tiingo_body() -> bytes:
    return (FIXTURES / "tiingo_daily_TEST.json").read_bytes()


def test_tiingo_rows_become_unadjusted_bars_with_actions() -> None:
    df = tiingo.to_bars("TEST", json.loads(tiingo_body(), parse_float=str, parse_int=str))
    schema.validate(df, "bars")
    assert df["symbol"].tolist() == ["TEST"] * 3
    assert [d.strftime("%Y-%m-%d") for d in df["date"]] == ["2020-01-02", "2020-01-03", "2020-01-06"]
    # Prices in micro-dollars, unadjusted; the adj* fields are ignored (they embed future
    # splits and dividends).
    assert df["open"].tolist() == [100_500_000, 101_000_000, 50_500_000]
    assert df["close"].tolist() == [101_000_000, 100_000_000, 50_000_000]
    assert df["volume"].tolist() == [120000, 90000, 200000]
    assert df["dividend"].tolist() == [0, 250_000, 0]
    assert list(zip(df["split_num"], df["split_den"])) == [(1, 1), (1, 1), (2, 1)]


@pytest.mark.parametrize(("factor", "ratio"), [("0.5", (1, 2)), ("0.333333", (1, 3)), ("1.5", (3, 2)),
                                               ("4.0", (4, 1))])
def test_split_factors_become_exact_ratios(factor: str, ratio: tuple[int, int]) -> None:
    assert tiingo.split_ratio(factor) == ratio


def test_client_reads_the_key_from_the_environment_only(monkeypatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "s3cret-key")
    transport = FakeTransport(tiingo_body())
    client = tiingo.TiingoClient(transport=transport)
    df = client.daily_bars("TEST", "2020-01-01", "2020-01-31")
    assert len(df) == 3
    url, headers = transport.calls[0]
    assert headers["Authorization"] == "Token s3cret-key"
    assert "s3cret-key" not in url
    assert "/tiingo/daily/TEST/prices" in url and "startDate=2020-01-01" in url
    assert "s3cret-key" not in repr(client)


def test_missing_key_is_a_clear_error(monkeypatch) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    with pytest.raises(tiingo.MissingApiKey, match="TIINGO_API_KEY"):
        tiingo.TiingoClient(transport=FakeTransport(b"[]"))


def test_http_errors_never_echo_the_key(monkeypatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "s3cret-key")
    client = tiingo.TiingoClient(transport=FakeTransport(b'{"detail":"bad token s3cret-key"}', 401))
    with pytest.raises(tiingo.TiingoError) as err:
        client.daily_bars("TEST", "2020-01-01", "2020-01-31")
    assert "s3cret-key" not in str(err.value)
    assert "401" in str(err.value)


def test_tiingo_prices_are_parsed_without_floats(monkeypatch) -> None:
    # 0.1 + 0.2 style artifacts would put a price off the micro grid.
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    body = json.dumps([{"date": "2020-01-02T00:00:00.000Z", "open": 0.1, "high": 0.3, "low": 0.1,
                        "close": 0.3, "volume": 1, "divCash": 0.0, "splitFactor": 1.0}]).encode()
    df = tiingo.TiingoClient(transport=FakeTransport(body)).daily_bars("X", "2020-01-01", "2020-01-03")
    assert df["open"].tolist() == [100_000] and df["close"].tolist() == [300_000]


# --- CSV bars --------------------------------------------------------------------------

def test_csv_bars() -> None:
    df = csvbars.read_bars_csv(FIXTURES / "bars.csv", "ABC")
    schema.validate(df, "bars")
    assert df["close"].tolist() == [10_200_000, 10_100_000, 5_050_000]
    assert df["dividend"].tolist() == [0, 50_000, 0]
    assert list(zip(df["split_num"], df["split_den"])) == [(1, 1), (1, 1), (2, 1)]


def test_csv_bars_without_action_columns(tmp_path) -> None:
    path = tmp_path / "b.csv"
    path.write_text("date,open,high,low,close,volume\n2020-01-02,1,1,1,1,0\n")
    df = csvbars.read_bars_csv(path, "X")
    assert df["dividend"].tolist() == [0] and df["split_num"].tolist() == [1]


def test_csv_bars_from_a_text_stream() -> None:
    df = csvbars.read_bars_csv(io.StringIO("date,open,high,low,close,volume\n2020-01-02,2,3,1,2,5\n"), "X")
    assert isinstance(df, pd.DataFrame) and df["high"].tolist() == [3_000_000]
