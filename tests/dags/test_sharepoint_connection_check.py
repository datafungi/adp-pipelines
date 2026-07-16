"""Tests for the sharepoint_connection_check example DAG.

Parses rather than runs it: a DAG that fails to import is invisible in the UI and
reports nothing, so catch it here instead of in the scheduler log.
"""

from __future__ import annotations

import pytest
from airflow.models.dagbag import DagBag

DAG_ID = "sharepoint_connection_check"
DAG_FOLDER = "dags"


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=DAG_FOLDER, include_examples=False)


def test_dag_imports_without_errors(dagbag):
    assert dagbag.import_errors == {}


def test_dag_is_registered(dagbag):
    assert DAG_ID in dagbag.dags


def test_dag_reads_config_toml(dagbag):
    """Site and conn_id come from config.toml; hardcoding either defeats the
    per-DAG convention."""
    from utils.dag_config import load_dag_config

    config = load_dag_config(f"{DAG_FOLDER}/{DAG_ID}")

    assert config["sharepoint"]["site_url"]
    assert config["sharepoint"]["sharepoint_conn_id"]


def test_dag_has_no_schedule(dagbag):
    """Manual-trigger only -- it moves no data, so a cadence is just noise."""
    assert dagbag.dags[DAG_ID].schedule is None


def test_tasks_run_in_order(dagbag):
    """check_site_access first: no point listing libraries if the token is refused,
    and the two failures mean different things."""
    dag = dagbag.dags[DAG_ID]

    assert set(dag.task_ids) == {"check_site_access", "list_document_libraries"}
    assert dag.get_task("list_document_libraries").upstream_task_ids == {
        "check_site_access"
    }
