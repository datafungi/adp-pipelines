"""Tests for providers.sharepoint.hooks.sharepoint.SharePointHook."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from providers.sharepoint.hooks.sharepoint import SharePointHook

SITE_URL = "https://contoso.sharepoint.com/sites/TalentAcquisition"
CLIENT_ID = "11111111-1111-1111-1111-111111111111"
TENANT_DOMAIN = "contoso.onmicrosoft.com"
THUMBPRINT = "AA:BB:CC"
EXPECTED_SCOPE = "https://contoso.sharepoint.com/.default"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(encryption=serialization.NoEncryption()) -> str:
    return _KEY.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, encryption
    ).decode()


# The hook validates the key, so these must be real PEMs. ESCAPED is the form the
# connection actually holds -- Airflow's single-line `password` field can't take
# literal newlines.
PRIVATE_KEY = _pem()
ESCAPED_PRIVATE_KEY = PRIVATE_KEY.replace("\n", "\\n")
ENCRYPTED_PRIVATE_KEY = _pem(serialization.BestAvailableEncryption(b"hunter2"))
PUBLIC_KEY = (
    _KEY.public_key()
    .public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    .decode()
)


class FakeConnection:
    """Stands in for an Airflow Connection.

    No `extra_dejson` on purpose: the hook reads native fields only, so an
    AttributeError here is a real failure, not a fixture gap.
    """

    def __init__(
        self,
        login=CLIENT_ID,
        host=TENANT_DOMAIN,
        schema=THUMBPRINT,
        password=ESCAPED_PRIVATE_KEY,
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
    """with_client_secret must never be called: SharePoint refuses non-appidacr=2
    tokens, and an earlier revision regressed onto exactly that path."""
    get_connection = _patch_get_connection(mocker)
    entra_auth_cls, fake_entra_auth, client_context_cls, fake_ctx = _patch_auth_chain(
        mocker
    )

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")
    result = hook.get_conn()

    entra_auth_cls.assert_called_once_with(
        tenant=TENANT_DOMAIN, scopes=[EXPECTED_SCOPE]
    )
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


@pytest.mark.parametrize(
    "stored",
    [
        ESCAPED_PRIVATE_KEY,
        PRIVATE_KEY.replace("\n", "\\r\\n"),
        PRIVATE_KEY,
    ],
    ids=["escaped-lf", "escaped-crlf", "already-real-newlines"],
)
def test_get_conn_unescapes_private_key_newlines(mocker, stored):
    r"""MSAL can't parse a PEM without real line breaks, and the single-line
    `password` field can only receive them escaped. Real newlines pass through."""
    _patch_get_connection(mocker, password=stored)
    _entra_auth_cls, fake_entra_auth, _client_context_cls, _fake_ctx = (
        _patch_auth_chain(mocker)
    )

    SharePointHook(site_url=SITE_URL).get_conn()

    fake_entra_auth.with_certificate.assert_called_once_with(
        CLIENT_ID, THUMBPRINT, PRIVATE_KEY
    )


def test_get_conn_encrypted_private_key_raises(mocker):
    """A passphrase-protected key is a distinct mistake (skipping openssl's -nodes)
    from a malformed one, and the hook passes no passphrase to MSAL."""
    _patch_get_connection(mocker, password=ENCRYPTED_PRIVATE_KEY.replace("\n", "\\n"))
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="encrypted 'private_key'"):
        hook.get_conn()


def test_get_conn_certificate_pasted_as_private_key_raises(mocker):
    """Step 2 of the setup doc produces certificate.pem *and* private-key.pem, so
    pasting the wrong half is an easy mistake -- it must not reach MSAL."""
    _patch_get_connection(mocker, password=PUBLIC_KEY.replace("\n", "\\n"))
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="not a readable PEM private key"):
        hook.get_conn()


def test_get_conn_malformed_private_key_raises(mocker):
    """Without this the failure surfaces from inside MSAL as `InvalidKeyError: Could
    not parse the provided public key`, naming neither the connection nor the cause."""
    _patch_get_connection(mocker, password="-----BEGIN PRIVATE KEY-----\\nnope\\n")
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint_default")

    with pytest.raises(ValueError, match="not a readable PEM private key"):
        hook.get_conn()


def test_get_conn_private_key_error_names_the_connection(mocker):
    """The message must identify which connection is at fault -- a deployment has
    several, and MSAL's own error identifies none of them."""
    _patch_get_connection(mocker, password="garbage")
    _patch_auth_chain(mocker)

    hook = SharePointHook(site_url=SITE_URL, sharepoint_conn_id="sharepoint-prod-conn")

    with pytest.raises(ValueError, match="sharepoint-prod-conn"):
        hook.get_conn()
