"""Auth0 bearer-token authentication and RBAC authorisation.

The module implements the four pieces the project specification calls for --
:func:`get_token_auth_header`, :func:`check_permissions`,
:func:`verify_decode_jwt` and the :func:`requires_auth` decorator -- and then
hardens each of them:

* **Algorithms are allow-listed, never negotiated.**  The ``alg`` header of an
  incoming token is checked against configuration before a key is even looked
  up.  Honouring the algorithm a token asks for is how ``alg: none`` and
  RS256-to-HS256 confusion attacks succeed.
* **JWKS responses are cached and refreshed on rotation.**  A signing key is
  fetched once per TTL, and an unrecognised ``kid`` triggers at most one
  rate-limited refresh, so an Auth0 key rotation heals itself without turning
  unknown-kid tokens into an outbound request amplifier.
* **401 and 403 mean different things.**  A caller who cannot prove who they
  are gets 401; a caller who is authenticated but lacks the permission gets
  403.  Collapsing the two hides misconfiguration and breaks RBAC testing.
* **Every claim that matters is required, not merely checked when present.**
  ``exp``, ``iat``, ``iss``, ``aud`` and ``sub`` must all be there.

A note on the library choice: the starter code used ``python-jose``.  This
implementation uses ``PyJWT`` with ``cryptography`` instead -- the rationale,
including the python-jose advisories, is in ``docs/DEPENDENCIES.md``.
"""

from __future__ import annotations

import json
import os
import threading
import time
from functools import wraps
from typing import Any, Dict, List, Optional, Sequence

import jwt
import requests
from flask import current_app, g, request
from jwt.algorithms import RSAAlgorithm

# ---------------------------------------------------------------------------
# Auth0 configuration
# ---------------------------------------------------------------------------
#
# These module-level values are the defaults the decorator falls back to.  When
# the code runs inside a Flask application they are overridden by the matching
# entries in ``app.config`` (see :func:`_setting`), which is how the test suite
# points verification at a locally generated key pair and how Azure App Service
# injects real values without a redeploy.  Nothing secret is ever hard-coded:
# populate these through the environment or a .env file, never by editing this
# module.  See docs/AUTH0_SETUP.md.

AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
ALGORITHMS = [
    algorithm.strip()
    for algorithm in os.environ.get("AUTH0_ALGORITHMS", "RS256").split(",")
    if algorithm.strip()
]
API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "")
AUTH0_CLIENT_ID = os.environ.get("AUTH0_CLIENT_ID", "")

# Claims the API refuses to do without.  `permissions` is intentionally absent:
# a token with no permissions is a valid token that simply cannot do much, and
# that distinction is what produces a 403 rather than a 401.
REQUIRED_CLAIMS = ("exp", "iat", "iss", "aud", "sub")

# Every permission the API recognises.  Used to reject a typo in a decorator at
# import time instead of at 3am in production.
KNOWN_PERMISSIONS = frozenset(
    {
        "get:drinks-detail",
        "post:drinks",
        "patch:drinks",
        "delete:drinks",
        "get:users",
        "post:users",
        "patch:users",
        "delete:users",
        "get:roles",
        "get:audit",
    }
)


class AuthError(Exception):
    """A standardised way to communicate an authentication failure.

    ``error`` is a JSON-serialisable body following the Auth0 convention of a
    machine-readable ``code`` plus a human-readable ``description``.
    """

    def __init__(self, error: Dict[str, str], status_code: int) -> None:
        """Store the error body and the HTTP status it should produce."""
        super().__init__(error.get("description", "authorization failed"))
        self.error = error
        self.status_code = status_code

    def __repr__(self) -> str:
        """Return a compact representation for logs."""
        return "<AuthError {0} {1}>".format(
            self.status_code, self.error.get("code", "unknown")
        )


def _setting(key: str, fallback: Any) -> Any:
    """Read a setting from the active app config, else the module default.

    Keeps the module importable and unit-testable outside an application
    context while letting a configured application win.
    """
    if current_app:
        return current_app.config.get(key, fallback)
    return fallback


def _auth0_domain() -> str:
    """Resolve the Auth0 tenant domain."""
    return _setting("AUTH0_DOMAIN", AUTH0_DOMAIN)


def _api_audience() -> str:
    """Resolve the API identifier this service accepts tokens for."""
    return _setting("AUTH0_API_AUDIENCE", API_AUDIENCE)


def _algorithms() -> List[str]:
    """Resolve the signing-algorithm allow-list."""
    return list(_setting("AUTH0_ALGORITHMS", ALGORITHMS))


