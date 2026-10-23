"""Initial schema: instruments, accounts, the projection tables and the backtest tables.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _now() -> sa.sql.elements.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    op.create_table(
        "instruments",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("tick_size", sa.Text, nullable=False))
    op.create_table(
        "accounts",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("api_key_prefix", sa.String(8), nullable=False, unique=True),
        sa.Column("api_key_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()))
    op.create_table(
        "risk_limits",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("scope", sa.String(16), primary_key=True),
        sa.Column("account_id", sa.BigInteger, primary_key=True),
        sa.Column("limits", JSONB, nullable=False),
        sa.Column("seq_no", sa.BigInteger, nullable=False))
    op.create_table(
        "sessions",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("seq_no", sa.BigInteger, nullable=False))
    op.create_table(
        "orders",
        sa.Column("order_id", sa.BigInteger, primary_key=True),
        sa.Column("symbol", sa.String(16), nullable=False),
        sa.Column("account_id", sa.BigInteger, nullable=False),
        sa.Column("client_order_id", sa.Text),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("order_type", sa.String(8), nullable=False),
        sa.Column("tif", sa.String(4), nullable=False),
        sa.Column("post_only", sa.Boolean, nullable=False),
        sa.Column("stp", sa.String(16), nullable=False),
        sa.Column("price", sa.BigInteger, nullable=False),
        sa.Column("qty", sa.BigInteger, nullable=False),
        sa.Column("leaves_qty", sa.BigInteger, nullable=False),
        sa.Column("cum_qty", sa.BigInteger, nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reject_reason", sa.String(32)),
        sa.Column("created_seq", sa.BigInteger, nullable=False),
        sa.Column("created_ts", sa.BigInteger, nullable=False),
        sa.Column("updated_seq", sa.BigInteger, nullable=False),
        sa.Column("updated_ts", sa.BigInteger, nullable=False),
        sa.UniqueConstraint("account_id", "client_order_id", name="orders_account_client_order_id"))
    op.create_index("orders_symbol_status", "orders", ["symbol", "status"])
    op.create_table(
        "fills",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("trade_id", sa.BigInteger, primary_key=True),
        sa.Column("order_id", sa.BigInteger, primary_key=True),
        sa.Column("account_id", sa.BigInteger, nullable=False),
        sa.Column("side", sa.String(4), nullable=False),
        sa.Column("liquidity", sa.String(8), nullable=False),
        sa.Column("price", sa.BigInteger, nullable=False),
        sa.Column("qty", sa.BigInteger, nullable=False),
        sa.Column("leaves_qty", sa.BigInteger, nullable=False),
        sa.Column("cum_qty", sa.BigInteger, nullable=False),
        sa.Column("seq_no", sa.BigInteger, nullable=False),
        sa.Column("ts", sa.BigInteger, nullable=False))
    op.create_index("fills_order_id", "fills", ["order_id"])
    op.create_table(
        "trades",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("trade_id", sa.BigInteger, primary_key=True),
        sa.Column("price", sa.BigInteger, nullable=False),
        sa.Column("qty", sa.BigInteger, nullable=False),
        sa.Column("maker_order_id", sa.BigInteger, nullable=False),
        sa.Column("taker_order_id", sa.BigInteger, nullable=False),
        sa.Column("aggressor_side", sa.String(4), nullable=False),
        sa.Column("seq_no", sa.BigInteger, nullable=False),
        sa.Column("ts", sa.BigInteger, nullable=False))
    op.create_table(
        "positions",
        sa.Column("account_id", sa.BigInteger, primary_key=True),
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("qty", sa.BigInteger, nullable=False),
        sa.Column("cash", sa.Numeric(38, 0), nullable=False),
        sa.Column("updated_seq", sa.BigInteger, nullable=False))
    op.create_table(
        "projection_cursor",
        sa.Column("symbol", sa.String(16), primary_key=True),
        sa.Column("last_seq_no", sa.BigInteger, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()))
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.BigInteger, nullable=False),
        sa.Column("config", JSONB, nullable=False),
        sa.Column("seed", sa.BigInteger),
        sa.Column("commit", sa.Text),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error", sa.Text),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=_now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)))
    op.create_table(
        "backtest_metrics",
        sa.Column("run_id", sa.String(32), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("metrics", JSONB, nullable=False))
    op.create_table(
        "equity_points",
        sa.Column("run_id", sa.String(32), sa.ForeignKey("backtest_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("idx", sa.Integer, primary_key=True),
        sa.Column("date", sa.Text, nullable=False),
        sa.Column("net_cum_return", sa.Float, nullable=False),
        sa.Column("gross_cum_return", sa.Float, nullable=False),
        sa.Column("net_drawdown", sa.Float, nullable=False))


def downgrade() -> None:
    for table in ["equity_points", "backtest_metrics", "backtest_runs", "projection_cursor", "positions", "trades",
                  "fills", "orders", "sessions", "risk_limits", "accounts", "instruments"]:
        op.drop_table(table)
