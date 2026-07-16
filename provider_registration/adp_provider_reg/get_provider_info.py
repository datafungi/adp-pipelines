"""apache_airflow_provider entry-point target.

ProvidersManager only discovers connection types via the `apache_airflow_provider`
entry point on an *installed* distribution -- a hook class merely on sys.path (as
plugins/ is, being git-synced rather than pip-installed) is invisible to it. This
package exists to be pip-installed at image build so that entry point exists; the
hooks themselves stay in plugins/.

`ui-field-behaviour` is read straight off this dict by ProvidersManager
(`_load_ui_metadata`). Defining it here rather than as `get_ui_field_behaviour` on
the hook avoids the AirflowProviderDeprecationWarning that emits as of Airflow 3.2.

No `conn-fields`: all four values map onto native Connection fields, relabeled
below to say what they hold. `schema` carries the thumbprint (public, so unmasked
costs nothing) and `password` the private key (masked and encrypted, unlike a
custom field). `extra` is hidden because nothing reads it.
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
                "ui-field-behaviour": {
                    "hidden-fields": ["port", "extra"],
                    "relabeling": {
                        "login": "Client ID",
                        "host": "Tenant",
                        "schema": "Certificate Thumbprint",
                        "password": "Certificate Private Key (PEM)",
                    },
                    "placeholders": {
                        "host": "<tenant>.onmicrosoft.com",
                        "schema": "Thumbprint shown after uploading the certificate",
                        "password": "-----BEGIN PRIVATE KEY-----",
                    },
                },
            }
        ],
    }
