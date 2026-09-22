"""Uniform JSON error handling.

Every failure leaving this API -- an aborted view, an unhandled exception, a
routing miss, a rate-limit rejection -- is rendered with the same body:

.. code-block:: json

    {"success": false, "error": 404, "message": "resource not found"}

That shape is what the project specification asks for and what the Postman
collection asserts on.  Authorisation failures additionally carry the Auth0
style ``code`` and ``description``, which a client needs in order to tell
"your token expired" apart from "you are not allowed to do that".

The handlers are registered in one place so that no route can accidentally
return an HTML traceback to a JSON client.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional, Tuple

from flask import current_app, g, jsonify
from werkzeug.exceptions import HTTPException

from .auth.auth import AuthError

#: Default human-readable message per status code.
ERROR_MESSAGES = {
    400: "bad request",
    401: "unauthorized",
    403: "forbidden",
    404: "resource not found",
    405: "method not allowed",
    409: "conflict",
    413: "payload too large",
    415: "unsupported media type",
    422: "unprocessable",
    429: "too many requests",
    500: "internal server error",
    503: "service unavailable",
}


def error_response(
    status_code: int,
    message: Optional[str] = None,
    **extra: Any,
) -> Tuple[Any, int]:
    """Build the canonical error body for ``status_code``.

    Args:
        status_code: The HTTP status to return.
        message: Overrides the default message for that status.
        **extra: Additional top-level keys, e.g. ``code`` and ``description``
            on an authorisation failure.

    Returns:
        A ``(response, status_code)`` pair ready to return from a view.
    """
    body: Dict[str, Any] = {
        "success": False,
        "error": status_code,
        "message": message or ERROR_MESSAGES.get(status_code, "request failed"),
    }
    body.update(extra)

    # Correlates the client's failure with the server's structured log line.
    request_id = getattr(g, "request_id", None)
    if request_id:
        body["request_id"] = request_id

    return jsonify(body), status_code


def register_error_handlers(app) -> None:
    """Attach every JSON error handler to ``app``."""

    # -- The specification's worked example ---------------------------------

    @app.errorhandler(422)
    def unprocessable(error):
        """Return 422 for a well-formed request the server cannot process."""
        return (
            jsonify({"success": False, "error": 422, "message": "unprocessable"}),
            422,
        )

    # -- The remaining status codes -----------------------------------------

    @app.errorhandler(400)
    def bad_request(error):
        """Return 400 when the request itself is malformed."""
        return error_response(400, _described(error, 400))

    @app.errorhandler(401)
    def unauthorized(error):
        """Return 401 when the caller has not proven who they are."""
        return error_response(401, _described(error, 401))

    @app.errorhandler(403)
    def forbidden(error):
        """Return 403 when the caller is known but not permitted."""
        return error_response(403, _described(error, 403))

    @app.errorhandler(404)
    def not_found(error):
        """Return 404 when the resource does not exist."""
        return error_response(404, "resource not found")

    @app.errorhandler(405)
    def method_not_allowed(error):
        """Return 405 when the route exists but the verb does not."""
        return error_response(405, "method not allowed")

    @app.errorhandler(409)
    def conflict(error):
        """Return 409 when the request collides with existing state."""
        return error_response(409, _described(error, 409))

    @app.errorhandler(413)
    def payload_too_large(error):
        """Return 413 when the request body exceeds the configured limit."""
        return error_response(413, "payload too large")

    @app.errorhandler(415)
    def unsupported_media_type(error):
        """Return 415 when the body is not JSON."""
        return error_response(
            415, "unsupported media type: send Content-Type: application/json"
        )

    @app.errorhandler(429)
    def too_many_requests(error):
        """Return 429 when the caller has exceeded the rate limit."""
        return error_response(
            429,
            "too many requests",
            description=getattr(error, "description", None),
        )

    @app.errorhandler(503)
    def service_unavailable(error):
        """Return 503 when a dependency is temporarily unreachable."""
        return error_response(503, _described(error, 503))

    # -- Authorisation ------------------------------------------------------

    @app.errorhandler(AuthError)
    def auth_error(error: AuthError):
        """Render an :class:`AuthError` as JSON.

        The Auth0-style ``code``/``description`` pair rides along beside the
        canonical body so that a client can distinguish an expired token from
        a missing permission without parsing prose.
        """
        detail = error.error or {}
        return error_response(
            error.status_code,
            detail.get("description")
            or ERROR_MESSAGES.get(error.status_code, "authorization failed"),
            code=detail.get("code", "authorization_failed"),
            description=detail.get("description", ""),
        )

    # -- Catch-alls ---------------------------------------------------------

    @app.errorhandler(HTTPException)
    def http_exception(error: HTTPException):
        """Render any other werkzeug HTTP error as JSON, never as HTML."""
        return error_response(error.code or 500, _described(error, error.code or 500))

    @app.errorhandler(Exception)
    def unhandled_exception(error: Exception):
        """Log an unexpected exception and return an opaque 500.

        The client is told only that something broke.  The stack trace, the
        exception type and a correlation id go to the server log, because an
        error message is an information-disclosure channel like any other.
        """
        incident = getattr(g, "request_id", None) or uuid.uuid4().hex
        current_app.logger.exception(
            "unhandled exception",
            extra={"event": "unhandled_exception", "request_id": incident},
        )
        return error_response(
            500,
            "internal server error",
            request_id=incident,
        )


def _described(error: Any, status_code: int) -> str:
    """Prefer werkzeug's description, falling back to the default message.

    Werkzeug's default descriptions are full sentences aimed at humans, which
    is exactly what belongs in ``message`` when a view called ``abort`` with a
    reason of its own.
    """
    description = getattr(error, "description", None)
    if isinstance(description, str) and description.strip():
        return description
    return ERROR_MESSAGES.get(status_code, "request failed")
