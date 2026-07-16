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

`ui-field-behaviour` below is read straight off this dict by ProvidersManager
(providers_manager.py's `_load_ui_metadata`) -- no provider.yaml file is
involved, that's just the authoring format real Apache providers compile into
this same shape at build time. Defining it here instead of as
`get_connection_form_widgets`/`get_ui_field_behaviour` on the hook class itself
avoids the AirflowProviderDeprecationWarning those emit as of Airflow 3.2.

There are no `conn-fields`, and so nothing in `extra`: all four values the hook
needs map onto native Connection fields, relabeled to say what they hold here.
`login` and `host` are near-literal. The other two are repurposed:

  schema   -> certificate thumbprint. A free native string field; the thumbprint
              is a public identifier, so it not being masked costs nothing.
  password -> certificate private key (PEM). Not a client secret: SharePoint's
              REST API rejects app-only tokens issued against one (see
              SharePointHook's docstring for the `appidacr` detail), so the hook
              has no secret to store. Putting the key here rather than in a
              custom extra field means Airflow renders it masked instead of as
              cleartext PEM in the form.

`extra` is hidden because nothing reads it, and there is no auth-method switch
because there is one auth method.
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
