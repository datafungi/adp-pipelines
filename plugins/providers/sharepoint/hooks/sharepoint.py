"""Airflow hook for SharePoint Online, authenticated as an Azure AD (Entra ID)
app registration with app-only permissions scoped via Sites.Selected.
Supports two auth methods: certificate takes precedence over client secret
whenever extra.private_key is set, no explicit switch needed. Each method
sources its own tenant identifier (see below) rather than sharing one, so a
connection only needs to fill in the fields its chosen method actually uses.

Connection (conn_type="sharepoint"):
    login    -> Azure AD application (client) ID
    host     -> tenant domain name, e.g. "<tenant>.onmicrosoft.com"
                (certificate auth only)
    password -> client secret (used unless extra.private_key is set)
    extra    -> {
        "tenant_id": "<azure-ad-tenant-id>",  # client-secret auth only
        # for certificate auth (takes precedence if private_key is set):
        "thumbprint": "<certificate-thumbprint>",
        "private_key": "<certificate-private-key-pem>",
    }

UI-facing field metadata (conn-fields / ui-field-behaviour) lives in
provider_registration/adp_provider_reg/get_provider_info.py, not here --
see that module's docstring for why.

The target SharePoint site URL is intentionally not part of the connection:
it varies per DAG and is expected to come from that DAG folder's
config.toml (see plugins/utils/dag_config.py), since Sites.Selected grants
are issued per site anyway.

Both auth methods acquire their token through office365-rest-python-client's
Entra (Azure AD) MSAL-backed AuthenticationContext, which is then handed to
ClientContext as a token callback. Notably this means *not* reaching for
ClientContext.with_client_credentials (nor the with_credentials(
ClientCredential(...)) call it delegates to): those authenticate via the
legacy ACS app-only model (see office365.runtime.auth.providers.
acs_token_provider.ACSTokenProvider), which app permissions like
Sites.Selected cannot be scoped against, and which Microsoft has retired for
new tenants. ClientContext.with_client_certificate would be safe on that
count -- it is MSAL-backed -- but routing both methods through one
AuthenticationContext keeps scope derivation and token acquisition identical
across them.

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

        scopes = [f"{self._site_resource()}/.default"]
        private_key = conn.extra_dejson.get("private_key")

        if private_key:
            self.log.debug("Private key provided, using certificate authentication.")
            thumbprint = conn.extra_dejson.get("thumbprint")
            if not thumbprint:
                raise ValueError(
                    f"Connection '{self.sharepoint_conn_id}' has 'private_key' set but is "
                    "missing 'thumbprint'"
                )
            if not conn.host:
                raise ValueError(
                    f"Connection '{self.sharepoint_conn_id}' has 'private_key' set but is "
                    "missing 'tenant'"
                )
            entra_auth = EntraAuthenticationContext(
                tenant=conn.host, scopes=scopes
            ).with_certificate(conn.login, thumbprint, private_key)
        else:
            self.log.debug(
                "No private key provided, using client secret authentication."
            )
            tenant_id = conn.extra_dejson.get("tenant_id")
            if not tenant_id:
                raise ValueError(
                    f"Connection '{self.sharepoint_conn_id}' is missing 'tenant_id'."
                )
            if not conn.password:
                raise ValueError(
                    f"Connection '{self.sharepoint_conn_id}' is missing 'client_secret'"
                )
            entra_auth = EntraAuthenticationContext(
                tenant=tenant_id, scopes=scopes
            ).with_client_secret(conn.login, conn.password)

        self._client_context = ClientContext(self.site_url).with_access_token(
            entra_auth.acquire_token
        )
        return self._client_context

    def _site_resource(self) -> str:
        """Resource identifier (scheme + host) the access token must be scoped to."""
        parts = urlsplit(self.site_url)
        return f"{parts.scheme}://{parts.netloc}"
