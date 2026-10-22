"""Download scripts (M8): pinned checksums, normalization, and the offline guarantee."""

import hashlib
import json
import socket
import urllib.request
import zipfile

import pytest

from conftest import REPO_ROOT, NetworkBlocked
from research.data import download, store, tiingo

FIXTURES = REPO_ROOT / "tests" / "fixtures" / "m8"
MANIFESTS = REPO_ROOT / "research" / "data" / "manifests"


def zipped(name: str, fixture: str) -> bytes:
    path = FIXTURES / fixture
    import io
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, path.read_text())
    return buf.getvalue()


@pytest.fixture
def binance_files() -> dict[str, bytes]:
    return {
        "https://data.binance.vision/x/KNCUSDT-bookTicker-2024-03-12.zip":
            zipped("KNCUSDT-bookTicker-2024-03-12.csv", "binance_um_bookticker.csv"),
        "https://data.binance.vision/x/KNCUSDT-trades-2024-03-12.zip":
            zipped("KNCUSDT-trades-2024-03-12.csv", "binance_um_trades.csv"),
    }


def manifest_for(files: dict[str, bytes]) -> dict:
    kinds = {"bookTicker": "quotes", "trades": "trades"}
    return {
        "name": "test-knc", "source": "binance", "license": "test",
        "instrument": {"symbol": "KNCUSDT", "tick_size": "0.0001", "lot_size": "1"},
        "files": [{"kind": next(v for k, v in kinds.items() if k in url), "url": url,
                   "sha256": hashlib.sha256(body).hexdigest()} for url, body in files.items()],
    }


class Opener:
    def __init__(self, files: dict[str, bytes]) -> None:
        self.files, self.calls = files, []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        return self.files[url]


def test_download_verifies_and_keeps_good_files(tmp_path, binance_files) -> None:
    manifest = manifest_for(binance_files)
    opener = Opener(binance_files)
    paths = download.fetch(manifest, tmp_path, opener=opener)
    assert len(paths) == 2 and all(p.is_file() for p in paths)
    assert all(p.is_relative_to(tmp_path / "raw" / "test-knc") for p in paths)
    # Already present and verified: not downloaded again.
    download.fetch(manifest, tmp_path, opener=opener)
    assert len(opener.calls) == 2


def test_checksum_mismatch_fails_and_leaves_nothing(tmp_path, binance_files) -> None:
    manifest = manifest_for(binance_files)
    tampered = {url: body + b"x" for url, body in binance_files.items()}
    with pytest.raises(download.ChecksumMismatch, match="sha256"):
        download.fetch(manifest, tmp_path, opener=Opener(tampered))
    assert not any(p.is_file() for p in (tmp_path / "raw").rglob("*"))


def test_a_corrupted_local_file_is_downloaded_again(tmp_path, binance_files) -> None:
    manifest = manifest_for(binance_files)
    paths = download.fetch(manifest, tmp_path, opener=Opener(binance_files))
    paths[0].write_bytes(b"corrupt")
    opener = Opener(binance_files)
    download.fetch(manifest, tmp_path, opener=opener)
    assert len(opener.calls) == 1
    assert hashlib.sha256(paths[0].read_bytes()).hexdigest() == manifest["files"][0]["sha256"]


def test_normalize_writes_validated_parquet(tmp_path, binance_files) -> None:
    manifest = manifest_for(binance_files)
    download.fetch(manifest, tmp_path, opener=Opener(binance_files))
    out = download.normalize(manifest, tmp_path)
    quotes = store.load(out["quotes"], "quotes")
    trades = store.load(out["trades"], "trades")
    assert len(quotes.df) == 3 and len(trades.df) == 3
    assert quotes.meta["instrument"]["tick_size"] == "0.0001"
    assert quotes.meta["source"]["sha256"] == manifest["files"][0]["sha256"]


def test_committed_binance_manifest_is_pinned() -> None:
    manifest = download.load_manifest(MANIFESTS / "binance-um-kncusdt-2024-03-12.json")
    assert manifest["source"] == "binance"
    assert {f["kind"] for f in manifest["files"]} == {"quotes", "trades"}
    for f in manifest["files"]:
        assert f["url"].startswith("https://data.binance.vision/data/futures/um/daily/")
        assert len(f["sha256"]) == 64 and int(f["sha256"], 16) >= 0


def test_manifests_are_strict(tmp_path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"name": "x", "source": "binance", "files": [{"url": "u"}]}))
    with pytest.raises(download.ManifestError):
        download.load_manifest(path)


# --- Tiingo pins ------------------------------------------------------------------------

class FakeTransport:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def __call__(self, url: str, headers: dict[str, str]) -> tuple[int, bytes]:
        return 200, self.body


def tiingo_manifest() -> dict:
    return {"name": "test-etf", "source": "tiingo", "license": "test", "universe": ["TEST"],
            "start": "2020-01-01", "end": "2020-01-31",
            "survivorship": "current listings only", "pins": {}}


def test_tiingo_first_fetch_needs_update_pins(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    client = tiingo.TiingoClient(transport=FakeTransport((FIXTURES / "tiingo_daily_TEST.json").read_bytes()))
    manifest = tiingo_manifest()
    with pytest.raises(download.PinMissing, match="--update-pins"):
        download.fetch_tiingo(manifest, tmp_path, client)
    path = download.fetch_tiingo(manifest, tmp_path, client, update_pins=True)
    assert set(manifest["pins"]) == {"TEST"}
    ds = store.load(path, "bars")
    assert ds.meta["survivorship"] == "current listings only"
    # Pinned now: the same data passes, revised data fails loudly.
    download.fetch_tiingo(manifest, tmp_path, client)
    revised = json.loads((FIXTURES / "tiingo_daily_TEST.json").read_text())
    revised[0]["close"] = 101.01
    client2 = tiingo.TiingoClient(transport=FakeTransport(json.dumps(revised).encode()))
    with pytest.raises(download.PinMismatch, match="TEST"):
        download.fetch_tiingo(manifest, tmp_path, client2)


def test_a_universe_out_of_alphabetical_order_is_stored_sorted(tmp_path, monkeypatch) -> None:
    # The real manifest lists SPY before QQQ; bars datasets are sorted by symbol (M9 fix:
    # the first real download failed validation on write).
    monkeypatch.setenv("TIINGO_API_KEY", "k")
    client = tiingo.TiingoClient(transport=FakeTransport((FIXTURES / "tiingo_daily_TEST.json").read_bytes()))
    manifest = {**tiingo_manifest(), "universe": ["SPY", "QQQ"]}
    path = download.fetch_tiingo(manifest, tmp_path, client, update_pins=True)
    df = store.load(path, "bars").df
    assert df["symbol"].drop_duplicates().tolist() == ["QQQ", "SPY"]
    assert list(manifest["pins"]) == ["SPY", "QQQ"]  # pins keep the manifest's order


def test_committed_tiingo_manifest_has_a_survivorship_note() -> None:
    manifest = download.load_manifest(MANIFESTS / "tiingo-etf-daily.json")
    assert manifest["source"] == "tiingo"
    assert len(manifest["universe"]) >= 10
    assert "survivor" in manifest["survivorship"].lower()


# --- Offline -----------------------------------------------------------------------------

def test_the_suite_cannot_reach_the_network() -> None:
    with pytest.raises(NetworkBlocked):
        urllib.request.urlopen("https://data.binance.vision/", timeout=1)
    with pytest.raises(NetworkBlocked):
        socket.create_connection(("127.0.0.1", 9), timeout=1)
