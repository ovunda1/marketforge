"""python -m services.projector run | verify | rebuild (services/projector/app.py)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from services.common.config import Settings
from services.projector import app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m services.projector")
    parser.add_argument("command", choices=["run", "verify", "rebuild"])
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format='{"level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}')
    settings = Settings.from_env()
    if args.command == "run":
        asyncio.run(app.run(settings))
    elif args.command == "verify":
        report = asyncio.run(app.verify(settings))
        print(json.dumps(report))
        return 0 if report["ok"] else 1
    else:
        asyncio.run(app.rebuild(settings))
        print("projection reset; the projector rebuilds it from the journals")
    return 0


if __name__ == "__main__":
    sys.exit(main())
