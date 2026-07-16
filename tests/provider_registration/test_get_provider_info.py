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


def test_sharepoint_conn_fields_match_hook_extra_keys():
    """conn-fields here and extra_dejson.get(...) calls in the hook must agree on
    key names -- conn-fields is the only thing keeping the intake form in sync
    with what SharePointHook.get_conn() actually reads."""
    from providers.sharepoint.hooks.sharepoint import SharePointHook

    info = get_provider_info()
    sharepoint_entry = next(
        e for e in info["connection-types"] if e["connection-type"] == "sharepoint"
    )

    assert set(sharepoint_entry["conn-fields"]) == {
        "tenant_id",
        "thumbprint",
        "private_key",
    }
    assert sharepoint_entry["hook-class-name"] == (
        f"{SharePointHook.__module__}.{SharePointHook.__qualname__}"
    )


def test_sharepoint_host_relabeled_not_hidden():
    """The native `host` field is repurposed to carry the certificate-auth tenant
    domain -- it must stay visible (not in hidden-fields) and be relabeled so the
    intake form doesn't show it as a bare, unexplained 'Host'."""
    info = get_provider_info()
    sharepoint_entry = next(
        e for e in info["connection-types"] if e["connection-type"] == "sharepoint"
    )
    behaviour = sharepoint_entry["ui-field-behaviour"]

    assert "host" not in behaviour["hidden-fields"]
    assert behaviour["relabeling"]["host"] == "Tenant"
