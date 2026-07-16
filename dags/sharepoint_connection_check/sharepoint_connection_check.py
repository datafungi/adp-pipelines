"""Verifies the SharePoint connection end to end against a real site.

Manual-trigger only: proves SharePointHook can authenticate and read, moves no
data. Also the worked example of the per-DAG config.toml convention.

The tasks are sequenced so failures are distinguishable: check_site_access failing
means the token was refused (401) or unconsented (403); list_document_libraries
failing after it passed means auth is fine but the per-site grant is missing.
"""

from __future__ import annotations

from pathlib import Path

from airflow.sdk import dag, task

from providers.sharepoint.hooks.sharepoint import SharePointHook
from utils.dag_config import load_dag_config

CONFIG = load_dag_config(Path(__file__).parent)
DAG_CONFIG = CONFIG.get("dag", {})
SHAREPOINT_CONFIG = CONFIG["sharepoint"]

# SharePoint list template id for a document library.
DOCUMENT_LIBRARY_TEMPLATE = 101


def _client_context():
    """Fresh authenticated context -- each task is its own process, so the hook's
    cache never crosses between them."""
    return SharePointHook(
        site_url=SHAREPOINT_CONFIG["site_url"],
        sharepoint_conn_id=SHAREPOINT_CONFIG["sharepoint_conn_id"],
    ).get_conn()


@dag(
    dag_id="sharepoint_connection_check",
    dag_display_name=DAG_CONFIG.get("display_name", "SharePoint Connection Check"),
    schedule=DAG_CONFIG.get("schedule"),
    catchup=False,
    tags=["sharepoint", "smoke-test"],
    doc_md=__doc__,
    default_args={"owner": DAG_CONFIG.get("owner", "data-eng")},
)
def sharepoint_connection_check():
    @task
    def check_site_access() -> str:
        """Cheapest call that proves the token was accepted."""
        web = _client_context().web.get().execute_query()
        title = web.properties["Title"]
        print(f"Connected to SharePoint site: {title}")
        return title

    @task
    def list_document_libraries() -> list[str]:
        """Reads content, proving the grant reaches past the site root."""
        libraries = (
            _client_context()
            .web.lists.filter(f"BaseTemplate eq {DOCUMENT_LIBRARY_TEMPLATE}")
            .get()
            .execute_query()
        )
        titles = [library.properties["Title"] for library in libraries]
        print(f"Document libraries: {titles}")
        return titles

    check_site_access() >> list_document_libraries()


sharepoint_connection_check()
