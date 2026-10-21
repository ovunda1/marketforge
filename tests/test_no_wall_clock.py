"""The engine never reads the host clock: time comes from an injected Clock (M4)."""

import re

from conftest import REPO_ROOT

# Wall-clock and monotonic-clock reads. Benchmarks (engine/bench) may time themselves,
# so only the library sources are scanned.
BANNED = re.compile(
    r"system_clock|steady_clock|high_resolution_clock|\btime\s*\(|gettimeofday|clock_gettime"
    r"|\blocaltime|\bgmtime|\bclock\s*\(\s*\)"
)


def test_engine_sources_never_read_the_host_clock() -> None:
    offenders = []
    for directory in ("engine/include", "engine/src"):
        for path in sorted((REPO_ROOT / directory).rglob("*")):
            if path.suffix not in (".hpp", ".cpp", ".h"):
                continue
            for n, line in enumerate(path.read_text().splitlines(), start=1):
                if BANNED.search(line):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_the_pattern_catches_clock_reads() -> None:
    for line in ("auto t = std::chrono::system_clock::now();", "time(nullptr)", "gettimeofday(&tv, 0)"):
        assert BANNED.search(line), line
    assert not BANNED.search("Timestamp now() override;")
