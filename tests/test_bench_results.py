"""Committed benchmark results and the docs generated from them (M6).

Every number in bench/RESULTS.md, bench/OPTIMIZATIONS.md and docs/learning/m6.md comes
from the JSON in bench/results/: the committed docs must equal what their templates
render, and the templates may not contain typed numbers."""

import json
from pathlib import Path

import pytest

from bench import render, schema
from conftest import REPO_ROOT

BENCH = REPO_ROOT / "bench"
RESULTS = BENCH / "results"
TEMPLATES = sorted([*BENCH.glob("*.md.in"), *(REPO_ROOT / "docs" / "learning").glob("*.md.in")])


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


def test_templates_exist() -> None:
    names = {p.name for p in TEMPLATES}
    assert {"RESULTS.md.in", "OPTIMIZATIONS.md.in", "m6.md.in"} <= names


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_rendered_doc_is_up_to_date(template: Path) -> None:
    rendered = render.render(template.read_text(), render.load_sources(REPO_ROOT))
    target = template.with_suffix("")  # X.md.in -> X.md
    assert target.read_text() == rendered, f"run `make bench-docs` to regenerate {target.name}"


@pytest.mark.parametrize("template", TEMPLATES, ids=lambda p: p.name)
def test_templates_hold_no_typed_numbers(template: Path) -> None:
    assert render.lint(template.read_text()) == []


def test_replay_results_are_complete_and_match_the_pinned_digests() -> None:
    doc = _load(RESULTS / "replay.json")
    schema.validate_replay(doc)
    expected = _load(BENCH / "expected_digests.json")
    n = str(doc["settings"]["n"])
    for profile in ("narrow", "wide"):
        assert doc["profiles"][profile]["digest"]["events"] == expected[profile][n]


def test_micro_results_are_complete() -> None:
    doc = _load(RESULTS / "micro.json")
    schema.validate_micro(doc)
    for name in ("AddExistingLevel", "CancelKeepsLevel", "ReplaceQtyDown", "MatchOneLevel/1",
                 "MatchMultiLevel/8", "RiskCheck", "EngineAdd"):
        assert name in doc["benchmarks"], name


def test_every_logged_optimization_has_its_ab_results() -> None:
    manifest = _load(BENCH / "optimizations.json")
    for opt in manifest["optimizations"]:
        doc = _load(RESULTS / "ab" / f"{opt['id']}.json")
        schema.validate_ab(doc)
        assert doc["before"]["ref"] == opt["before"] and doc["after"]["ref"] == opt["after"]
        for profile in ("narrow", "wide"):
            # Before and after ran the same workload to the same events.
            assert doc["profiles"][profile]["digest_match"] is True


def test_results_were_measured_on_ac_power() -> None:
    for path in [RESULTS / "replay.json", RESULTS / "micro.json", *sorted((RESULTS / "ab").glob("*.json"))]:
        power = _load(path)["machine"]["power"]
        assert power["forced"] is False, f"{path.name} was measured with --force"
