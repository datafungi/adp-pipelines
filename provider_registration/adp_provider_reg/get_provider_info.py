"""apache_airflow_provider entry-point target.

Airflow's ProvidersManager only discovers connection types (and renders
them in the Add Connection UI) via the `apache_airflow_provider` entry
point on an *installed* distribution -- a hook class merely sitting on
sys.path (e.g. in plugins/, which is git-synced at runtime and never
pip-installed) is invisible to it.

This package exists solely to be pip-installed at image build time so
that entry point exists. The hook classes it points to still live in
plugins/ and are resolved at import time once that folder is on
sys.path -- this package does not duplicate or vendor them.

`conn-fields`/`ui-field-behaviour` below are read straight off this dict
by ProvidersManager (providers_manager.py's `_load_ui_metadata`) -- no
provider.yaml file is involved, that's just the authoring format real
Apache providers compile into this same shape at build time. Defining
them here instead of as `get_connection_form_widgets`/`get_ui_field_behaviour`
on the hook class itself avoids the AirflowProviderDeprecationWarning
those emit as of Airflow 3.2.

No explicit auth-method switch: SharePointHook.get_conn() picks certificate
auth over client-secret auth whenever extra.private_key is set, so there's
no field here to fall out of sync with which fields are actually filled in.
Each auth method also sources its own tenant identifier rather than sharing
one: client-secret auth uses extra.tenant_id (GUID), certificate auth uses
the native `host` field repurposed to hold the tenant domain name (e.g.
"<tenant>.onmicrosoft.com") -- see SharePointHook's docstring for why they
differ.
"""

from __future__ import annotations


def get_provider_info() -> dict:
    return {
        "package-name": "adp-provider-registration",
        "name": "ADP Pipelines Providers",
        "description": "In-house Airflow providers for ADP pipelines.",
        "connection-types": [
            {
                "hook-class-name": "providers.sharepoint.hooks.sharepoint.SharePointHook",
                "connection-type": "sharepoint",
                "conn-fields": {
                    "tenant_id": {
                        "label": "Tenant ID",
                        "description": "Azure AD (Entra ID) tenant ID. Used for client-secret auth.",
                        "schema": {"type": ["string", "null"]},
                    },
                    "thumbprint": {
                        "label": "Certificate Thumbprint",
                        "description": "Required if Certificate Private Key is set.",
                        "schema": {"type": ["string", "null"], "format": "password"},
                    },
                    "private_key": {
                        "label": "Certificate Private Key (PEM)",
                        "description": "If set, certificate auth is used instead of Client Secret.",
                        "schema": {"type": ["string", "null"], "format": "password"},
                    },
                },
                "ui-field-behaviour": {
                    "hidden-fields": ["schema", "port"],
                    "relabeling": {
                        "login": "Client ID",
                        "password": "Client Secret",
                        "host": "Tenant",
                    },
                    "placeholders": {
                        "host": "<tenant>.onmicrosoft.com -- required if Certificate Private Key is set",
                        "password": "Used unless Certificate Private Key is set",
                        "thumbprint": "Required if Certificate Private Key is set",
                        "private_key": "Takes precedence over Client Secret if set",
                    },
                },
            }
        ],
    }
