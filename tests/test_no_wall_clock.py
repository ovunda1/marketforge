"""The engine never reads the host clock: time comes from an injected Clock (M4).

Benchmarks (M6) time themselves, but only from bench/: every C++ source under engine/
(library, tools and tests) is scanned."""

import re

from conftest import REPO_ROOT

# Wall-clock, monotonic-clock and cycle-counter reads.
BANNED = re.compile(
    r"system_clock|steady_clock|high_resolution_clock|\btime\s*\(|gettimeofday|clock_gettime"
    r"|\blocaltime|\bgmtime|\bclock\s*\(\s*\)|mach_absolute_time|mach_continuous_time"
    r"|clock_gettime_nsec_np|cntvct_el0|\brdtsc|__rdtsc|__builtin_readcyclecounter"
)


def _offenders(directory: str) -> list[str]:
    found = []
    for path in sorted((REPO_ROOT / directory).rglob("*")):
        if path.suffix not in (".hpp", ".cpp", ".h", ".in") or "golden" in path.parts:
            continue
        for n, line in enumerate(path.read_text().splitlines(), start=1):
            if BANNED.search(line):
                found.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    return found


def test_engine_sources_never_read_the_host_clock() -> None:
    offenders = _offenders("engine")
    assert not offenders, "\n".join(offenders)


def test_binding_sources_never_read_the_host_clock() -> None:
    # The bindings (M7) are glue around the engine: time still comes from the caller.
    offenders = _offenders("bindings")
    assert not offenders, "\n".join(offenders)


def test_benchmarks_do_read_a_clock() -> None:
    # The scan would see bench/'s timing code: proof that it looks in the right places.
    assert any("bench/lib/runner.hpp" in o for o in _offenders("bench"))


def test_the_pattern_catches_clock_reads() -> None:
    for line in (
        "auto t = std::chrono::system_clock::now();", "time(nullptr)", "gettimeofday(&tv, 0)",
        "std::chrono::steady_clock::now()", "mach_absolute_time()", "__rdtsc()",
        "asm volatile(\"mrs %0, cntvct_el0\" : \"=r\"(t));", "clock_gettime_nsec_np(CLOCK_UPTIME_RAW)",
    ):
        assert BANNED.search(line), line
    assert not BANNED.search("Timestamp now() override;")
