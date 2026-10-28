"""Writes results/tests.json (M11): how many tests each suite ran and passed, from JUnit
XML reports. The dashboard's Overview reads it (with results/difftest.json and
bench/results/*.json): no test count is ever typed into the page.

    make facts      # runs every offline suite with JUnit output, then this script
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def count(report: Path) -> dict[str, int]:
    root = ET.parse(report).getroot()
    suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
    out = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
    for s in suites:
        for k in out:
            out[k] += int(s.get(k, s.get("disabled", 0) if k == "skipped" else 0) or 0)
    out["passed"] = out["tests"] - out["failures"] - out["errors"] - out["skipped"]
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="results/facts.py")
    parser.add_argument("--out", type=Path, default=Path("results/tests.json"))
    parser.add_argument("suite", nargs="+", help="name=path/to/junit.xml")
    args = parser.parse_args(argv)
    suites = {}
    for item in args.suite:
        name, _, path = item.partition("=")
        suites[name] = count(Path(path))
    # The file is committed with the work it counts, so it cannot name that commit: it
    # records the date, the commit the work is based on, and whether the tree had changes.
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    dirty = bool(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout.strip())
    doc = {"schema": 1, "generated_by": "make facts", "date": dt.date.today().isoformat(), "base_commit": commit,
           "dirty": dirty, "suites": suites,
           "total": {k: sum(s[k] for s in suites.values()) for k in ("tests", "passed", "failures", "errors",
                                                                     "skipped")}}
    args.out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(json.dumps(doc["total"]))
    return 1 if doc["total"]["failures"] or doc["total"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
