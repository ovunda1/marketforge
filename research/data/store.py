"""Parquet storage for normalized datasets (M8), validated on write and on load.

The dataset kind and its metadata (instrument, source, seed, survivorship note) travel
in the Parquet schema metadata under ``marketforge``. ``content_hash`` is a SHA-256 of
the logical content (column names, dtypes, values, metadata), not of the file bytes,
so it does not depend on the Parquet writer's version or compression.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from research.data import schema
from research.data.schema import ValidationError

META_KEY = b"marketforge"
SCHEMA_VERSION = 2  # 2: bar dividends in 1e-8 dollars (were micro-dollars)


@dataclass(frozen=True)
class Dataset:
    df: pd.DataFrame
    kind: str
    meta: dict[str, Any]
    sha256: str


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _arrow_schema(kind: str) -> pa.Schema:
    types = {"int64": pa.int64(), "int8": pa.int8(), "str": pa.string(), "datetime64[ns]": pa.date32()}
    return pa.schema([(c, types[t]) for c, t in schema.COLUMNS[kind].items()])


def content_hash(df: pd.DataFrame, kind: str, meta: dict[str, Any]) -> str:
    h = hashlib.sha256()
    h.update(canonical_json({"kind": kind, "schema_version": SCHEMA_VERSION, "meta": meta}).encode())
    for col, dtype in schema.COLUMNS[kind].items():
        h.update(f"\n{col}:{dtype}:{len(df)}\n".encode())
        values = df[col]
        if dtype == "str":
            for s in values.astype(str):
                b = s.encode()
                h.update(len(b).to_bytes(4, "little") + b)
        elif dtype == "datetime64[ns]":
            h.update(np.ascontiguousarray(values.to_numpy().astype("datetime64[D]").astype("<i8")).tobytes())
        else:
            h.update(np.ascontiguousarray(values.to_numpy().astype("<i8")).tobytes())
    return h.hexdigest()


def write(df: pd.DataFrame, path: str | Path, kind: str, meta: dict[str, Any]) -> str:
    """Validate, then write ``df`` as a ``kind`` dataset. Returns its content hash."""
    schema.validate(df, kind)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(df, schema=_arrow_schema(kind), preserve_index=False)
    header = {"kind": kind, "schema_version": SCHEMA_VERSION, "meta": meta}
    table = table.replace_schema_metadata({META_KEY: canonical_json(header).encode()})
    tmp = path.with_name(path.name + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    return content_hash(df, kind, meta)


def load(path: str | Path, kind: str | None = None) -> Dataset:
    """Read and validate a dataset; ``kind`` (if given) must match the file's."""
    table = pq.read_table(path)
    raw = (table.schema.metadata or {}).get(META_KEY)
    if raw is None:
        raise ValidationError(f"{path}: not a MarketForge dataset (no {META_KEY.decode()} metadata)")
    header = json.loads(raw)
    if header.get("schema_version") != SCHEMA_VERSION:
        raise ValidationError(f"{path}: schema version {header.get('schema_version')}, expected {SCHEMA_VERSION}")
    file_kind = header["kind"]
    if kind is not None and file_kind != kind:
        raise ValidationError(f"{path}: kind is {file_kind}, expected {kind}")
    df = table.to_pandas()
    if file_kind == "bars":
        df = schema.normalize_bars_frame(df)
    else:
        df = df.astype(schema.COLUMNS[file_kind])
    schema.validate(df, file_kind)
    return Dataset(df=df, kind=file_kind, meta=header["meta"], sha256=content_hash(df, file_kind, header["meta"]))
