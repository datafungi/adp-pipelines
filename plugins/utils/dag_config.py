"""Loader for the per-DAG-folder `config.toml` convention.

Each DAG folder under `dags/` carries a `config.toml` holding DAG metadata
(schedule, owner, display name, ...) and per-integration settings (e.g. a
`[sharepoint]` section with `site_url` for a SharePoint hook), namespaced
by section.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.toml"


def load_dag_config(dag_folder: str | Path) -> dict[str, Any]:
    """Parse `config.toml` from `dag_folder` and return it as a dict."""
    config_path = Path(dag_folder) / CONFIG_FILENAME
    if not config_path.is_file():
        raise FileNotFoundError(
            f"No {CONFIG_FILENAME} found in DAG folder '{dag_folder}'."
        )

    try:
        return tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{config_path} is not valid TOML: {exc}") from exc
