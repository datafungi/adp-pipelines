"""Live checks for SharePointHook -- real Key Vault, real site.

Auth and one read. Everything deterministic belongs to the unit tier
(test_sharepoint.py); see docs/testing.md.
"""

from __future__ import annotations

import os

import pytest

from providers.sharepoint.hooks.sharepoint import SharePointHook

pytestmark = pytest.mark.integration

# The connection the DAGs resolve (dags/*/config.toml), stored in Key Vault as
# airflow-connections-sharepoint-default-conn.
CONN_ID = "sharepoint-default-conn"


@pytest.fixture(scope="session")
def site_url(live_key_vault) -> str:
    """Takes live_key_vault so .env is loaded before this reads it."""
    url = os.getenv("SHAREPOINT_TEST_SITE_URL")
    if not url:
        pytest.skip("SHAREPOINT_TEST_SITE_URL not set (see .env.example)")
    return url


def test_authenticates_and_reads_the_site(site_url):
    """A token proves only that Entra issued one. Reading the site title proves the
    Sites.Selected grant reaches content -- without that read, a missing grant surfaces
    later, at the first real task, looking like a permissions bug."""
    hook = SharePointHook(site_url=site_url, sharepoint_conn_id=CONN_ID)

    web = hook.get_conn().web.get().execute_query()

    assert web.properties["Title"]
