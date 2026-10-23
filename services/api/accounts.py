"""Accounts and API keys (M10).

    python -m services.api.accounts create --name alice [--role trader|admin]

Prints one JSON line with the account id and its API key. The key is shown only here:
the database keeps its SHA-256 and prefix (services/api/auth.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from sqlalchemy import insert

from services.api.auth import new_account_row
from services.common.config import Settings
from services.db.engine import make_engine
from services.db.models import accounts


async def create(settings: Settings, name: str, role: str) -> dict[str, object]:
    row, key = new_account_row(name=name, role=role)
    engine = make_engine(settings)
    async with engine.begin() as conn:
        account_id = (await conn.execute(insert(accounts).values(**row).returning(accounts.c.id))).scalar_one()
    await engine.dispose()
    return {"account_id": int(account_id), "name": name, "role": role, "api_key": key}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.api.accounts")
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--name", required=True)
    c.add_argument("--role", choices=["trader", "admin"], default="trader")
    args = parser.parse_args(argv)
    print(json.dumps(asyncio.run(create(Settings.from_env(), args.name, args.role))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