def _issuer() -> str:
    """Resolve the expected ``iss`` claim, trailing slash included."""
    return "https://{0}/".format(_auth0_domain())


def _jwks_url() -> str:
    """Resolve the tenant's JWKS endpoint."""
    return "https://{0}/.well-known/jwks.json".format(_auth0_domain())


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------


class JWKSCache:
    """Thread-safe, TTL-bounded cache of Auth0's public signing keys.

    Fetching the JWKS on every request would put an Auth0 round trip in front
    of every API call and make the service unavailable whenever Auth0 is slow.
    Caching forever, on the other hand, means the service stops accepting
    tokens the moment Auth0 rotates a key.  This cache does both jobs: it
    serves from memory for ``ttl`` seconds, and when a token arrives bearing an
    unrecognised ``kid`` it refreshes once -- no more often than
    ``min_refresh_interval`` -- before deciding the key really is unknown.
    """

    def __init__(self) -> None:
        """Create an empty cache."""
        self._lock = threading.Lock()
        self._keys_by_kid: Dict[str, Dict[str, Any]] = {}
        self._fetched_at: float = 0.0
        self._last_fetch_attempt: float = 0.0
        self._url: Optional[str] = None

    def clear(self) -> None:
        """Forget every cached key.  Used by tests and by /health probes."""
        with self._lock:
            self._keys_by_kid = {}
            self._fetched_at = 0.0
            self._last_fetch_attempt = 0.0
            self._url = None

    def stats(self) -> Dict[str, Any]:
        """Return non-sensitive cache metrics for the health endpoint."""
        with self._lock:
            return {
                "keys_cached": len(self._keys_by_kid),
                "age_seconds": (
                    round(time.monotonic() - self._fetched_at, 1)
                    if self._fetched_at
                    else None
                ),
                "kids": sorted(self._keys_by_kid),
            }

    def get_key(self, kid: str, url: str, ttl: int, timeout: int, min_refresh: int):
        """Return the public key for ``kid``, refreshing the cache if needed.

        Raises :class:`AuthError` with a 401 when the key cannot be found, and
        with a 503 when Auth0 itself is unreachable -- an outage upstream is
        not the caller's fault and should not read as a bad token.
        """
        with self._lock:
            stale = (
                not self._keys_by_kid
                or self._url != url
                or (time.monotonic() - self._fetched_at) > ttl
            )
            if stale:
                self._refresh_locked(url, timeout)

            key = self._keys_by_kid.get(kid)
            if key is not None:
                return key

            # Unknown kid.  Auth0 may have rotated its signing key since the
            # last fetch, so refresh once -- but only if we have not just
            # tried, otherwise a stream of forged kids becomes an amplifier.
            since_attempt = time.monotonic() - self._last_fetch_attempt
            if since_attempt >= min_refresh:
                self._refresh_locked(url, timeout)
                key = self._keys_by_kid.get(kid)
                if key is not None:
                    return key

        raise AuthError(
            {
                "code": "invalid_header",
                "description": "Unable to find the appropriate signing key.",
            },
            401,
        )

    def _refresh_locked(self, url: str, timeout: int) -> None:
        """Fetch and parse the JWKS document.  Caller must hold the lock."""
        self._last_fetch_attempt = time.monotonic()
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            document = response.json()
        except requests.exceptions.RequestException as exc:
            raise AuthError(
                {
                    "code": "jwks_unavailable",
                    "description": (
                        "Unable to reach the authorization server to verify "
                        "the token. Please retry."
                    ),
                },
                503,
            ) from exc
        except ValueError as exc:
            raise AuthError(
                {
                    "code": "jwks_malformed",
                    "description": "The authorization server returned an "
                    "unreadable key set.",
                },
                503,
            ) from exc

        keys: Dict[str, Any] = {}
        for jwk in document.get("keys", []):
            kid = jwk.get("kid")
            # Only RSA signing keys are usable here.  Auth0 also publishes
            # encryption keys, which must not be treated as signature keys.
            if not kid or jwk.get("kty") != "RSA":
                continue
            if jwk.get("use") not in (None, "sig"):
                continue
            try:
                keys[kid] = RSAAlgorithm.from_jwk(json.dumps(jwk))
            except (ValueError, TypeError, KeyError):
                # A single unparseable key must not poison the whole set.
                continue

        if not keys:
            raise AuthError(
                {
                    "code": "jwks_malformed",
                    "description": "The authorization server published no "
                    "usable signing keys.",
                },
                503,
            )

        self._keys_by_kid = keys
        self._fetched_at = time.monotonic()
        self._url = url


