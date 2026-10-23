"""PostgreSQL schema (M10), SQLAlchemy 2.0 Core. The migration in migrations/versions/
creates exactly these tables.

Projection tables (rebuilt from the journals by the projector; `make projection-rebuild`):
orders, fills, trades, positions, risk_limits, sessions, projection_cursor. Their
columns match services/projector/projection.py's rows one to one.

Not projections: instruments (seeded from the instruments YAML), accounts (API key
hashes, created by `python -m services.api.accounts`), and the backtest tables (written
by the worker).

Prices are BIGINT ticks and quantities BIGINT units; a position's cash (ticks x qty) is
NUMERIC(38, 0) so a large account cannot overflow it. Timestamps from the journal are
BIGINT nanoseconds, as in the journal.
"""

from __future__ import annotations

from sqlalchemy import (BigInteger, Boolean, Column, DateTime, Float, ForeignKey, Index, Integer, MetaData, Numeric,
                        String, Table, Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB

metadata = MetaData()

instruments = Table(
    "instruments", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("tick_size", Text, nullable=False),  # decimal text, never a float
)

accounts = Table(
    "accounts", metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("name", Text, nullable=False),
    Column("role", String(16), nullable=False),
    Column("api_key_prefix", String(8), nullable=False, unique=True),
    Column("api_key_hash", String(64), nullable=False, unique=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

risk_limits = Table(
    "risk_limits", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("scope", String(16), primary_key=True),  # instrument | default | account | kill_switch
    Column("account_id", BigInteger, primary_key=True),  # 0 when the scope has none
    Column("limits", JSONB, nullable=False),
    Column("seq_no", BigInteger, nullable=False),
)

sessions = Table(
    "sessions", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("state", String(16), nullable=False),
    Column("seq_no", BigInteger, nullable=False),
)

orders = Table(
    "orders", metadata,
    Column("order_id", BigInteger, primary_key=True),
    Column("symbol", String(16), nullable=False),
    Column("account_id", BigInteger, nullable=False),
    Column("client_order_id", Text),
    Column("side", String(4), nullable=False),
    Column("order_type", String(8), nullable=False),
    Column("tif", String(4), nullable=False),
    Column("post_only", Boolean, nullable=False),
    Column("stp", String(16), nullable=False),
    Column("price", BigInteger, nullable=False),
    Column("qty", BigInteger, nullable=False),
    Column("leaves_qty", BigInteger, nullable=False),
    Column("cum_qty", BigInteger, nullable=False),
    Column("status", String(16), nullable=False),
    Column("reject_reason", String(32)),
    Column("created_seq", BigInteger, nullable=False),
    Column("created_ts", BigInteger, nullable=False),
    Column("updated_seq", BigInteger, nullable=False),
    Column("updated_ts", BigInteger, nullable=False),
    UniqueConstraint("account_id", "client_order_id", name="orders_account_client_order_id"),
    Index("orders_symbol_status", "symbol", "status"),
)

fills = Table(
    "fills", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("trade_id", BigInteger, primary_key=True),
    Column("order_id", BigInteger, primary_key=True),
    Column("account_id", BigInteger, nullable=False),
    Column("side", String(4), nullable=False),
    Column("liquidity", String(8), nullable=False),
    Column("price", BigInteger, nullable=False),
    Column("qty", BigInteger, nullable=False),
    Column("leaves_qty", BigInteger, nullable=False),
    Column("cum_qty", BigInteger, nullable=False),
    Column("seq_no", BigInteger, nullable=False),
    Column("ts", BigInteger, nullable=False),
    Index("fills_order_id", "order_id"),
)

trades = Table(
    "trades", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("trade_id", BigInteger, primary_key=True),
    Column("price", BigInteger, nullable=False),
    Column("qty", BigInteger, nullable=False),
    Column("maker_order_id", BigInteger, nullable=False),
    Column("taker_order_id", BigInteger, nullable=False),
    Column("aggressor_side", String(4), nullable=False),
    Column("seq_no", BigInteger, nullable=False),
    Column("ts", BigInteger, nullable=False),
)

positions = Table(
    "positions", metadata,
    Column("account_id", BigInteger, primary_key=True),
    Column("symbol", String(16), primary_key=True),
    Column("qty", BigInteger, nullable=False),
    Column("cash", Numeric(38, 0), nullable=False),
    Column("updated_seq", BigInteger, nullable=False),
)

projection_cursor = Table(
    "projection_cursor", metadata,
    Column("symbol", String(16), primary_key=True),
    Column("last_seq_no", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

backtest_runs = Table(
    "backtest_runs", metadata,
    Column("id", String(32), primary_key=True),
    Column("account_id", BigInteger, nullable=False),
    Column("config", JSONB, nullable=False),
    Column("seed", BigInteger),
    Column("commit", Text),
    Column("status", String(16), nullable=False),  # queued | running | done | failed
    Column("error", Text),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("finished_at", DateTime(timezone=True)),
)

backtest_metrics = Table(
    "backtest_metrics", metadata,
    Column("run_id", String(32), ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("metrics", JSONB, nullable=False),
)

equity_points = Table(
    "equity_points", metadata,
    Column("run_id", String(32), ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
    Column("idx", Integer, primary_key=True),
    Column("date", Text, nullable=False),
    Column("net_cum_return", Float, nullable=False),
    Column("gross_cum_return", Float, nullable=False),
    Column("net_drawdown", Float, nullable=False),
)

PROJECTION_TABLES = (orders, fills, trades, positions, risk_limits, sessions)
