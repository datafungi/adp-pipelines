"""Tests for utils.dag_config.load_dag_config."""

from __future__ import annotations

import pytest

from utils.dag_config import load_dag_config


def test_load_dag_config_parses_toml_sections(tmp_path):
    dag_folder = tmp_path / "ta_sharepoint_ingest"
    dag_folder.mkdir()
    (dag_folder / "config.toml").write_text(
        "[dag]\n"
        'display_name = "TA SharePoint Requisitions Ingest"\n'
        'owner = "data-eng"\n'
        'schedule = "@weekly"\n'
        "\n"
        "[sharepoint]\n"
        'site_url = "https://contoso.sharepoint.com/sites/TalentAcquisition"\n'
        'sharepoint_conn_id = "sharepoint_default"\n'
    )

    config = load_dag_config(dag_folder)

    assert config == {
        "dag": {
            "display_name": "TA SharePoint Requisitions Ingest",
            "owner": "data-eng",
            "schedule": "@weekly",
        },
        "sharepoint": {
            "site_url": "https://contoso.sharepoint.com/sites/TalentAcquisition",
            "sharepoint_conn_id": "sharepoint_default",
        },
    }


def test_load_dag_config_accepts_str_path(tmp_path):
    dag_folder = tmp_path / "ta_sharepoint_ingest"
    dag_folder.mkdir()
    (dag_folder / "config.toml").write_text(
        '[sharepoint]\nsite_url = "https://contoso.sharepoint.com/sites/TA"\n'
    )

    config = load_dag_config(str(dag_folder))

    assert config["sharepoint"]["site_url"] == "https://contoso.sharepoint.com/sites/TA"


def test_load_dag_config_missing_file_raises(tmp_path):
    dag_folder = tmp_path / "no_config_here"
    dag_folder.mkdir()

    with pytest.raises(FileNotFoundError, match="config.toml"):
        load_dag_config(dag_folder)


def test_load_dag_config_empty_file_returns_empty_dict(tmp_path):
    dag_folder = tmp_path / "empty_config"
    dag_folder.mkdir()
    (dag_folder / "config.toml").write_text("")

    assert load_dag_config(dag_folder) == {}


def test_load_dag_config_invalid_toml_raises(tmp_path):
    dag_folder = tmp_path / "broken_config"
    dag_folder.mkdir()
    (dag_folder / "config.toml").write_text("[dag\nowner = data-eng\n")

    with pytest.raises(ValueError):
        load_dag_config(dag_folder)
