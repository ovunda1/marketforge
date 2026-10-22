"""Shared helpers for the mfengine binding tests (M7)."""

from __future__ import annotations

import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path

import mfengine as mf
from reference import commands as ref

SIDES = {"B": mf.Side.BUY, "S": mf.Side.SELL}
TYPES = {"LMT": mf.OrderType.LIMIT, "MKT": mf.OrderType.MARKET}
TIFS = {"GTC": mf.TimeInForce.GTC, "IOC": mf.TimeInForce.IOC, "FOK": mf.TimeInForce.FOK,
        "DAY": mf.TimeInForce.DAY}
STPS = {"CN": mf.StpMode.CANCEL_NEWEST, "CO": mf.StpMode.CANCEL_OLDEST,
        "CB": mf.StpMode.CANCEL_BOTH}


def to_mf(command: ref.Command) -> mf.Command:
    """A reference command as an mfengine command, built field by field (no parsing)."""
    if isinstance(command, ref.NewOrder):
        return mf.NewOrder(seq_no=command.seq, order_id=command.order_id,
                           account_id=command.account, side=SIDES[command.side],
                           type=TYPES[command.type], tif=TIFS[command.tif],
                           post_only=command.post_only, stp=STPS[command.stp],
                           price=command.price, qty=command.qty)
    if isinstance(command, ref.Cancel):
        return mf.Cancel(seq_no=command.seq, order_id=command.order_id)
    return mf.Replace(seq_no=command.seq, order_id=command.order_id, price=command.price,
                      qty=command.qty)


def built_commands(lines: Iterable[str]) -> list[mf.Command]:
    """Book-only command lines (the generator's) as Python-built mfengine commands."""
    out = []
    for line in lines:
        command = ref.parse_command(line)
        if command is not None:
            out.append(to_mf(command))
    return out


def timed(lines: Iterable[str]) -> list[tuple[mf.Command, int]]:
    """Any canonical command lines, clock lines included, as (command, timestamp)."""
    now = 0
    out = []
    for line in lines:
        parsed = mf.parse_line(line)
        if parsed is None:
            continue
        if isinstance(parsed, mf.ClockSet):
            now = parsed.timestamp
        else:
            out.append((parsed, now))
    return out


def command_output(engine: mf.Engine, events: Sequence[mf.Event], seq_no: int) -> str:
    """One command's canonical output: its events, then the BOOK line."""
    return "".join(f"{e}\n" for e in events) + mf.format_top_of_book(engine, seq_no) + "\n"


def drive(commands: Iterable[tuple[mf.Command, int]], engine: mf.Engine | None = None) -> bytes:
    """The canonical stream of the commands, one process() call each."""
    engine = engine or mf.Engine()
    parts = [command_output(engine, engine.process(c, timestamp=ts), c.seq_no)
             for c, ts in commands]
    parts.append(mf.format_depth(engine))
    return "".join(parts).encode()


def batch_of(commands: Iterable[tuple[mf.Command, int]]) -> mf.CommandBatch:
    batch = mf.CommandBatch()
    batch.extend(commands)
    return batch


def run_driver(driver: Path, cmds: Path, config: Path | None = None) -> bytes:
    args = [str(driver), *(["--config", str(config)] if config else []), str(cmds)]
    result = subprocess.run(args, capture_output=True)
    assert result.returncode == 0, result.stderr.decode()
    return result.stdout


def write_lines(path: Path, lines: Iterable[str]) -> Path:
    path.write_text("".join(f"{line}\n" for line in lines))
    return path
