"""Contract: every DAG folder carries a loadable config.toml.

Iterates dags/*/ so a new folder cannot skip validation by omitting its own test file.
See docs/testing.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from utils.dag_config import load_dag_config

ROOT = Path(__file__).parents[2]


def _dag_folders() -> list[Path]:
    return sorted(
        path
        for path in (ROOT / "dags").iterdir()
        if path.is_dir() and not path.name.startswith((".", "_"))
    )


def test_dag_folders_are_discovered():
    """Guards the parametrize below: an empty set would collect no tests and pass."""
    assert _dag_folders()


@pytest.mark.parametrize("dag_folder", _dag_folders(), ids=lambda path: path.name)
def test_dag_folder_config_loads(dag_folder):
    """load_dag_config raises on a missing file and on malformed TOML; an empty config
    carries no DAG metadata and is a folder that forgot one."""
    assert load_dag_config(dag_folder)
