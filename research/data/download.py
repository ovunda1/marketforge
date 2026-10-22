"""Download pinned datasets and normalize them to Parquet (M8).

    python -m research.data.download research/data/manifests/<name>.json [--data-dir data]
    python -m research.data.download research/data/manifests/tiingo-etf-daily.json --update-pins

Raw files go to ``<data-dir>/raw/<name>/``, normalized datasets to
``<data-dir>/normalized/<name>/<kind>.parquet``. ``data/`` is git-ignored: nothing
downloaded is committed (docs/data.md has the sources and their terms).

- **Binance** manifests pin each archive's SHA-256, as Binance publishes it next to the
  file. A download that does not match is deleted and the run fails.
- **Tiingo** has no immutable archive, so a manifest pins the content hash of each
  ticker's normalized bars over a fixed date range. The first fetch must be run with
  ``--update-pins`` (then commit the manifest); later fetches fail on any revision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from research.data import binance, store, tiingo
from research.data.schema import Instrument

Opener = Callable[[str], bytes]

REQUIRED = {
    "binance": {"name", "source", "license", "instrument", "files"},
    "tiingo": {"name", "source", "license", "universe", "start", "end", "survivorship", "pins"},
}


class ManifestError(ValueError):
    pass


class ChecksumMismatch(RuntimeError):
    pass


class PinMissing(RuntimeError):
    pass


class PinMismatch(RuntimeError):
    pass


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text())
    source = manifest.get("source")
    if source not in REQUIRED:
        raise ManifestError(f"{path}: unknown source {source!r}")
    missing = REQUIRED[source] - set(manifest)
    if missing:
        raise ManifestError(f"{path}: missing {', '.join(sorted(missing))}")
    if source == "binance":
        for f in manifest["files"]:
            if set(f) != {"kind", "url", "sha256"} or f["kind"] not in ("quotes", "trades"):
                raise ManifestError(f"{path}: each file needs kind (quotes|trades), url and sha256")
    return manifest


def _urllib_opener(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def raw_path(manifest: dict[str, Any], data_dir: Path, url: str) -> Path:
    return Path(data_dir) / "raw" / manifest["name"] / url.rsplit("/", 1)[-1]


def fetch(manifest: dict[str, Any], data_dir: str | Path, opener: Opener | None = None) -> list[Path]:
    """Download each file unless a verified copy is already there."""
    opener = opener or _urllib_opener
    paths = []
    for f in manifest["files"]:
        path = raw_path(manifest, Path(data_dir), f["url"])
        if path.is_file() and _sha256(path) == f["sha256"]:
            paths.append(path)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".part")
        tmp.write_bytes(opener(f["url"]))
        actual = _sha256(tmp)
        if actual != f["sha256"]:
            tmp.unlink()
            path.unlink(missing_ok=True)
            raise ChecksumMismatch(f"{f['url']}: sha256 {actual}, pinned {f['sha256']}")
        tmp.replace(path)
        paths.append(path)
    return paths


def normalize(manifest: dict[str, Any], data_dir: str | Path) -> dict[str, Path]:
    """Raw Binance archives to validated ``quotes`` and ``trades`` Parquet files."""
    instrument = Instrument(**manifest["instrument"])
    readers = {"quotes": binance.read_book_ticker_csv, "trades": binance.read_trades_csv}
    out = {}
    for f in manifest["files"]:
        df = readers[f["kind"]](raw_path(manifest, Path(data_dir), f["url"]), instrument)
        meta = {"instrument": instrument.to_dict(),
                "source": {"name": manifest["name"], "url": f["url"], "sha256": f["sha256"]}}
        path = Path(data_dir) / "normalized" / manifest["name"] / f"{f['kind']}.parquet"
        store.write(df, path, f["kind"], meta)
        out[f["kind"]] = path
    return out


def fetch_tiingo(manifest: dict[str, Any], data_dir: str | Path, client: tiingo.TiingoClient,
                 update_pins: bool = False) -> Path:
    """Every ticker of the universe over the manifest's range, checked against the pins,
    as one bars dataset. With ``update_pins`` the pins are (re)written into ``manifest``."""
    frames = []
    for ticker in manifest["universe"]:
        df = client.daily_bars(ticker, manifest["start"], manifest["end"])
        digest = store.content_hash(df, "bars", {"ticker": ticker})
        pinned = manifest["pins"].get(ticker)
        if update_pins:
            manifest["pins"][ticker] = digest
        elif pinned is None:
            raise PinMissing(f"{ticker}: no pinned checksum; run with --update-pins and commit the manifest")
        elif pinned != digest:
            raise PinMismatch(f"{ticker}: Tiingo's data changed since it was pinned ({digest} != {pinned})")
        frames.append((ticker, df))
    # A bars dataset is sorted by symbol, whatever order the manifest lists the universe in.
    bars = pd.concat([df for _, df in sorted(frames, key=lambda f: f[0])], ignore_index=True)
    meta = {"source": {"name": manifest["name"], "vendor": "tiingo", "start": manifest["start"],
                       "end": manifest["end"], "pins": dict(sorted(manifest["pins"].items()))},
            "survivorship": manifest["survivorship"]}
    path = Path(data_dir) / "normalized" / manifest["name"] / "bars.parquet"
    store.write(bars, path, "bars", meta)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--update-pins", action="store_true", help="Tiingo: write the pins into the manifest")
    args = parser.parse_args(argv)
    manifest = load_manifest(args.manifest)
    if manifest["source"] == "binance":
        fetch(manifest, args.data_dir)
        for kind, path in normalize(manifest, args.data_dir).items():
            print(f"{kind}: {path}")
    else:
        path = fetch_tiingo(manifest, args.data_dir, tiingo.TiingoClient(), update_pins=args.update_pins)
        if args.update_pins:
            args.manifest.write_text(json.dumps(manifest, indent=2) + "\n")
            print(f"pins written to {args.manifest}: commit it")
        print(f"bars: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
