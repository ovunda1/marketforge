"""The benchmark tooling (M6): doc rendering, the no-typed-numbers lint, A/B statistics
and the results schema. Pure Python: no benchmark is run here."""

import copy

import pytest

from bench import render, schema, stats

SOURCES = {
    "replay": {
        "profiles": {
            "narrow": {"throughput": {"commands_per_sec": {"median": 12_345_678.9}},
                       "latency": {"p99": {"median": 412}}},
            "wide": {"throughput": {"commands_per_sec": {"median": 3_210_000.0}},
                     "latency": {"p99": {"median": 1_534}}},
        },
        "settings": {"n": 5_000_000, "reps": 5},
    },
}


def test_placeholders_format_and_label_their_profile() -> None:
    out = render.render(
        "Narrow: {{ replay:profiles.narrow.throughput.commands_per_sec.median|mps }}; "
        "p99 {{ replay:profiles.wide.latency.p99.median|ns }}; "
        "n = {{ replay:settings.n|int }}.",
        SOURCES,
    )
    assert out == ("Narrow: 12.35 M/s (narrow workload); p99 1.53 µs (wide workload); "
                   "n = 5,000,000.")


def test_bare_drops_the_label_but_only_inside_a_table() -> None:
    row = "| narrow | {{ replay:profiles.narrow.latency.p99.median|ns|bare }} |"
    assert render.render(row, SOURCES) == "| narrow | 412 ns |"
    with pytest.raises(render.RenderError, match="bare"):
        render.render("p99 {{ replay:profiles.narrow.latency.p99.median|ns|bare }}", SOURCES)


@pytest.mark.parametrize(
    ("fmt", "value", "expected"),
    [
        ("ns", 41.67, "42 ns"), ("ns", 999.4, "999 ns"), ("ns", 1534, "1.53 µs"),
        ("ns", 2_500_000, "2.50 ms"), ("mps", 950_000, "0.95 M/s"), ("int", 1234567, "1,234,567"),
        ("pct", 1.234, "+23.4%"), ("pct", 0.95, "-5.0%"), ("ratio", 1.2345, "1.23×"),
        ("fraction", 0.05, "5%"), ("f2", 3.14159, "3.14"), ("text", "abc", "abc"),
    ],
)
def test_formatters(fmt: str, value: object, expected: str) -> None:
    assert render.FORMATS[fmt](value) == expected


def test_missing_values_and_unknown_formats_are_errors() -> None:
    with pytest.raises(render.RenderError, match="profiles.medium"):
        render.render("{{ replay:profiles.medium.latency|ns }}", SOURCES)
    with pytest.raises(render.RenderError, match="nosuch"):
        render.render("{{ nosuch:a|ns }}", SOURCES)
    with pytest.raises(render.RenderError, match="weird"):
        render.render("{{ replay:settings.n|weird }}", SOURCES)


@pytest.mark.parametrize(
    "line",
    ["Throughput rose 15%.", "p99 fell to 412 ns", "a 1.5× speedup", "over 5 repetitions",
     "about 3,000 levels", "C++20 gives 7 ns"],
)
def test_lint_flags_typed_numbers(line: str) -> None:
    assert render.lint(line), line


@pytest.mark.parametrize(
    "line",
    ["p99 and p99.9 latency", "Milestone M6 uses SHA-256 on x86_64 and int64",
     "1. A numbered list item", "See `-O3 -mcpu=native` and `make bench SEED=7`",
     "{{ replay:settings.n|int }} commands", "the opt-1 tag", "C++20"],
)
def test_lint_allows_identifiers_code_and_placeholders(line: str) -> None:
    assert render.lint(line) == [], line


def test_blocks_render_tables() -> None:
    called = []
    render.BLOCKS["test_block"] = lambda sources, arg: called.append(arg) or "| a |"
    try:
        assert render.render("{{ block:test_block:x }}", SOURCES) == "| a |"
        assert called == ["x"]
    finally:
        del render.BLOCKS["test_block"]


# --- A/B statistics ----------------------------------------------------------------------


def test_ratio_summary_pairs_rounds() -> None:
    s = stats.ratio_summary(before=[100, 100, 100], after=[110, 120, 130])
    assert s == {"n": 3, "median": pytest.approx(1.2), "min": pytest.approx(1.1),
                 "max": pytest.approx(1.3)}
    with pytest.raises(ValueError):
        stats.ratio_summary([1, 2], [1])


@pytest.mark.parametrize(
    ("lo", "hi", "higher_is_better", "expected"),
    [
        (1.02, 1.10, True, "faster"), (0.90, 0.98, True, "slower"),
        (0.97, 1.04, True, "no measurable effect"), (0.80, 0.95, False, "faster"),
        (1.01, 1.20, False, "slower"), (0.95, 1.01, False, "no measurable effect"),
    ],
)
def test_verdict_needs_the_whole_range_on_one_side(lo, hi, higher_is_better, expected) -> None:
    s = {"n": 7, "median": (lo + hi) / 2, "min": lo, "max": hi}
    assert stats.verdict(s, higher_is_better=higher_is_better) == expected


def test_geomean() -> None:
    assert stats.geomean([1.21, 1.0]) == pytest.approx(1.1)


# --- Results schema ----------------------------------------------------------------------


def test_validators_accept_the_examples_and_report_every_missing_key() -> None:
    for name in ("replay", "micro", "ab"):
        example = schema.EXAMPLES[name]
        schema.VALIDATORS[name](example)
        broken = copy.deepcopy(example)
        del broken["machine"]["cpu"]
        del broken["build"]["cxx_flags"]
        with pytest.raises(schema.SchemaError) as e:
            schema.VALIDATORS[name](broken)
        assert "machine.cpu" in str(e.value) and "build.cxx_flags" in str(e.value)


def test_replay_needs_both_profiles_with_spread() -> None:
    broken = copy.deepcopy(schema.EXAMPLES["replay"])
    del broken["profiles"]["wide"]
    del broken["profiles"]["narrow"]["latency"]["p99"]["min"]
    with pytest.raises(schema.SchemaError) as e:
        schema.validate_replay(broken)
    assert "profiles.wide" in str(e.value)
    assert "profiles.narrow.latency.p99.min" in str(e.value)
