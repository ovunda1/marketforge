"""Machine, OS, power and git facts for every results file."""

from __future__ import annotations

import os
import platform
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _sysctl(name: str) -> str:
    return _run("sysctl", "-n", name)


def _int_or_none(text: str) -> int | None:
    return int(text) if text.isdigit() else None


def power() -> dict:
    """Power source and Low Power Mode (macOS); 'n/a' elsewhere."""
    if platform.system() != "Darwin":
        return {"source": "n/a", "low_power_mode": None}
    batt = _run("pmset", "-g", "batt")
    m = re.search(r"Now drawing from '([^']+)'", batt)
    settings = _run("pmset", "-g")
    lpm = re.search(r"lowpowermode\s+(\d)", settings)
    return {"source": m.group(1) if m else "unknown",
            "low_power_mode": bool(int(lpm.group(1))) if lpm else None}


def power_problem(p: dict) -> str | None:
    """Why this machine should not be benchmarked right now, or None."""
    if p["source"] not in ("AC Power", "n/a"):
        return f"running on {p['source']}: plug in the charger"
    if p["low_power_mode"]:
        return "Low Power Mode is on: turn it off"
    return None


def machine(forced: bool) -> dict:
    p = power()
    p["forced"] = forced
    if platform.system() == "Darwin":
        info = {
            "cpu": _sysctl("machdep.cpu.brand_string"),
            "model": _sysctl("hw.model"),
            "performance_cores": _int_or_none(_sysctl("hw.perflevel0.logicalcpu")),
            "efficiency_cores": _int_or_none(_sysctl("hw.perflevel1.logicalcpu")),
            "logical_cpus": _int_or_none(_sysctl("hw.logicalcpu")),
            "memory_bytes": _int_or_none(_sysctl("hw.memsize")),
            "os": f"macOS {_run('sw_vers', '-productVersion')} ({_run('sw_vers', '-buildVersion')}), "
                  f"Darwin {platform.release()}",
            "thermal": " ".join(_run("pmset", "-g", "therm").split()),
        }
    else:
        cpuinfo = Path("/proc/cpuinfo").read_text() if Path("/proc/cpuinfo").exists() else ""
        m = re.search(r"model name\s*:\s*(.+)", cpuinfo)
        meminfo = Path("/proc/meminfo").read_text() if Path("/proc/meminfo").exists() else ""
        mem = re.search(r"MemTotal:\s*(\d+) kB", meminfo)
        os_release = Path("/etc/os-release")
        pretty = re.search(r'PRETTY_NAME="([^"]+)"', os_release.read_text()) if os_release.exists() else None
        info = {
            "cpu": m.group(1).strip() if m else platform.processor() or platform.machine(),
            "model": platform.machine(),
            "performance_cores": None,
            "efficiency_cores": None,
            "logical_cpus": os.cpu_count(),
            "memory_bytes": int(mem.group(1)) * 1024 if mem else None,
            "os": f"{pretty.group(1) if pretty else platform.system()}, kernel {platform.release()}",
            "thermal": "n/a",
        }
    info["power"] = p
    return info


# The code a measurement depends on: "dirty" means some of it differs from the commit.
# Docs, templates and results do not count.
MEASURED = ("engine", "bench/lib", "bench/*.cpp", "bench/*.hpp", "bench/build_info.hpp.in", "bench/CMakeLists.txt",
            "CMakeLists.txt", "bindings", "bench/bindings.py", "pyproject.toml")


def git_info(root: Path = REPO_ROOT) -> dict:
    commit = _run("git", "-C", str(root), "rev-parse", "HEAD")
    dirty = bool(_run("git", "-C", str(root), "status", "--porcelain", "--untracked-files=all",
                      "--", *MEASURED))
    return {"commit": commit, "dirty": dirty}
