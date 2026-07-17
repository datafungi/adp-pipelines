"""Shared fixtures.

`live_key_vault` is opt-in rather than autouse: it points Airflow at a real vault, and
only the Live tier (`-m integration`) may reach one. The import fixtures below are shared
by the Contract tier, which checks the delivered code's imports against the lock, and the
Image tier, which imports them inside the built image. See docs/testing.md.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dotenv import load_dotenv

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).parents[1]

# Airflow puts plugins/ and dags/ on sys.path at runtime; provider_registration is
# pip-installed separately with --no-deps, so it answers to a different rule.
_DELIVERED = ("plugins", "dags")
# Mirrors pytest's `pythonpath`: imports resolving here are first-party, not packages.
_SOURCE_ROOTS = ("plugins", "dags", "provider_registration")

# The local-dev SP, under the names DefaultAzureCredential reads. Mirrors docker-compose,
# except AZURE_TENANT_ID must be set: pytest has no equivalent of compose's default.
_CREDENTIAL_ENV = {
    "AZURE_CLIENT_ID": "KV_CLIENT_ID",
    "AZURE_CLIENT_SECRET": "KV_CLIENT_SECRET",
}
_REQUIRED = ("KV_URI", "KV_CLIENT_ID", "KV_CLIENT_SECRET", "AZURE_TENANT_ID")


def canonical(name: str) -> str:
    """PEP 503 distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _first_party() -> set[str]:
    return {
        path.stem if path.suffix == ".py" else path.name
        for root in _SOURCE_ROOTS
        for path in (ROOT / root).iterdir()
        if not path.name.startswith((".", "_"))
    }


@pytest.fixture(scope="session")
def declared_dependencies() -> set[str]:
    """Distribution names in [project.dependencies] -- what the image installs."""
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    return {
        canonical(re.split(r"[=<>~!\[; ]", spec, maxsplit=1)[0])
        for spec in pyproject["project"]["dependencies"]
    }


@pytest.fixture(scope="session")
def delivered_imports() -> dict[str, set[str]]:
    """Top-level module name -> the git-synced files importing it."""
    found: dict[str, set[str]] = {}
    for root in _DELIVERED:
        for path in (ROOT / root).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and not node.level:
                    names = [node.module or ""]
                else:
                    continue
                for name in names:
                    top = name.split(".")[0]
                    if top:
                        found.setdefault(top, set()).add(str(path.relative_to(ROOT)))
    return found


@pytest.fixture(scope="session")
def third_party_imports(delivered_imports) -> list[str]:
    """The delivered code's third-party module names."""
    first_party = _first_party()
    return sorted(
        module
        for module in delivered_imports
        if module not in sys.stdlib_module_names and module not in first_party
    )


@pytest.fixture(scope="session")
def module_distributions(third_party_imports) -> dict[str, set[str]]:
    """Third-party module -> the canonical distributions providing it, empty if none."""
    installed = packages_distributions()
    return {
        module: {canonical(dist) for dist in installed.get(module, [])}
        for module in third_party_imports
    }


@pytest.fixture(scope="session")
def live_key_vault() -> Iterator[None]:
    """Resolve Connections from the dev Key Vault for the session.

    Outside a task Airflow's fallback chain consults env vars and external backends only,
    never the metastore -- so this needs no database and reaches the same
    AzureKeyVaultBackend AKS uses, differing in credential and nothing else.
    """
    load_dotenv(ROOT / ".env")

    missing = [name for name in _REQUIRED if not os.getenv(name)]
    if missing:
        pytest.skip(f"not set (see .env.example): {', '.join(missing)}")

    backend_kwargs = {
        "connections_prefix": "airflow-connections",
        "variables_prefix": "airflow-variables",
        "vault_url": os.environ["KV_URI"],
    }

    monkeypatch = pytest.MonkeyPatch()
    try:
        for target, source in _CREDENTIAL_ENV.items():
            monkeypatch.setenv(target, os.environ[source])
        monkeypatch.setenv(
            "AIRFLOW__SECRETS__BACKEND",
            "airflow.providers.microsoft.azure.secrets.key_vault.AzureKeyVaultBackend",
        )
        monkeypatch.setenv(
            "AIRFLOW__SECRETS__BACKEND_KWARGS", json.dumps(backend_kwargs)
        )
        yield
    finally:
        monkeypatch.undo()
