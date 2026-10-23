"""Architecture rules that are easy to break by accident (M10): API workers never load
the engine (one process owns it), the matcher never touches Postgres (the projection is
off the matching path), and no secret is baked into an image or committed."""

from __future__ import annotations

import re
import subprocess
import sys

import pytest

from conftest import REPO_ROOT

DEPLOY = REPO_ROOT / "deploy"


def loaded_modules(module: str) -> set[str]:
    code = f"import sys, {module}; print('\\n'.join(sys.modules))"
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True)
    return set(out.stdout.split())


def test_the_api_never_loads_the_engine() -> None:
    mods = loaded_modules("services.api.app")
    assert "fastapi" in mods
    assert not {m for m in mods if m.split(".")[0] in ("mfengine", "research") or m.startswith("services.matcher")}


def test_the_matcher_never_loads_a_database_driver() -> None:
    mods = loaded_modules("services.matcher.app")
    assert "mfengine" in mods
    assert not {m for m in mods if m.split(".")[0] in ("sqlalchemy", "asyncpg", "alembic", "psycopg", "psycopg2")}


def test_no_secret_values_in_deploy_files() -> None:
    files = [p for p in DEPLOY.rglob("*") if p.is_file()] + [REPO_ROOT / ".env.example"]
    assert any(p.name == "Dockerfile" for p in files) and any(p.name == "docker-compose.yml" for p in files)
    for p in files:
        text = p.read_text()
        for name in ("POSTGRES_PASSWORD", "PGPASSWORD", "TIINGO_API_KEY", "MF_BOOTSTRAP_ADMIN_KEY"):
            for m in re.finditer(rf"(?<![{{\w]){name}[ \t]*[:=][ \t]*(\S*)", text):  # not a ${NAME} reference
                value = m.group(1).strip("\"'")
                assert value == "" or value.startswith("${") or value == "change-me", (p, m.group(0))
    dockerfile = (DEPLOY / "Dockerfile").read_text()
    assert not re.search(r"^\s*(ARG|ENV)\s+\S*(PASSWORD|API_KEY|SECRET)", dockerfile, re.M)
    assert ".env" in (REPO_ROOT / ".dockerignore").read_text().split()


def test_dot_env_is_ignored_by_git() -> None:
    out = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=REPO_ROOT)
    assert out.returncode == 0


def test_compose_requires_the_database_password_and_gives_tiingo_only_to_the_worker() -> None:
    import yaml

    compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    services = compose["services"]
    assert services["postgres"]["environment"]["POSTGRES_PASSWORD"].startswith("${POSTGRES_PASSWORD:?")
    with_tiingo = [name for name, s in services.items() if "TIINGO_API_KEY" in str(s.get("environment", {}))]
    assert with_tiingo == ["worker"]
    assert services["matcher"].get("deploy", {}).get("replicas", 1) == 1
    assert "ports" not in services["matcher"]


@pytest.mark.parametrize("module", ["services.matcher", "services.projector", "services.worker.tasks"])
def test_service_entry_points_import(module: str) -> None:
    subprocess.run([sys.executable, "-c", f"import {module}"], cwd=REPO_ROOT, check=True)
