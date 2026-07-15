"""Airflow hook for SharePoint Online, authenticated as an Azure AD (Entra ID)
app registration with a client secret and app-only Microsoft Graph permissions
scoped via Sites.Selected.

Connection (conn_type="sharepoint"):
    login    -> Azure AD application (client) ID
    password -> client secret
    extra    -> {"tenant_id": "<azure-ad-tenant-id>"}

The target SharePoint site URL is intentionally not part of the connection:
it varies per DAG and is expected to come from that DAG folder's
config.yaml (see plugins/utils/dag_config.py), since Sites.Selected grants
are issued per site anyway.

Auth is bridged through office365-rest-python-client's Entra (Azure AD)
MSAL-backed AuthenticationContext rather than ClientContext's own
`with_client_credentials` shortcut: that shortcut authenticates via the
legacy ACS app-only model (see office365.runtime.auth.providers.
acs_token_provider.ACSTokenProvider), which Microsoft Graph app permissions
like Sites.Selected cannot be scoped against.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from airflow.sdk.bases.hook import BaseHook
from office365.runtime.auth.entra.authentication_context import (
    AuthenticationContext as EntraAuthenticationContext,
)
from office365.sharepoint.client_context import ClientContext


class SharePointHook(BaseHook):
    """Hook for authenticating to a SharePoint Online site."""

    conn_name_attr = "sharepoint_conn_id"
    default_conn_name = "sharepoint_default"
    conn_type = "sharepoint"
    hook_name = "SharePoint"

    def __init__(
        self, site_url: str, sharepoint_conn_id: str = default_conn_name
    ) -> None:
        super().__init__()
        self.site_url = site_url
        self.sharepoint_conn_id = sharepoint_conn_id
        self._client_context: ClientContext | None = None

    def get_conn(self) -> ClientContext:
        """Return an authenticated ClientContext for `self.site_url`, caching it."""
        if self._client_context is not None:
            return self._client_context

        conn = self.get_connection(self.sharepoint_conn_id)
        tenant_id = conn.extra_dejson.get("tenant_id")
        if not tenant_id:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing required extra field 'tenant_id'."
            )
        if not conn.login or not conn.password:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing 'login' (client ID) "
                "and/or 'password' (client secret)."
            )

        entra_auth = EntraAuthenticationContext(
            tenant=tenant_id, scopes=[f"{self._site_resource()}/.default"]
        ).with_client_secret(conn.login, conn.password)

        self._client_context = ClientContext(self.site_url).with_access_token(
            entra_auth.acquire_token
        )
        return self._client_context

    def _site_resource(self) -> str:
        """Resource identifier (scheme + host) the access token must be scoped to."""
        parts = urlsplit(self.site_url)
        return f"{parts.scheme}://{parts.netloc}"

    @classmethod
    def get_ui_field_behaviour(cls) -> dict[str, Any]:
        return {
            "hidden_fields": ["host", "schema", "port"],
            "relabeling": {
                "login": "Client ID",
                "password": "Client Secret",
            },
            "placeholders": {
                "extra": '{"tenant_id": "<azure-ad-tenant-id>"}',
            },
        }
