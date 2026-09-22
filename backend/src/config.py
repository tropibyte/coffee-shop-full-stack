"""Environment-driven configuration for the Coffee Shop API.

Every value that differs between a laptop, CI and Azure App Service is read
from the environment.  No secret is ever written to this file, and the only
literals here are safe, non-sensitive defaults.

The configuration classes deliberately *fail fast*: :func:`Config.validate`
raises :class:`ConfigurationError` at start-up rather than letting the service
boot into a state where it would accept unverifiable tokens.  A service that
refuses to start is far easier to diagnose than one that silently authorises
every caller.
"""

from __future__ import annotations

import os
from typing import List, Optional, Type

from dotenv import load_dotenv

# The configuration classes below read os.environ in their *class bodies*,
# which run at import time.  The .env file therefore has to be loaded here,
# before the first class is defined -- loading it later, from the application
# factory, would be too late and every value would silently fall back to its
# default.  ``override=False`` keeps a real environment variable (what a
# container or Azure App Service sets) ahead of the developer's file.
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_ROOT, ".env"), override=False)


class ConfigurationError(RuntimeError):
    """Raised when a required setting is missing or self-contradictory."""


def _env_bool(name: str, default: bool = False) -> bool:
    """Read a boolean from the environment, accepting the usual spellings."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read an integer from the environment, falling back on ``default``."""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(
            "{0} must be an integer, got {1!r}".format(name, raw)
        ) from exc


