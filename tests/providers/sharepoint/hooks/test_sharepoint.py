"""Tests for providers.sharepoint.hooks.sharepoint.SharePointHook."""

from __future__ import annotations

import pytest

from providers.sharepoint.hooks.sharepoint import SharePointHook

SITE_URL = "https://contoso.sharepoint.com/sites/TalentAcquisition"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"
TENANT_DOMAIN = "contoso.onmicrosoft.com"
THUMBPRINT = "AA:BB:CC"
PRIVATE_KEY = "-----BEGIN PRIVATE KEY-----..."
EXPECTED_SCOPE = "https://contoso.sharepoint.com/.default"


class FakeConnection:
    """Stands in for an Airflow Connection.

    Deliberately has no `extra_dejson`: the hook reads native fields only, so an
    AttributeError here is a real failure rather than a fixture gap.
    """

    def __init__(
        self,
        login=CLIENT_ID,
        host=TENANT_DOMAIN,
        schema=THUMBPRINT,
        password=PRIVATE_KEY,
    ):
        self.login = login
        self.host = host
        self.schema = schema
        self.password = password


def _patch_get_connection(mocker, **kwargs):
    conn = FakeConnection(**kwargs)
    return mocker.patch.object(SharePointHook, "get_connection", return_value=conn)


def _patch_auth_chain(mocker):
    """Patch the Entra AuthenticationContext + ClientContext bridge and return both mocks."""
    fake_entra_auth = mocker.Mock()
    fake_entra_auth.with_certificate.return_value = fake_entra_auth
    fake_entra_auth.with_client_secret.return_value = fake_entra_auth
    entra_auth_cls = mocker.patch(
        "providers.sharepoint.hooks.sharepoint.EntraAuthenticationContext",
        return_value=fake_entra_auth,
    )

    fake_ctx = mocker.Mock()
    fake_ctx.with_access_token.return_value = fake_ctx
    client_context_cls = mocker.patch(
        "providers.sharepoint.hooks.sharepoint.ClientContext", return_value=fake_ctx
    )

    return entra_auth_cls, fake_entra_auth, client_context_cls, fake_ctx


def test_hook_class_metadata():
    assert SharePointHook.conn_type == "sharepoint"
    assert SharePointHook.hook_name == "SharePoint"
    assert SharePointHook.default_conn_name == "sharepoint_default"
    assert SharePointHook.conn_name_attr == "sharepoint_conn_id"


def test_get_conn_authenticates_via_certificate(mocker):
    """`password` holds the private key, never a client secret -- SharePoint refuses
    app-only tokens that aren't appidacr=2, and an earlier revision regressed onto
    exactly that path, hence the with_client_secret assertion."""
    get_connection = _patch_get_connection(mocker)
    entra_auth_cls, fake_entra_auth, client_context_cls, fake_ctx = _patch_auth_chain(
        mocker
    )

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")
    result = hook.get_conn()

    entra_auth_cls.assert_called_once_with(tenant=TENANT_DOMAIN, scopes=[EXPECTED_SCOPE])
    fake_entra_auth.with_certificate.assert_called_once_with(
        CLIENT_ID, THUMBPRINT, PRIVATE_KEY
    )
    fake_entra_auth.with_client_secret.assert_not_called()
    client_context_cls.assert_called_once_with(SITE_URL)
    fake_ctx.with_access_token.assert_called_once_with(fake_entra_auth.acquire_token)
    get_connection.assert_called_once_with("sharepoint_default")
    assert result is fake_ctx


def test_get_conn_caches_client_context(mocker):
    _patch_get_connection(mocker)
    _entra_auth_cls, _fake_entra_auth, client_context_cls, _fake_ctx = (
        _patch_auth_chain(mocker)
    )

    hook = SharePointHook(site_url=SITE_URL)
    first = hook.get_conn()
    second = hook.get_conn()

    assert first is second
    client_context_cls.assert_called_once()


@pytest.mark.parametrize("login", [None, ""])
def test_get_conn_missing_login_raises(mocker, login):
    _patch_get_connection(mocker, login=login)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="client_id"):
        hook.get_conn()


@pytest.mark.parametrize("host", [None, ""])
def test_get_conn_missing_host_raises(mocker, host):
    _patch_get_connection(mocker, host=host)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="'tenant'"):
        hook.get_conn()


@pytest.mark.parametrize("schema", [None, ""])
def test_get_conn_missing_schema_raises(mocker, schema):
    _patch_get_connection(mocker, schema=schema)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="thumbprint"):
        hook.get_conn()


@pytest.mark.parametrize("password", [None, ""])
def test_get_conn_missing_password_raises(mocker, password):
    _patch_get_connection(mocker, password=password)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="private_key"):
        hook.get_conn()
