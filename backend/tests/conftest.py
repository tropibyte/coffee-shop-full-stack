"""Shared pytest fixtures.

The important idea here is that the tests do not stub out authentication.  A
session-scoped RSA key pair stands in for Auth0's signing key, a matching JWKS
document is served from an intercepted HTTP layer, and tests mint real RS256
tokens against it.  Every request therefore travels the same path a production
request does -- header parsing, JWKS lookup, signature verification, claim
validation, permission check -- and a regression in any of those steps fails a
test rather than passing one.

``responses`` is active for every test and refuses any request that has not
been registered, so a test that accidentally reaches the internet fails loudly
instead of going quiet and slow.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, Iterator, List, Optional

# FLASK_CONFIG has to be in place before src.config is imported, because the
# configuration classes read the environment at class-definition time.
os.environ["FLASK_CONFIG"] = "testing"
os.environ.setdefault("AUTH0_DOMAIN", "coffee-shop-test.us.auth0.com")
os.environ.setdefault("AUTH0_API_AUDIENCE", "coffee-shop-test")

import jwt  # noqa: E402
import pytest  # noqa: E402
import responses as responses_lib  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402
from jwt.algorithms import RSAAlgorithm  # noqa: E402

TEST_DOMAIN = "coffee-shop-test.us.auth0.com"
TEST_AUDIENCE = "coffee-shop-test"
TEST_ISSUER = "https://{0}/".format(TEST_DOMAIN)
JWKS_URL = "https://{0}/.well-known/jwks.json".format(TEST_DOMAIN)
MANAGEMENT_BASE = "https://{0}/api/v2".format(TEST_DOMAIN)
TOKEN_URL = "https://{0}/oauth/token".format(TEST_DOMAIN)

PRIMARY_KID = "primary-signing-key"
ROTATED_KID = "rotated-signing-key"
FOREIGN_KID = "someone-elses-key"

#: The permission sets the Auth0 roles carry.  Kept here so that a change to
#: the RBAC design shows up as a single diff across the whole suite.
BARISTA_PERMISSIONS = ["get:drinks-detail"]
MANAGER_PERMISSIONS = [
    "get:drinks-detail",
    "post:drinks",
    "patch:drinks",
    "delete:drinks",
    "get:users",
    "post:users",
    "patch:users",
    "delete:users",
    "get:roles",
]
ADMIN_PERMISSIONS = MANAGER_PERMISSIONS + ["get:audit"]

ROLES_CLAIM = "https://coffee-shop.api/roles"


# ---------------------------------------------------------------------------
# Signing keys and JWKS
# ---------------------------------------------------------------------------


class SigningKey:
    """An RSA key pair plus the JWK the JWKS endpoint would publish for it."""

    def __init__(self, kid: str) -> None:
        """Generate a fresh 2048-bit key under the given key id."""
        self.kid = kid
        self.private_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048
        )
        self.public_key = self.private_key.public_key()

    def jwk(self) -> Dict[str, Any]:
        """Return this key's public half in JWKS form."""
        document = json.loads(RSAAlgorithm.to_jwk(self.public_key))
        document.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        return document


@pytest.fixture(scope="session")
def primary_key() -> SigningKey:
    """The key Auth0 is currently signing with."""
    return SigningKey(PRIMARY_KID)


@pytest.fixture(scope="session")
def rotated_key() -> SigningKey:
    """A key that appears only after a simulated rotation."""
    return SigningKey(ROTATED_KID)


@pytest.fixture(scope="session")
def foreign_key() -> SigningKey:
    """An attacker's key: correct shape, wrong owner."""
    return SigningKey(FOREIGN_KID)


@pytest.fixture(scope="session")
def jwks_document(primary_key: SigningKey) -> Dict[str, Any]:
    """The JWKS body served for the tenant."""
    return {"keys": [primary_key.jwk()]}


# ---------------------------------------------------------------------------
# HTTP interception
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mocked_responses(jwks_document) -> Iterator[responses_lib.RequestsMock]:
    """Intercept every outbound HTTP call for the duration of a test.

    The JWKS endpoint and the Management API token endpoint are pre-registered
    because almost every test needs them; anything else a test wants must be
    registered by that test, and anything unregistered raises.
    """
    with responses_lib.RequestsMock(assert_all_requests_are_fired=False) as mock:
        mock.add(responses_lib.GET, JWKS_URL, json=jwks_document, status=200)
        mock.add(
            responses_lib.POST,
            TOKEN_URL,
            json={
                "access_token": "management-api-token",
                "expires_in": 86400,
                "token_type": "Bearer",
            },
            status=200,
        )
        yield mock


@pytest.fixture(autouse=True)
def clear_jwks_cache() -> Iterator[None]:
    """Start every test with an empty JWKS cache.

    The cache is process-wide by design.  Letting it leak between tests would
    mean a test that simulates key rotation could change the outcome of one
    that runs after it.
    """
    from src.auth.auth import jwks_cache

    jwks_cache.clear()
    yield
    jwks_cache.clear()


@pytest.fixture(autouse=True)
def clear_role_cache() -> Iterator[None]:
    """Start every test with an empty caller-role cache."""
    from src.management.users_api import _self_roles_cache

    _self_roles_cache.clear()
    yield
    _self_roles_cache.clear()


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def flask_app():
    """Import the application once per session.

    ``src.api`` builds its app at import time, exactly as ``flask run`` sees
    it, so importing it here tests the real wiring rather than a parallel
    construction that only the tests use.
    """
    from src.api import app

    return app


