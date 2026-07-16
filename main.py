"""Manual smoke test: authenticate to a real SharePoint site through SharePointHook.

Drives the actual hook rather than re-implementing its auth, so this exercises
the code path that ships: Entra (MSAL) -> site-scoped token -> ClientContext.

The hook reads an Airflow connection, but no Airflow DB or scheduler is needed:
Connection.get() resolves AIRFLOW_CONN_<CONN_ID> from the environment, so this
builds that connection in-process.

Both auth methods are covered, and they deliberately source their tenant
differently -- mirroring the hook's split: certificate auth passes the tenant
domain via `host`, client-secret auth passes the tenant GUID via
extra.tenant_id.

Requires (via .env):
    SHAREPOINT_CLIENT_ID       -- app registration granted Sites.Selected
    SHAREPOINT_TEST_SITE_URL   -- site the grant was issued for
  certificate auth:
    SHAREPOINT_TENANT_ID, KV_CLIENT_ID, KV_CLIENT_SECRET, KV_URI
                               -- to read the cert out of Key Vault
  client-secret auth:
    SHAREPOINT_CLIENT_SECRET, SHAREPOINT_TENANT_ID

Run with: uv run python main.py [cert|secret]   (default: cert)
"""

import json
import os
import sys
from pathlib import Path

from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent / "plugins"))

from providers.sharepoint.hooks.sharepoint import SharePointHook  # noqa: E402

load_dotenv()

CONN_ID = "sharepoint_default"
TENANT_DOMAIN = os.getenv("SHAREPOINT_TENANT_DOMAIN", "datafungi.onmicrosoft.com")


def get_sharepoint_creds() -> tuple[str, str]:
    creds = ClientSecretCredential(
        tenant_id=os.environ["SHAREPOINT_TENANT_ID"],
        client_id=os.environ["KV_CLIENT_ID"],
        client_secret=os.environ["KV_CLIENT_SECRET"],
        additionally_allowed_tenants="*",
    )

    secret_client = SecretClient(vault_url=os.environ["KV_URI"], credential=creds)
    thumbprint = secret_client.get_secret(name="adp-sharepoint-reader-thumbprint").value
    private_key = secret_client.get_secret(
        name="adp-sharepoint-reader-private-key"
    ).value

    if not thumbprint or not private_key:
        raise RuntimeError("Key Vault returned an empty thumbprint or private key.")
    return thumbprint, private_key


def export_connection(**fields: object) -> None:
    """Publish the connection the hook will read, as AIRFLOW_CONN_<CONN_ID>."""
    os.environ[f"AIRFLOW_CONN_{CONN_ID.upper()}"] = json.dumps(
        {
            "conn_type": "sharepoint",
            "login": os.environ["SHAREPOINT_CLIENT_ID"],
            **fields,
        }
    )


def export_certificate_connection() -> None:
    """extra.private_key selects the certificate branch; tenant comes from `host`."""
    thumbprint, private_key = get_sharepoint_creds()
    export_connection(
        host=TENANT_DOMAIN,
        extra={"thumbprint": thumbprint, "private_key": private_key},
    )


def export_client_secret_connection() -> None:
    """No extra.private_key, so the hook falls through to the client-secret branch."""
    export_connection(
        password=os.environ["SHAREPOINT_CLIENT_SECRET"],
        extra={"tenant_id": os.environ["SHAREPOINT_TENANT_ID"]},
    )


def authenticate_sharepoint(method: str) -> None:
    site_url = os.environ["SHAREPOINT_TEST_SITE_URL"]
    if method == "cert":
        export_certificate_connection()
    elif method == "secret":
        export_client_secret_connection()
    else:
        raise SystemExit(f"Unknown auth method '{method}'. Use 'cert' or 'secret'.")

    hook = SharePointHook(site_url=site_url, sharepoint_conn_id=CONN_ID)
    ctx = hook.get_conn()

    web = ctx.web.get().execute_query()
    print("Auth method:", method)
    print("Token scoped to:", f"{hook._site_resource()}/.default")
    print("Connected to SharePoint Site:", web.properties["Title"])


if __name__ == "__main__":
    authenticate_sharepoint(sys.argv[1] if len(sys.argv) > 1 else "cert")
