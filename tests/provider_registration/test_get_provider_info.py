from __future__ import annotations

import importlib
import json
from pathlib import Path

import airflow
import jsonschema

from adp_provider_reg.get_provider_info import get_provider_info


def test_hook_class_names_are_importable():
    """hook-class-name is a bare string Airflow imports lazily -- a typo or a
    plugins/ rename wouldn't be caught until someone opens the connection UI."""
    info = get_provider_info()

    for entry in info["connection-types"]:
        module_path, class_name = entry["hook-class-name"].rsplit(".", 1)
        module = importlib.import_module(module_path)
        assert hasattr(module, class_name)


def test_connection_types_match_hook_conn_type():
    info = get_provider_info()

    for entry in info["connection-types"]:
        module_path, class_name = entry["hook-class-name"].rsplit(".", 1)
        hook_class = getattr(importlib.import_module(module_path), class_name)
        assert hook_class.conn_type == entry["connection-type"]


def test_matches_airflow_provider_info_schema():
    """ProvidersManager validates get_provider_info()'s return value against this
    schema at discovery time (providers_discovery.py) -- a violation here would
    otherwise only surface as a silent registration failure at webserver startup."""
    schema_path = Path(airflow.__file__).parent / "provider_info.schema.json"
    schema = json.loads(schema_path.read_text())

    jsonschema.validate(get_provider_info(), schema)


def _sharepoint_entry():
    info = get_provider_info()
    return next(
        e for e in info["connection-types"] if e["connection-type"] == "sharepoint"
    )


def test_sharepoint_hook_class_name_matches():
    from providers.sharepoint.hooks.sharepoint import SharePointHook

    assert _sharepoint_entry()["hook-class-name"] == (
        f"{SharePointHook.__module__}.{SharePointHook.__qualname__}"
    )


def test_sharepoint_declares_no_extra_fields():
    """The hook reads native Connection fields only. A conn-field here would add an
    `extra` key nothing consumes -- and for the private key specifically, would
    render it as cleartext PEM instead of using the masked `password` slot."""
    assert "conn-fields" not in _sharepoint_entry()


def test_sharepoint_repurposed_fields_relabeled_not_hidden():
    """Four native fields are repurposed to carry Entra certificate credentials, two
    of them (`schema`, `password`) holding something their stock label actively
    misdescribes. Each must stay visible and be relabeled, or the intake form asks
    for a bare 'Schema' and a 'Password' that is neither."""
    behaviour = _sharepoint_entry()["ui-field-behaviour"]

    assert behaviour["relabeling"] == {
        "login": "Client ID",
        "host": "Tenant",
        "schema": "Certificate Thumbprint",
        "password": "Certificate Private Key (PEM)",
    }
    assert not set(behaviour["relabeling"]) & set(behaviour["hidden-fields"])
