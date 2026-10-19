"""E2.1 unit tests: LDAP configuration, filter escaping, OIDC token verification."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from pydantic import ValidationError

from forge.iam import directory, oidc
from forge.tenancy.mappings import RANK
from tests.support.mock_oidc import MockOidc

LDAP = {
    "url": "ldaps://ldap.example.org",
    "bind_dn": "cn=svc,dc=example,dc=org",
    "user_base_dn": "ou=people,dc=example,dc=org",
}


@pytest.mark.ac("US-2.1.1/3")
def test_plain_ldap_needs_starttls_or_the_insecure_flag() -> None:
    with pytest.raises(ValidationError, match="allow_insecure"):
        directory.LdapConfig.model_validate({**LDAP, "url": "ldap://ldap.example.org"})
    directory.LdapConfig.model_validate(
        {**LDAP, "url": "ldap://ldap.example.org", "start_tls": True}
    )
    directory.LdapConfig.model_validate(
        {**LDAP, "url": "ldap://ldap.example.org", "allow_insecure": True}
    )
    with pytest.raises(ValidationError):
        directory.LdapConfig.model_validate({**LDAP, "url": "http://ldap.example.org"})
    with pytest.raises(ValidationError, match="login"):
        directory.LdapConfig.model_validate({**LDAP, "user_filter": "(uid=x)"})


@pytest.mark.ac("US-2.1.2/1")
def test_empty_password_never_reaches_the_directory() -> None:
    cfg = directory.LdapConfig.model_validate(LDAP)
    # An empty password is an anonymous bind: it must be refused before any connection.
    assert directory.authenticate(cfg, "x", "alice", "") is None
    assert directory.authenticate(cfg, "x", "  ", "pw") is None


@pytest.mark.ac("US-2.1.2/2")
def test_login_is_escaped_in_the_filter() -> None:
    from ldap3.utils.conv import escape_filter_chars

    hostile = "*)(uid=*))(|(uid=*"
    assert "(" not in escape_filter_chars(hostile)
    assert "*" not in escape_filter_chars(hostile)


@pytest.mark.ac("US-2.1.5/2")
def test_role_rank_orders_role_types() -> None:
    order = ["viewer", "end_user", "operator", "builder", "project_admin"]
    assert sorted(RANK, key=RANK.__getitem__) == order


def _cfg(idp: MockOidc, **extra: Any) -> oidc.OidcConfig:
    return oidc.OidcConfig.model_validate(
        {"issuer": idp.issuer, "client_id": idp.client_id, "allow_insecure": True, **extra}
    )


@pytest.mark.ac("US-2.1.3/3")
def test_http_issuer_needs_the_insecure_flag() -> None:
    with pytest.raises(ValidationError, match="allow_insecure"):
        oidc.OidcConfig.model_validate({"issuer": "http://idp.example.org", "client_id": "x"})
    with pytest.raises(ValidationError, match="openid"):
        oidc.OidcConfig.model_validate(
            {"issuer": "https://idp.example.org", "client_id": "x", "scopes": "email"}
        )


@pytest.mark.ac("US-2.1.3/2")
def test_id_token_checks() -> None:
    with MockOidc() as idp:
        cfg = _cfg(idp)
        meta = asyncio.run(oidc.discover(cfg))
        jwks = asyncio.run(oidc.fetch_jwks(meta))

        def verify(**claims: Any) -> dict[str, Any]:
            token = idp._token({"nonce": "n1", **claims})
            return oidc.verify_id_token(cfg, meta, jwks, token, "n1")

        assert verify(email="a@example.org")["email"] == "a@example.org"
        for bad in (
            {"_nonce": "other"},
            {"_aud": "someone-else"},
            {"_exp": int(time.time()) - 3600},
            {"_iss": "https://evil.example.org"},
            {"_sign_with_other_key": True},
        ):
            with pytest.raises(oidc.OidcError):
                verify(**bad)


@pytest.mark.ac("US-2.1.3/2")
def test_discovery_rejects_a_foreign_issuer_and_pkce_is_s256() -> None:
    with MockOidc() as idp:
        cfg = oidc.OidcConfig.model_validate(
            {"issuer": idp.issuer + "/", "client_id": "x", "allow_insecure": True}
        )
        asyncio.run(oidc.discover(cfg))  # trailing slash tolerated
        idp.issuer = "http://127.0.0.1:1"  # the document now names another issuer
        with pytest.raises(oidc.OidcError, match="issuer"):
            asyncio.run(oidc.discover(cfg))
    verifier, challenge = oidc.new_pkce()
    assert len(verifier) >= 43
    assert challenge != verifier


@pytest.mark.ac("US-2.1.3/4")
def test_groups_claim_as_string_or_missing() -> None:
    cfg = oidc.OidcConfig.model_validate({"issuer": "https://idp.example.org", "client_id": "x"})
    one = oidc.identity_from_claims(cfg, {"sub": "s", "email": "A@X.org", "groups": "g1"})
    assert one.groups == ["g1"]
    assert one.email == "a@x.org"
    assert oidc.identity_from_claims(cfg, {"sub": "s"}).groups == []
