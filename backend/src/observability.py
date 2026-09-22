"""Structured logging, request correlation and security headers.

Three concerns that every request passes through:

* **Correlation.** Each request is stamped with an id, echoed back in the
  ``X-Request-Id`` response header and embedded in every log line and audit
  row it produces.  Given a failure a user reports, the id is enough to pull
  the whole story out of the log.
* **Structured logs.** In production the log is newline-delimited JSON, which
  Azure Log Analytics and every other collector can index without a regex.  On
  a laptop it is a short coloured line, because a human is reading it.
* **Security headers.** Defaults that cost nothing on a JSON API and remove a
  class of browser-side surprises.

Nothing here logs a token, an Authorization header or a Management API
secret.  Only the ``sub`` claim identifies the caller.
"""

from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from typing import Any, Dict

from flask import g, request

#: Header a caller may set to supply their own correlation id.
REQUEST_ID_HEADER = "X-Request-Id"

#: Log record attributes that are already represented elsewhere in the JSON
#: envelope, or that are noise.  Everything else on a record is emitted.
_RESERVED_LOG_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


class JSONFormatter(logging.Formatter):
    """Render a log record as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialise ``record``, preserving any extra fields it carries."""
        payload: Dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + ".{0:03d}Z".format(int(record.msecs)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_ATTRS or key.startswith("_"):
                continue
            payload[key] = _jsonable(value)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    """Compact single-line output for local development."""

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as ``LEVEL logger message key=value ...``."""
        base = "{0:<8} {1:<28} {2}".format(
            record.levelname, record.name, record.getMessage()
        )
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _RESERVED_LOG_ATTRS
            and not key.startswith("_")
            and key not in ("event",)
        }
        if extras:
            base += "  " + " ".join(
                "{0}={1}".format(k, v) for k, v in sorted(extras.items())
            )
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def _jsonable(value: Any) -> Any:
    """Coerce a value into something :func:`json.dumps` will accept."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return str(value)


def configure_logging(app) -> None:
    """Install the chosen formatter on the root logger and on Flask's."""
    level = getattr(logging, app.config.get("LOG_LEVEL", "INFO"), logging.INFO)
    fmt = app.config.get("LOG_FORMAT", "json")
    formatter = JSONFormatter() if fmt == "json" else ConsoleFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    # Replace rather than append, so a reload does not double every line.
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level)

    app.logger.handlers = []
    app.logger.propagate = True
    app.logger.setLevel(level)

    # Werkzeug's own request log duplicates ours; keep it for warnings only.
    logging.getLogger("werkzeug").setLevel(max(level, logging.WARNING))


def register_request_hooks(app) -> None:
    """Stamp every request with an id and log its outcome."""

    @app.before_request
    def _start_request() -> None:
        """Assign a correlation id and start the latency clock."""
        supplied = request.headers.get(REQUEST_ID_HEADER, "")
        # Accept a client-supplied id only if it looks like one; otherwise a
        # caller could inject newlines or megabytes into the log stream.
        if supplied and len(supplied) <= 64 and supplied.replace("-", "").isalnum():
            g.request_id = supplied
        else:
            g.request_id = uuid.uuid4().hex
        g.request_started_at = time.perf_counter()

    @app.after_request
    def _finish_request(response):
        """Log the completed request and add correlation/security headers."""
        started = getattr(g, "request_started_at", None)
        duration_ms = (
            round((time.perf_counter() - started) * 1000, 2) if started else None
        )
        request_id = getattr(g, "request_id", None)

        if request_id:
            response.headers[REQUEST_ID_HEADER] = request_id

        _apply_security_headers(response)

        # Health probes would otherwise dominate the log.
        if request.path not in ("/health", "/health/live", "/health/ready"):
            app.logger.info(
                "request",
                extra={
                    "event": "request",
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.path,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                    "actor": _safe_actor(),
                    "remote_addr": request.remote_addr,
                },
            )
        return response


def _safe_actor() -> str:
    """Return the caller's ``sub`` claim, or ``anonymous``.

    Imported lazily to keep this module free of an import cycle with auth.
    """
    from .auth.auth import current_user_sub

    return current_user_sub()


def _apply_security_headers(response) -> None:
    """Add conservative security headers to every response.

    A JSON API is not a web page, but browsers still fetch it, and these cost
    nothing.  ``nosniff`` stops a JSON body being re-interpreted as HTML, and
    a ``default-src 'none'`` policy means a JSON response can never load or
    execute anything even if it does get treated as a document.  The docs page
    sets its own, looser policy via ``setdefault``.  HSTS is applied only over
    HTTPS so that plain-HTTP local development is unaffected.
    """
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
    )
    response.headers.setdefault(
        "Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'"
    )
    response.headers.setdefault("Cache-Control", "no-store")
    if request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
