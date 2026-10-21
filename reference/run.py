"""Run a command file through the reference book and write canonical output.

Usage: python -m reference.run <commands-file> [<events-file>]
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Iterator

from reference.book import ReferenceBook
from reference.commands import parse_command


def run_lines(lines: Iterable[str]) -> Iterator[str]:
    """Each command's events, then its BOOK line; the final depth at the end."""
    book = ReferenceBook()
    for line in lines:
        command = parse_command(line)
        if command is None:
            continue
        yield from book.process(command)
        yield book.top_of_book(command.seq)
    yield from book.depth()


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 1
    with open(argv[1]) as f:
        lines = f.read().splitlines()
    out = open(argv[2], "w") if len(argv) == 3 else sys.stdout
    try:
        for line in run_lines(lines):
            out.write(line + "\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
