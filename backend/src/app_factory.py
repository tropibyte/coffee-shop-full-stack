"""Application factory.

``api.py`` holds the five drink endpoints the project specification asks for
and reads top to bottom as a plain Flask module.  Everything that has to
happen *around* those endpoints -- configuration, logging, CORS, rate limits,
error handlers, blueprint registration, first-boot database work -- is
assembled here, so that neither file has to be read to understand the other.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Optional, Type

from flask import Flask, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from .config import Config, get_config
from .database.models import db, seed_demo_drinks, setup_db
from .errors import register_error_handlers
from .observability import configure_logging, register_request_hooks

logger = logging.getLogger("coffee_shop")

#: Reject request bodies larger than this before they are parsed.  A drink
#: recipe is a few hundred bytes; anything approaching a megabyte is either a
#: mistake or an attempt to make the server do pointless work.
MAX_CONTENT_LENGTH = 256 * 1024

#: Shared limiter instance, attached to the app during :func:`create_app`.
limiter = Limiter(key_func=get_remote_address)


def rate_limit_key() -> str:
    """Return the identity a rate limit should be counted against.

    Authenticated callers are keyed on a hash of their bearer token, so one
    noisy client cannot spend another's budget and so a shared NAT egress
    address does not throttle an entire office.  The token is hashed, never
    stored: the limiter needs a stable identifier, not a credential.

    Callers with no token fall back to their source address.
    """
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        token = header[7:].strip()
        if token:
            return "tok:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
    return "ip:" + (get_remote_address() or "unknown")


def create_app(config_object: Optional[Type[Config]] = None) -> Flask:
    """Build, configure and return the Flask application.

    Args:
        config_object: A configuration class.  When omitted it is resolved
            from the ``FLASK_CONFIG`` environment variable, defaulting to
            development.

    Returns:
        A fully wired :class:`~flask.Flask` application.
    """
    # .env is loaded by src.config at import time -- it has to be, because the
    # configuration classes read os.environ in their class bodies.
    config_object = config_object or get_config()
    config_object.validate()

    app = Flask(__name__)
    app.config.from_object(config_object)
    app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH
    # Keep the derived Auth0 values on the config so that auth.py and the
    # Management client can read everything from one place.
    app.config["AUTH0_ISSUER"] = config_object.issuer()
    app.config["AUTH0_JWKS_URL"] = config_object.jwks_url()
    app.config["FRONTEND_URL"] = os.environ.get("FRONTEND_URL", "")

    configure_logging(app)

    # -- Persistence --------------------------------------------------------
    setup_db(app)

    # -- Cross-origin -------------------------------------------------------
    # Origins are named explicitly rather than wildcarded.  The frontend sends
    # a bearer token, so a permissive policy here would let any page on the
    # internet make authenticated calls on a logged-in user's behalf.
    CORS(
        app,
        resources={r"/*": {"origins": app.config["CORS_ORIGINS"]}},
        allow_headers=["Content-Type", "Authorization", "X-Request-Id"],
        methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        expose_headers=["X-Request-Id"],
        supports_credentials=False,
        max_age=600,
    )

    # -- Abuse resistance ---------------------------------------------------
    limiter.enabled = bool(app.config.get("RATELIMIT_ENABLED", True))
    limiter._key_func = rate_limit_key  # noqa: SLF001 - documented extension point
    limiter.init_app(app)
    app.config["RATELIMIT_STORAGE_URI"] = app.config.get(
        "RATELIMIT_STORAGE_URI", "memory://"
    )

    # -- Cross-cutting request behaviour ------------------------------------
    register_request_hooks(app)
    register_error_handlers(app)

    # -- Blueprints ---------------------------------------------------------
    from .health import health_bp

    app.register_blueprint(health_bp)

    if app.config.get("MANAGEMENT_API_ENABLED", True):
        from .management.users_api import users_bp

        app.register_blueprint(users_bp)
    else:
        logger.info(
            "user administration endpoints disabled by configuration",
            extra={"event": "management_api_disabled"},
        )

    if app.config.get("DOCS_ENABLED", True):
        from .docs import docs_bp

        app.register_blueprint(docs_bp)

    # -- First-boot database work -------------------------------------------
    _prepare_database(app)

    logger.info(
        "coffee shop api ready",
        extra={
            "event": "startup",
            "config": config_object.__name__,
            "auth0_domain": app.config.get("AUTH0_DOMAIN"),
            "audience": app.config.get("AUTH0_API_AUDIENCE"),
            "management_api": app.config.get("MANAGEMENT_API_ENABLED"),
        },
    )
    return app


def _prepare_database(app: Flask) -> None:
    """Create the schema and, if asked, reset or seed it.

    Tests manage their own schema, so this is a no-op under ``TESTING``.
    """
    if app.config.get("TESTING"):
        return

    with app.app_context():
        if app.config.get("DB_DROP_AND_CREATE_ALL"):
            logger.warning(
                "DB_DROP_AND_CREATE_ALL is set: destroying and rebuilding the "
                "database",
                extra={"event": "db_reset"},
            )
            db.drop_all()

        db.create_all()

        if app.config.get("DB_SEED_IF_EMPTY") or app.config.get(
            "DB_DROP_AND_CREATE_ALL"
        ):
            inserted = seed_demo_drinks()
            if inserted:
                logger.info(
                    "seeded demo drinks",
                    extra={"event": "db_seeded", "rows": inserted},
                )
