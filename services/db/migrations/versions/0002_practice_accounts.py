"""Practice accounts (M11): self-service accounts the dashboard creates in demo mode.
They are traders like any other, marked so the gateway can cap their backtests; the
status filter index serves the per-account count of queued and running runs.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("accounts", sa.Column("practice", sa.Boolean, nullable=False, server_default=sa.false()))
    op.create_index("ix_backtest_runs_account_status", "backtest_runs", ["account_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_backtest_runs_account_status", "backtest_runs")
    op.drop_column("accounts", "practice")