#: Process-wide cache.  Safe to share: it guards its own state with a lock.
jwks_cache = JWKSCache()


# ---------------------------------------------------------------------------
# Auth header
# ---------------------------------------------------------------------------


def get_token_auth_header() -> str:
    """Extract the bearer token from the request's Authorization header.

    Returns:
        The raw JWT, with the ``Bearer`` scheme stripped.

    Raises:
        AuthError: 401 when the header is absent or malformed.
    """
    auth_header = request.headers.get("Authorization", None)
    if not auth_header:
        raise AuthError(
            {
                "code": "authorization_header_missing",
                "description": "Authorization header is expected.",
            },
            401,
        )

    parts = auth_header.split()

    if parts[0].lower() != "bearer":
        raise AuthError(
            {
                "code": "invalid_header",
                "description": 'Authorization header must start with "Bearer".',
            },
            401,
        )
    if len(parts) == 1:
        raise AuthError(
            {"code": "invalid_header", "description": "Token not found."},
            401,
        )
    if len(parts) > 2:
        raise AuthError(
            {
                "code": "invalid_header",
                "description": "Authorization header must be a bearer token.",
            },
            401,
        )

    return parts[1]


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------


def _permissions_from_payload(payload: Dict[str, Any]) -> Optional[List[str]]:
    """Read the caller's permissions from a decoded token.

    Auth0 delivers RBAC permissions in a ``permissions`` array when *Add
    Permissions in the Access Token* is enabled on the API.  Tenants that use
    scopes instead deliver a space-delimited ``scope`` string.  Both are
    accepted so the API works under either configuration; ``None`` means the
    token carried neither, which is a tenant misconfiguration rather than a
    caller error.
    """
    raw = payload.get("permissions")
    if isinstance(raw, list):
        return [str(item) for item in raw]

    scope = payload.get("scope")
    if isinstance(scope, str):
        return [item for item in scope.split() if item]

    return None


def check_permissions(permission: str, payload: Dict[str, Any]) -> bool:
    """Assert that a decoded token carries ``permission``.

    Args:
        permission: The permission string required, e.g. ``'post:drinks'``.
            An empty string means "any successfully verified token will do".
        payload: The decoded JWT payload.

    Returns:
        ``True`` when the permission is present.

    Raises:
        AuthError: 403 when the token is valid but does not grant the
            permission, or when it carries no permissions claim at all.
    """
    granted = _permissions_from_payload(payload)

    if granted is None:
        # The token verified, so the caller is authenticated -- they simply
        # cannot be authorised.  That is a 403, and the code points straight
        # at the tenant setting that needs fixing.
        raise AuthError(
            {
                "code": "invalid_permissions_claim",
                "description": (
                    "Token carries no permissions claim. Enable RBAC and "
                    '"Add Permissions in the Access Token" on the Auth0 API.'
                ),
            },
            403,
        )

    if not permission:
        return True

    if permission not in granted:
        raise AuthError(
            {
                "code": "unauthorized",
                "description": "Permission not found: {0}.".format(permission),
            },
            403,
        )

    return True


# ---------------------------------------------------------------------------
# Token verification
# ---------------------------------------------------------------------------


