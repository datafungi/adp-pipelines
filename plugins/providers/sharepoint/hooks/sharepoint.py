r"""Airflow hook for SharePoint Online, authenticated as an Entra ID app
registration with app-only permissions scoped via Sites.Selected.

Certificate-only, which is SharePoint's rule rather than a preference: it refuses
app-only tokens whose `appidacr` claim isn't "2" (certificate), so a client secret
401s however it's consented. Legacy ACS is not the way out -- it can't be scoped
with Sites.Selected at all. See docs/providers/sharepoint/service-principal-setup.md.

Connection (conn_type="sharepoint"), native fields only -- nothing in `extra`:
    login    -> application (client) ID
    host     -> tenant domain, e.g. "<tenant>.onmicrosoft.com"
    schema   -> certificate thumbprint
    password -> certificate private key (PEM, newlines escaped as \n)

`password` holds the key because Airflow masks and encrypts that field; the cost is
that it's single-line, hence the escaping. The key must be unencrypted -- no
passphrase is passed to MSAL. Field labels live in
provider_registration/adp_provider_reg/get_provider_info.py.

site_url is not part of the connection: Sites.Selected grants are per site, so it
comes from the DAG folder's config.toml (see plugins/utils/dag_config.py). Tokens
scope to the SharePoint resource, not Graph.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from airflow.sdk.bases.hook import BaseHook
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from office365.runtime.auth.entra.authentication_context import (
    AuthenticationContext as EntraAuthenticationContext,
)
from office365.sharepoint.client_context import ClientContext


def _load_private_key(private_key: str, conn_id: str) -> str:
    r"""Return the PEM with real line breaks, having checked it parses.

    Unescaping is unconditional: Airflow's `password` field is single-line and
    AIRFLOW_CONN_* is JSON, so a PEM can only arrive `\n`-escaped. PEM armour and
    base64 never contain a backslash, so `\n` is never key material.

    Parsing here is purely for the error message. MSAL fails deep inside its client
    assertion with `InvalidKeyError: Could not parse the provided public key`, which
    names neither the connection nor the cause, and is reached only after a token
    request -- so the same message covers a wrong key, an encrypted key, and a
    pasted certificate.
    """
    pem = private_key.replace("\\r\\n", "\n").replace("\\n", "\n")
    try:
        load_pem_private_key(pem.encode(), password=None)
    except TypeError as exc:
        raise ValueError(
            f"Connection '{conn_id}' has an encrypted 'private_key', which this hook "
            "cannot decrypt -- it passes no passphrase to MSAL. Re-issue the key "
            "unencrypted (openssl req ... -nodes)."
        ) from exc
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise ValueError(
            f"Connection '{conn_id}' has a 'private_key' that is not a readable PEM "
            f"private key ({exc}). Check it is the private key (private-key.pem) and "
            "not the certificate, and that its newlines are escaped as \\n."
        ) from exc
    return pem


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
        ).with_certificate(
            conn.login,
            conn.schema,
            _load_private_key(conn.password, self.sharepoint_conn_id),
        )

        self._client_context = ClientContext(self.site_url).with_access_token(
            entra_auth.acquire_token
        )
        return self._client_context

    def _site_resource(self) -> str:
        """Resource identifier (scheme + host) the access token must be scoped to."""
        parts = urlsplit(self.site_url)
        return f"{parts.scheme}://{parts.netloc}"
