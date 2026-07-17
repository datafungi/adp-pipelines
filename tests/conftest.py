"""Shared fixtures.

`live_key_vault` is opt-in rather than autouse: it points Airflow at a real vault, and
only the Live tier (`-m integration`) may reach one. See docs/testing.md.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from dotenv import load_dotenv

if TYPE_CHECKING:
    from collections.abc import Iterator

# The local-dev SP, under the names DefaultAzureCredential reads. Mirrors docker-compose,
# except AZURE_TENANT_ID must be set: pytest has no equivalent of compose's default.
_CREDENTIAL_ENV = {
    "AZURE_CLIENT_ID": "KV_CLIENT_ID",
    "AZURE_CLIENT_SECRET": "KV_CLIENT_SECRET",
}
_REQUIRED = ("KV_URI", "KV_CLIENT_ID", "KV_CLIENT_SECRET", "AZURE_TENANT_ID")


@pytest.fixture(scope="session")
def live_key_vault() -> Iterator[None]:
    """Resolve Connections from the dev Key Vault for the session.

    Outside a task Airflow's fallback chain consults env vars and external backends only,
    never the metastore -- so this needs no database and reaches the same
    AzureKeyVaultBackend AKS uses, differing in credential and nothing else.
    """
    load_dotenv(Path(__file__).parents[1] / ".env")

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