def verify_decode_jwt(token: str) -> Dict[str, Any]:
    """Verify an Auth0 access token and return its decoded payload.

    The token's ``kid`` selects a public key from the tenant's JWKS, the
    signature is checked against it, and the standard claims are validated:
    signature, expiry, issued-at, not-before, issuer and audience.

    Args:
        token: A JSON Web Token in compact serialisation.

    Returns:
        The decoded, verified payload.

    Raises:
        AuthError: 401 for any token that fails verification, 503 when the
            authorization server cannot be reached.
    """
    domain = _auth0_domain()
    audience = _api_audience()
    if not domain or not audience:
        # Refuse rather than fall back to something permissive.  A service
        # that cannot verify tokens must not serve protected data.
        raise AuthError(
            {
                "code": "server_misconfigured",
                "description": (
                    "The API is missing its Auth0 domain or audience "
                    "configuration and cannot verify tokens."
                ),
            },
            500,
        )

    try:
        unverified_header = jwt.get_unverified_header(token)
    except jwt.DecodeError as exc:
        raise AuthError(
            {
                "code": "invalid_header",
                "description": "Authorization malformed.",
            },
            401,
        ) from exc

    # Check the algorithm before touching a key.  PyJWT enforces the
    # allow-list again during decode; doing it here as well means a token
    # asking for `none` or HS256 never reaches key selection at all.
    algorithms = _algorithms()
    alg = unverified_header.get("alg")
    if alg not in algorithms:
        raise AuthError(
            {
                "code": "invalid_header",
                "description": "Token algorithm {0!r} is not allowed.".format(alg),
            },
            401,
        )

    kid = unverified_header.get("kid")
    if not kid:
        raise AuthError(
            {
                "code": "invalid_header",
                "description": "Authorization malformed: no key id.",
            },
            401,
        )

    public_key = jwks_cache.get_key(
        kid,
        url=_jwks_url(),
        ttl=int(_setting("JWKS_CACHE_TTL_SECONDS", 600)),
        timeout=int(_setting("JWKS_FETCH_TIMEOUT_SECONDS", 5)),
        min_refresh=int(_setting("JWKS_MIN_REFRESH_INTERVAL_SECONDS", 30)),
    )

    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=algorithms,
            audience=audience,
            issuer=_issuer(),
            leeway=int(_setting("AUTH0_LEEWAY_SECONDS", 10)),
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_nbf": True,
                "verify_iss": True,
                "verify_aud": True,
                "require": list(REQUIRED_CLAIMS),
            },
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError(
            {"code": "token_expired", "description": "Token expired."},
            401,
        ) from exc
    except jwt.MissingRequiredClaimError as exc:
        raise AuthError(
            {
                "code": "invalid_claims",
                "description": "Token is missing a required claim: {0}.".format(
                    exc.claim
                ),
            },
            401,
        ) from exc
    except (jwt.InvalidAudienceError, jwt.InvalidIssuerError) as exc:
        raise AuthError(
            {
                "code": "invalid_claims",
                "description": (
                    "Incorrect claims. Please check the audience and issuer."
                ),
            },
            401,
        ) from exc
    except jwt.InvalidTokenError as exc:
        # Covers bad signatures, malformed segments and immature tokens.
        raise AuthError(
            {
                "code": "invalid_token",
                "description": "Unable to parse authentication token.",
            },
            401,
        ) from exc

    return payload


# ---------------------------------------------------------------------------
# Decorator
# ---------------------------------------------------------------------------


def requires_auth(permission: str = ""):
    """Protect a view with Auth0 authentication and an RBAC check.

    Usage::

        @app.route('/drinks', methods=['POST'])
        @requires_auth('post:drinks')
        def create_drink(payload):
            ...

    The decorated view receives the verified JWT payload as its first
    positional argument, matching the project specification.  The same payload
    is also published on :data:`flask.g` so that error handlers, the audit log
    and the request logger can reach it without threading it through.

    Args:
        permission: The permission the caller must hold.  An empty string
            requires a valid token but no particular permission.

    Raises:
        ValueError: At import time if ``permission`` is not a permission this
            API knows about -- a typo becomes a startup failure, not a
            silently unreachable endpoint.
    """
    if permission and permission not in KNOWN_PERMISSIONS:
        raise ValueError(
            "Unknown permission {0!r}. Add it to KNOWN_PERMISSIONS in "
            "auth.py and to the Auth0 API's permission list.".format(permission)
        )

    def requires_auth_decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            token = get_token_auth_header()
            payload = verify_decode_jwt(token)
            check_permissions(permission, payload)

            # Publish the caller for downstream consumers (audit, logging).
            g.current_user = payload
            g.current_permission = permission

            return f(payload, *args, **kwargs)

        # Record the requirement on the view so that the generated OpenAPI
        # document and the /health/rbac introspection endpoint stay in step
        # with the code instead of drifting from it.
        wrapper.required_permission = permission
        return wrapper

    return requires_auth_decorator


# ---------------------------------------------------------------------------
# Helpers for callers outside the decorator
# ---------------------------------------------------------------------------


def current_user() -> Optional[Dict[str, Any]]:
    """Return the verified payload for this request, or ``None``."""
    return getattr(g, "current_user", None)


def current_user_sub(default: str = "anonymous") -> str:
    """Return the caller's Auth0 ``sub``, or ``default`` when unauthenticated."""
    payload = current_user()
    if not payload:
        return default
    return str(payload.get("sub", default))


def current_user_permissions() -> Sequence[str]:
    """Return the permissions on the current request's token."""
    payload = current_user()
    if not payload:
        return ()
    return tuple(_permissions_from_payload(payload) or ())


def has_permission(permission: str) -> bool:
    """Report whether the current caller holds ``permission``.

    Unlike :func:`check_permissions` this never raises, which suits branching
    inside a view -- for example letting a manager edit only baristas while an
    administrator edits anyone.
    """
    return permission in current_user_permissions()