def _env_list(name: str, default: str = "") -> List[str]:
    """Read a comma-separated list from the environment."""
    raw = os.environ.get(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _default_sqlite_url() -> str:
    """Return a SQLite URL pointing at ``src/database/database.db``.

    Azure App Service only persists files written under ``/home``, so the
    location stays overridable through ``DATABASE_URL``.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return "sqlite:///" + os.path.join(here, "database", "database.db")


class Config:
    """Base configuration shared by every environment."""

    # --- Flask core --------------------------------------------------------
    # Only used to sign Flask's own session cookie.  This API is stateless and
    # never sets a session, but a weak key is still worth avoiding.
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    JSON_SORT_KEYS = False
    TESTING = False
    DEBUG = False

    # --- Persistence -------------------------------------------------------
    SQLALCHEMY_DATABASE_URI = os.environ.get("DATABASE_URL") or _default_sqlite_url()
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # Recreate the schema (and the demo row) on boot.  Destructive, so it is
    # opt-in and refused outright by ProductionConfig.
    DB_DROP_AND_CREATE_ALL = _env_bool("DB_DROP_AND_CREATE_ALL", False)
    # Insert the starter drinks only when the table is empty.  Non-destructive,
    # so it is safe to leave on: it keeps a cold-started demo from looking
    # broken without ever touching data that already exists.
    DB_SEED_IF_EMPTY = _env_bool("DB_SEED_IF_EMPTY", True)

    # --- Auth0 -------------------------------------------------------------
    AUTH0_DOMAIN = os.environ.get("AUTH0_DOMAIN", "")
    AUTH0_API_AUDIENCE = os.environ.get("AUTH0_API_AUDIENCE", "")
    # An allow-list, never a value read back out of the token.  Trusting the
    # `alg` header a token asks for is the classic JWT confusion bug.
    AUTH0_ALGORITHMS = _env_list("AUTH0_ALGORITHMS", "RS256")
    # Seconds of clock skew tolerated when checking exp / iat / nbf.
    AUTH0_LEEWAY_SECONDS = _env_int("AUTH0_LEEWAY_SECONDS", 10)

    # --- JWKS cache --------------------------------------------------------
    JWKS_CACHE_TTL_SECONDS = _env_int("JWKS_CACHE_TTL_SECONDS", 600)
    JWKS_FETCH_TIMEOUT_SECONDS = _env_int("JWKS_FETCH_TIMEOUT_SECONDS", 5)
    # Smallest gap between two forced refreshes, so an attacker cannot turn
    # "unknown kid" into a request amplifier pointed at Auth0.
    JWKS_MIN_REFRESH_INTERVAL_SECONDS = _env_int(
        "JWKS_MIN_REFRESH_INTERVAL_SECONDS", 30
    )

    # --- Auth0 Management API (user administration endpoints) --------------
    AUTH0_M2M_CLIENT_ID = os.environ.get("AUTH0_M2M_CLIENT_ID", "")
    AUTH0_M2M_CLIENT_SECRET = os.environ.get("AUTH0_M2M_CLIENT_SECRET", "")
    AUTH0_CONNECTION = os.environ.get(
        "AUTH0_CONNECTION", "Username-Password-Authentication"
    )
    MANAGEMENT_API_ENABLED = _env_bool("MANAGEMENT_API_ENABLED", True)
    MANAGEMENT_HTTP_TIMEOUT_SECONDS = _env_int("MANAGEMENT_HTTP_TIMEOUT_SECONDS", 10)

    # --- Cross-origin ------------------------------------------------------
    # Defaults cover `ionic serve` (8100) and `ng serve` (4200) on localhost.
    CORS_ORIGINS = _env_list(
        "CORS_ORIGINS",
        "http://localhost:8100,http://127.0.0.1:8100,"
        "http://localhost:4200,http://127.0.0.1:4200",
    )

    # --- Rate limiting -----------------------------------------------------
    RATELIMIT_ENABLED = _env_bool("RATELIMIT_ENABLED", True)
    RATELIMIT_STORAGE_URI = os.environ.get("RATELIMIT_STORAGE_URI", "memory://")
    RATELIMIT_DEFAULT = os.environ.get("RATELIMIT_DEFAULT", "200 per minute")
    RATELIMIT_WRITE = os.environ.get("RATELIMIT_WRITE", "30 per minute")
    RATELIMIT_ADMIN = os.environ.get("RATELIMIT_ADMIN", "60 per minute")

    # --- Observability -----------------------------------------------------
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT = os.environ.get("LOG_FORMAT", "json").lower()
    AUDIT_LOG_ENABLED = _env_bool("AUDIT_LOG_ENABLED", True)

    # --- Documentation -----------------------------------------------------
    DOCS_ENABLED = _env_bool("DOCS_ENABLED", True)

    @classmethod
    def validate(cls) -> None:
        """Reject a configuration that cannot securely serve traffic."""
        missing = [
            name
            for name in ("AUTH0_DOMAIN", "AUTH0_API_AUDIENCE")
            if not getattr(cls, name)
        ]
        if missing:
            raise ConfigurationError(
                "Missing required Auth0 settings: "
                + ", ".join(missing)
                + ". Copy .env.example to .env and fill it in "
                "(see docs/AUTH0_SETUP.md)."
            )

        if not cls.AUTH0_ALGORITHMS:
            raise ConfigurationError("AUTH0_ALGORITHMS must not be empty.")

        # A symmetric algorithm cannot be verified against a public JWKS, and
        # allowing one alongside RS256 is exactly how algorithm-confusion
        # attacks get in.  Refuse rather than warn.
        symmetric = [a for a in cls.AUTH0_ALGORITHMS if a.upper().startswith("HS")]
        if symmetric:
            raise ConfigurationError(
                "Symmetric algorithms cannot be used with an Auth0 JWKS: "
                + ", ".join(symmetric)
            )
        if "none" in {a.lower() for a in cls.AUTH0_ALGORITHMS}:
            raise ConfigurationError("The 'none' algorithm is never acceptable.")

        if cls.MANAGEMENT_API_ENABLED and not (
            cls.AUTH0_M2M_CLIENT_ID and cls.AUTH0_M2M_CLIENT_SECRET
        ):
            raise ConfigurationError(
                "MANAGEMENT_API_ENABLED is on but AUTH0_M2M_CLIENT_ID / "
                "AUTH0_M2M_CLIENT_SECRET are not set. Set both, or set "
                "MANAGEMENT_API_ENABLED=false to run without user admin."
            )

    # -- Derived values -----------------------------------------------------

    @classmethod
    def issuer(cls) -> str:
        """The expected ``iss`` claim, always with its trailing slash."""
        return "https://{0}/".format(cls.AUTH0_DOMAIN)

    @classmethod
    def jwks_url(cls) -> str:
        """Location of the tenant's public signing keys."""
        return "https://{0}/.well-known/jwks.json".format(cls.AUTH0_DOMAIN)

    @classmethod
    def management_audience(cls) -> str:
        """Audience required on a Management API access token."""
        return "https://{0}/api/v2/".format(cls.AUTH0_DOMAIN)


class DevelopmentConfig(Config):
    """Local development: chatty logs, readable output, relaxed limits."""

    DEBUG = True
    LOG_FORMAT = os.environ.get("LOG_FORMAT", "console").lower()
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-not-a-secret")


class TestingConfig(Config):
    """Test runs: in-memory database, no network, deterministic limits.

    Tests mint their own RS256 tokens against a locally generated key pair and
    serve a matching JWKS document, so the production verification path is
    exercised end to end without ever contacting Auth0.

    Every setting the suite depends on is pinned here rather than inherited,
    because :class:`Config` reads the environment -- and a developer's local
    ``.env`` must never be able to change what the tests assert.  The Auth0
    Management endpoints, for instance, are always registered here and always
    served by test doubles.
    """

    TESTING = True
    DEBUG = False
    MANAGEMENT_API_ENABLED = True
    DOCS_ENABLED = True
    AUDIT_LOG_ENABLED = True
    AUTH0_ALGORITHMS = ["RS256"]
    AUTH0_LEEWAY_SECONDS = 10
    JWKS_FETCH_TIMEOUT_SECONDS = 5
    JWKS_MIN_REFRESH_INTERVAL_SECONDS = 30
    MANAGEMENT_HTTP_TIMEOUT_SECONDS = 10
    AUTH0_CONNECTION = "Username-Password-Authentication"
    CORS_ORIGINS = [
        "http://localhost:8100",
        "http://127.0.0.1:8100",
        "http://localhost:4200",
        "http://127.0.0.1:4200",
    ]
    # Fixed so test runs are reproducible; never reaches a real deployment.
    SECRET_KEY = "testing-only"  # nosec B105
    SQLALCHEMY_DATABASE_URI = "sqlite:///:memory:"
    AUTH0_DOMAIN = "coffee-shop-test.us.auth0.com"
    AUTH0_API_AUDIENCE = "coffee-shop-test"
    AUTH0_M2M_CLIENT_ID = "test-m2m-client"
    # Placeholder matched by the test doubles; not a credential.
    AUTH0_M2M_CLIENT_SECRET = "test-m2m-secret"  # nosec B105
    RATELIMIT_ENABLED = False
    JWKS_CACHE_TTL_SECONDS = 600
    LOG_LEVEL = "CRITICAL"
    DB_DROP_AND_CREATE_ALL = False
    DB_SEED_IF_EMPTY = False


class ProductionConfig(Config):
    """Azure App Service and container deployments."""

    DEBUG = False
    LOG_FORMAT = os.environ.get("LOG_FORMAT", "json").lower()

    @classmethod
    def validate(cls) -> None:
        """Add the checks that only start to matter once the API is public."""
        super().validate()
        if not cls.SECRET_KEY or len(cls.SECRET_KEY) < 32:
            raise ConfigurationError(
                "SECRET_KEY must be at least 32 random characters in "
                "production. Generate one with: python -c "
                "'import secrets; print(secrets.token_urlsafe(48))'"
            )
        if cls.DB_DROP_AND_CREATE_ALL:
            raise ConfigurationError(
                "DB_DROP_AND_CREATE_ALL must never be enabled in production; "
                "it destroys the drinks table on every boot."
            )
        if "*" in cls.CORS_ORIGINS:
            raise ConfigurationError(
                "CORS_ORIGINS must name the frontend origins explicitly in "
                "production rather than '*'."
            )


CONFIGURATIONS = {
    "development": DevelopmentConfig,
    "testing": TestingConfig,
    "production": ProductionConfig,
}


def get_config(name: Optional[str] = None) -> Type[Config]:
    """Resolve a configuration class from a name or from ``FLASK_CONFIG``."""
    key = (name or os.environ.get("FLASK_CONFIG") or "development").lower()
    try:
        return CONFIGURATIONS[key]
    except KeyError as exc:
        raise ConfigurationError(
            "Unknown FLASK_CONFIG {0!r}; expected one of {1}".format(
                key, ", ".join(sorted(CONFIGURATIONS))
            )
        ) from exc
