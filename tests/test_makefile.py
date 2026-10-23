"""Makefile recipes that are easy to get subtly wrong (M8)."""

import shutil
import subprocess

import pytest

from conftest import REPO_ROOT


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_sanitize_reads_the_compiler_after_configuring(tmp_path) -> None:
    # Make expands a whole recipe before running its first line, so a compiler read from
    # the CMake cache by $(shell ...) is read before `cmake -S -B` has configured a fresh
    # tree, and the ASan runtime silently fell back to plain `c++`. The read must happen
    # in the shell, when the preload line runs.
    asan_dir = tmp_path / "never-configured"
    out = subprocess.run(
        ["make", "-n", "sanitize", f"ASAN_DIR={asan_dir}"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout
    preload = [line for line in out.splitlines() if "asan_runtime.sh" in line]
    assert len(preload) == 1, out
    assert f"cmake -LA -N {asan_dir}" in preload[0]
    assert "asan_runtime.sh c++" not in preload[0]


def test_asan_runtime_refuses_an_empty_compiler() -> None:
    # A failed cache read must not fall back to the default compiler's runtime.
    result = subprocess.run(
        [str(REPO_ROOT / "bindings" / "asan_runtime.sh"), ""], capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "empty compiler" in result.stderr


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_test_services_uses_its_own_project_and_tears_it_down(tmp_path) -> None:
    # M10: the compose suite must not run against (or delete) the `make up` stack, and
    # must remove its stack and volumes even when tests fail, unless KEEP is set.
    def dry(*args: str) -> str:
        return subprocess.run(["make", "-n", "test-services", *args], cwd=REPO_ROOT, capture_output=True,
                              text=True, check=True).stdout
    out = dry()
    assert "-p mf-test" in out and "deploy/docker-compose.test.yml" in out and "--env-file .env" in out
    assert "down -v" in out and "exit $status" in out
    assert "MF_COMPOSE_API=" in out
    up = subprocess.run(["make", "-n", "up"], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout
    assert "-p mf-test" not in up and "docker-compose.test.yml" not in up


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_e2e_uses_the_test_project_and_tears_it_down_from_the_repo_root() -> None:
    # M11: like test-services, and the teardown runs outside dashboard/ (where .env is not).
    out = subprocess.run(["make", "-n", "-W", "dashboard/package.json", "e2e"], cwd=REPO_ROOT, capture_output=True,
                         text=True, check=True).stdout
    assert "-p mf-test" in out and "down -v" in out and "exit $status" in out
    run = next(line for line in out.splitlines() if "playwright test" in line)
    assert run.lstrip().startswith("(cd dashboard &&") and "MF_COMPOSE=" in run


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_difftest_long_records_its_outcome_for_the_overview() -> None:
    out = subprocess.run(["make", "-n", "difftest-long"], cwd=REPO_ROOT, capture_output=True, text=True,
                         check=True).stdout
    assert out.count("--record results/difftest.json") == 2 and "--profile wide" in out


@pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
def test_only_the_demo_and_the_browser_tests_start_the_bot() -> None:
    # M11: the demo bot writes all the time; the API suite's projection checks need a quiet
    # exchange, so its stack starts without the "demo" profile (and removes a leftover bot).
    def dry(target: str) -> list[str]:
        out = subprocess.run(["make", "-n", "-W", "dashboard/package.json", target], cwd=REPO_ROOT,
                             capture_output=True, text=True, check=True).stdout
        return [line for line in out.splitlines() if " up " in line or " down " in line]
    services = dry("test-services")
    assert any("up -d" in line and "--profile demo" not in line for line in services)
    assert all("--profile demo" in line for line in services if " down " in line)
    assert all("--profile demo" in line for line in dry("e2e"))
    assert all("--profile demo" in line for line in dry("up"))
