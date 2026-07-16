"""Airflow hook for SharePoint Online, authenticated as an Azure AD (Entra ID)
app registration with app-only permissions scoped via Sites.Selected.

Authentication is certificate-only. That is a SharePoint constraint, not a
preference: Entra stamps every app-only token with an `appidacr` claim recording
how the app proved its identity -- "1" for a client secret, "2" for a
certificate -- and the SharePoint REST API refuses any app-only token that isn't
`appidacr=2`, answering 401 "Unsupported app only token". Entra issues the
client-secret token perfectly happily, with the right audience and the
Sites.Selected role present, so the rejection surfaces only at the API and looks
like a permissions problem it isn't. Client secrets do authenticate app-only
against SharePoint under the legacy ACS model (see office365.runtime.auth.
providers.acs_token_provider.ACSTokenProvider, reachable via ClientContext.
with_client_credentials or with_credentials(ClientCredential(...))), but ACS is
retired for new tenants and cannot be scoped with Sites.Selected at all, so it
is not a way out of this -- reaching for it trades a 401 for a silent
blast-radius increase. Microsoft Graph does accept `appidacr=1`; a future
Graph-based hook could take a secret, but this one talks to SharePoint REST.

Connection (conn_type="sharepoint"), native fields only -- no `extra` needed:
    login    -> Azure AD application (client) ID
    host     -> tenant domain name, e.g. "<tenant>.onmicrosoft.com"
    schema   -> certificate thumbprint
    password -> certificate private key (PEM)

`password` carries the private key rather than a client secret, which never
authenticates here. It is the right slot for it regardless: Airflow renders it
masked, where a custom extra field would show the key in cleartext.

UI-facing field metadata (ui-field-behaviour) lives in
provider_registration/adp_provider_reg/get_provider_info.py, not here -- see
that module's docstring for why.

The target SharePoint site URL is intentionally not part of the connection:
it varies per DAG and is expected to come from that DAG folder's
config.toml (see plugins/utils/dag_config.py), since Sites.Selected grants
are issued per site anyway.

The token is acquired through office365-rest-python-client's Entra (Azure AD)
MSAL-backed AuthenticationContext and handed to ClientContext as a token
callback. ClientContext.with_client_certificate would also be MSAL-backed and
safe on the ACS count, but going through AuthenticationContext directly keeps
scope derivation explicit.

Tokens are scoped to the SharePoint resource (e.g.
"https://<tenant>.sharepoint.com/.default"), not Microsoft Graph, because
ClientContext talks to the SharePoint REST API. The Sites.Selected grant the
app registration needs is therefore the SharePoint one; per-site access is
still administered through Graph's sites/{id}/permissions.
"""

from __future__ import annotations

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
        if not conn.login:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing 'client_id'."
            )
        if not conn.host:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing 'tenant'."
            )

        if not conn.schema:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing 'thumbprint'."
            )
        if not conn.password:
            raise ValueError(
                f"Connection '{self.sharepoint_conn_id}' is missing 'private_key'."
            )

        scopes = [f"{self._site_resource()}/.default"]
        self.log.debug("Acquiring SharePoint token for scope: %s", scopes[0])

        entra_auth = EntraAuthenticationContext(
            tenant=conn.host, scopes=scopes
        ).with_certificate(conn.login, conn.schema, conn.password)

        self._client_context = ClientContext(self.site_url).with_access_token(
            entra_auth.acquire_token
        )
        return self._client_context

    def _site_resource(self) -> str:
        """Resource identifier (scheme + host) the access token must be scoped to."""
        parts = urlsplit(self.site_url)
        return f"{parts.scheme}://{parts.netloc}"