@pytest.fixture
def app(flask_app) -> Iterator[Any]:
    """Provide an application context over a freshly created schema."""
    from src.database.models import db

    with flask_app.app_context():
        db.create_all()
        try:
            yield flask_app
        finally:
            db.session.remove()
            db.drop_all()


@pytest.fixture
def client(app):
    """A test client bound to the prepared application."""
    return app.test_client()


@pytest.fixture
def db_session(app):
    """The active SQLAlchemy session."""
    from src.database.models import db

    return db.session


# ---------------------------------------------------------------------------
# Token minting
# ---------------------------------------------------------------------------


@pytest.fixture
def make_token(primary_key: SigningKey):
    """Return a factory that mints access tokens.

    Every parameter has a valid default, so a test names only the thing it is
    varying.  That keeps the intent of a security test visible: a test for
    audience rejection says ``audience="wrong"`` and nothing else.
    """

    def _make(
        permissions: Optional[List[str]] = None,
        *,
        sub: str = "auth0|test-user",
        audience: Any = TEST_AUDIENCE,
        issuer: str = TEST_ISSUER,
        expires_in: int = 3600,
        issued_at: Optional[int] = None,
        not_before: Optional[int] = None,
        key: Optional[SigningKey] = None,
        kid: Optional[str] = None,
        algorithm: str = "RS256",
        roles: Optional[List[str]] = None,
        include_permissions: bool = True,
        scope: Optional[str] = None,
        extra_claims: Optional[Dict[str, Any]] = None,
        drop_claims: Optional[List[str]] = None,
    ) -> str:
        signing = key or primary_key
        now = int(time.time())
        payload: Dict[str, Any] = {
            "iss": issuer,
            "sub": sub,
            "aud": audience,
            "iat": issued_at if issued_at is not None else now,
            "exp": now + expires_in,
            "azp": "test-client-id",
            "jti": uuid.uuid4().hex,
        }
        if not_before is not None:
            payload["nbf"] = not_before
        if include_permissions:
            payload["permissions"] = list(permissions or [])
        if scope is not None:
            payload["scope"] = scope
        if roles is not None:
            payload[ROLES_CLAIM] = roles
        if extra_claims:
            payload.update(extra_claims)
        for claim in drop_claims or []:
            payload.pop(claim, None)

        headers = {"kid": kid or signing.kid}
        return jwt.encode(
            payload,
            signing.private_key,
            algorithm=algorithm,
            headers=headers,
        )

    return _make


@pytest.fixture
def auth_header(make_token):
    """Return a factory producing a ready-made Authorization header dict."""

    def _header(permissions: Optional[List[str]] = None, **kwargs) -> Dict[str, str]:
        return {"Authorization": "Bearer " + make_token(permissions, **kwargs)}

    return _header


@pytest.fixture
def public_headers() -> Dict[str, str]:
    """No credentials at all."""
    return {}


@pytest.fixture
def barista_headers(auth_header) -> Dict[str, str]:
    """A barista's token: recipes, nothing more."""
    return auth_header(BARISTA_PERMISSIONS, sub="auth0|barista", roles=["Barista"])


@pytest.fixture
def manager_headers(auth_header) -> Dict[str, str]:
    """A manager's token: full menu control, may administer baristas."""
    return auth_header(MANAGER_PERMISSIONS, sub="auth0|manager", roles=["Manager"])


@pytest.fixture
def admin_headers(auth_header) -> Dict[str, str]:
    """An administrator's token: everything, including the audit trail."""
    return auth_header(ADMIN_PERMISSIONS, sub="auth0|admin", roles=["Administrator"])


# ---------------------------------------------------------------------------
# Domain fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def sample_recipe() -> List[Dict[str, Any]]:
    """A well-formed two-ingredient recipe."""
    return [
        {"name": "blue foam", "color": "#2ec4f1", "parts": 1},
        {"name": "espresso", "color": "#4b2e1e", "parts": 2},
    ]


@pytest.fixture
def make_drink(app, sample_recipe):
    """Insert a drink directly, bypassing the API."""
    from src.database.models import Drink

    created: List[Any] = []

    def _make(title: str = "Test Latte", recipe: Optional[List[Dict]] = None):
        drink = Drink(
            title=title,
            recipe=json.dumps(recipe if recipe is not None else sample_recipe),
        )
        drink.insert()
        created.append(drink)
        return drink

    return _make


@pytest.fixture
def existing_drink(make_drink):
    """One drink already on the menu."""
    return make_drink()


# ---------------------------------------------------------------------------
# Auth0 Management API doubles
# ---------------------------------------------------------------------------


def auth0_user(
    user_id: str = "auth0|target",
    email: str = "target@example.com",
    name: str = "Target User",
    blocked: bool = False,
) -> Dict[str, Any]:
    """Build an Auth0 user object of the shape the Management API returns."""
    return {
        "user_id": user_id,
        "email": email,
        "name": name,
        "nickname": name.split()[0].lower(),
        "picture": "https://example.com/avatar.png",
        "blocked": blocked,
        "email_verified": True,
        "logins_count": 3,
        "last_login": "2026-09-01T10:00:00.000Z",
        "created_at": "2026-01-01T10:00:00.000Z",
    }


def auth0_role(name: str) -> Dict[str, Any]:
    """Build an Auth0 role object."""
    return {
        "id": "rol_{0}".format(name.lower()),
        "name": name,
        "description": "{0} role".format(name),
    }


ALL_ROLES = [auth0_role("Barista"), auth0_role("Manager"), auth0_role("Administrator")]
