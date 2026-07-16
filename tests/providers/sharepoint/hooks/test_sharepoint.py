"""Tests for providers.sharepoint.hooks.sharepoint.SharePointHook."""

from __future__ import annotations

import pytest

from providers.sharepoint.hooks.sharepoint import SharePointHook

SITE_URL = "https://contoso.sharepoint.com/sites/TalentAcquisition"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_SECRET = "super-secret-value"
TENANT_ID = "22222222-2222-2222-2222-222222222222"
TENANT_DOMAIN = "contoso.onmicrosoft.com"
EXPECTED_SCOPE = "https://contoso.sharepoint.com/.default"


class FakeConnection:
    def __init__(self, login, password, extra_dejson, host=None):
        self.login = login
        self.password = password
        self.extra_dejson = extra_dejson
        self.host = host


def _patch_get_connection(mocker, extra_dejson=None, host=None):
    conn = FakeConnection(
        login=CLIENT_ID,
        password=CLIENT_SECRET,
        extra_dejson=extra_dejson
        if extra_dejson is not None
        else {"tenant_id": TENANT_ID},
        host=host,
    )
    return mocker.patch.object(SharePointHook, "get_connection", return_value=conn)


def _patch_auth_chain(mocker):
    """Patch the Entra AuthenticationContext + ClientContext bridge and return both mocks."""
    fake_entra_auth = mocker.Mock()
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


def test_get_conn_authenticates_via_entra_client_secret(mocker):
    get_connection = _patch_get_connection(mocker)
    entra_auth_cls, fake_entra_auth, client_context_cls, fake_ctx = _patch_auth_chain(
        mocker
    )

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")
    result = hook.get_conn()

    entra_auth_cls.assert_called_once_with(tenant=TENANT_ID, scopes=[EXPECTED_SCOPE])
    fake_entra_auth.with_client_secret.assert_called_once_with(CLIENT_ID, CLIENT_SECRET)
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


def test_get_conn_missing_tenant_id_raises(mocker):
    _patch_get_connection(mocker, extra_dejson={})
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="tenant_id"):
        hook.get_conn()


@pytest.mark.parametrize("login", [None, ""])
def test_get_conn_missing_login_raises(mocker, login):
    conn = FakeConnection(
        login=login, password=CLIENT_SECRET, extra_dejson={"tenant_id": TENANT_ID}
    )
    mocker.patch.object(SharePointHook, "get_connection", return_value=conn)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="client_id"):
        hook.get_conn()


def test_get_conn_missing_password_for_client_secret_raises(mocker):
    conn = FakeConnection(
        login=CLIENT_ID, password=None, extra_dejson={"tenant_id": TENANT_ID}
    )
    mocker.patch.object(SharePointHook, "get_connection", return_value=conn)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="client_secret"):
        hook.get_conn()


def test_get_conn_authenticates_via_certificate(mocker):
    """Certificate auth (triggered by extra.private_key) sources its tenant from the
    native `host` field, not extra.tenant_id -- the two auth paths don't share one."""
    thumbprint = "AA:BB:CC"
    private_key = "-----BEGIN PRIVATE KEY-----..."
    conn = FakeConnection(
        login=CLIENT_ID,
        password=None,
        extra_dejson={"thumbprint": thumbprint, "private_key": private_key},
        host=TENANT_DOMAIN,
    )
    mocker.patch.object(SharePointHook, "get_connection", return_value=conn)
    entra_auth_cls, fake_entra_auth, client_context_cls, fake_ctx = _patch_auth_chain(
        mocker
    )
    fake_entra_auth.with_certificate.return_value = fake_entra_auth

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")
    result = hook.get_conn()

    entra_auth_cls.assert_called_once_with(tenant=TENANT_DOMAIN, scopes=[EXPECTED_SCOPE])
    fake_entra_auth.with_certificate.assert_called_once_with(
        CLIENT_ID, thumbprint, private_key
    )
    fake_entra_auth.with_client_secret.assert_not_called()
    client_context_cls.assert_called_once_with(SITE_URL)
    fake_ctx.with_access_token.assert_called_once_with(fake_entra_auth.acquire_token)
    assert result is fake_ctx


def test_get_conn_certificate_missing_thumbprint_raises(mocker):
    conn = FakeConnection(
        login=CLIENT_ID,
        password=None,
        extra_dejson={"private_key": "-----BEGIN PRIVATE KEY-----..."},
        host=TENANT_DOMAIN,
    )
    mocker.patch.object(SharePointHook, "get_connection", return_value=conn)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="thumbprint"):
        hook.get_conn()


def test_get_conn_certificate_missing_host_raises(mocker):
    conn = FakeConnection(
        login=CLIENT_ID,
        password=None,
        extra_dejson={"thumbprint": "AA:BB:CC", "private_key": "-----BEGIN..."},
        host=None,
    )
    mocker.patch.object(SharePointHook, "get_connection", return_value=conn)
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="'tenant'"):
        hook.get_conn()


def test_get_conn_ignores_thumbprint_when_private_key_absent(mocker):
    """A leftover/empty 'thumbprint' with no 'private_key' must not trip certificate
    auth -- private_key alone is the switch."""
    get_connection = _patch_get_connection(
        mocker, extra_dejson={"tenant_id": TENANT_ID, "thumbprint": "AA:BB:CC"}
    )
    entra_auth_cls, fake_entra_auth, _client_context_cls, _fake_ctx = _patch_auth_chain(
        mocker
    )

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")
    hook.get_conn()

    entra_auth_cls.assert_called_once_with(tenant=TENANT_ID, scopes=[EXPECTED_SCOPE])
    fake_entra_auth.with_client_secret.assert_called_once_with(CLIENT_ID, CLIENT_SECRET)
    fake_entra_auth.with_certificate.assert_not_called()
    get_connection.assert_called_once_with("sharepoint_default")
