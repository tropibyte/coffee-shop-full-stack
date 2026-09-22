"""Audit trail for privileged and state-changing actions.

Role-based access control decides whether an action is allowed.  An audit
trail records that it happened.  Without the second, a compromised manager
account is indistinguishable from a busy one, and "who deleted that drink?"
has no answer once the row is gone.

Every event is written twice: to the structured log, where a collector will
pick it up and where it cannot be altered from inside the application, and to
the ``audit_event`` table, which backs the ``GET /audit`` endpoint.  A failure
to persist the row is logged but never propagated -- refusing a legitimate
delete because the audit insert failed would be a worse outcome than a gap in
the table, and the log line survives either way.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from flask import current_app, g

from .auth.auth import current_user_sub
from .database.models import AuditEvent, db

logger = logging.getLogger("coffee_shop.audit")

#: Keys that must never reach the audit detail blob, however they arrive.
_REDACTED_KEYS = frozenset(
    {
        "authorization",
        "access_token",
        "client_secret",
        "id_token",
        "password",
        "refresh_token",
        "secret",
        "token",
    }
)


def _scrub(value: Any, depth: int = 0) -> Any:
    """Recursively drop anything secret-looking from an audit detail blob."""
    if depth > 4:
        return "..."
    if isinstance(value, dict):
        return {
            key: (
                "[redacted]"
                if key.lower() in _REDACTED_KEYS
                else _scrub(item, depth + 1)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item, depth + 1) for item in value[:20]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...(truncated)"
    return value


def record(
    action: str,
    resource_type: str,
    resource_id: Optional[Any] = None,
    status_code: Optional[int] = None,
    detail: Optional[Dict[str, Any]] = None,
) -> Optional[AuditEvent]:
    """Write one audit event.

    Args:
        action: Verb describing what happened, e.g. ``'drink.deleted'``.
        resource_type: The kind of thing acted on, e.g. ``'drink'``.
        resource_id: Identifier of the thing acted on, if there is one.
        status_code: HTTP status the request returned.
        detail: Extra non-sensitive context; scrubbed before storage.

    Returns:
        The persisted :class:`AuditEvent`, or ``None`` when auditing is off or
        the insert failed.
    """
    actor = current_user_sub()
    permission = getattr(g, "current_permission", None) or None
    request_id = getattr(g, "request_id", None)
    safe_detail = _scrub(detail or {})

    # The log line is written unconditionally: it is the copy that survives a
    # database problem, and the one a SIEM will actually be watching.
    logger.info(
        "audit",
        extra={
            "event": "audit",
            "action": action,
            "resource_type": resource_type,
            "resource_id": str(resource_id) if resource_id is not None else None,
            "actor": actor,
            "permission": permission,
            "status": status_code,
            "request_id": request_id,
            "detail": safe_detail,
        },
    )

    if not current_app.config.get("AUDIT_LOG_ENABLED", True):
        return None

    event = AuditEvent(
        actor_sub=actor,
        permission=permission,
        action=action,
        resource_type=resource_type,
        resource_id=str(resource_id) if resource_id is not None else None,
        status_code=status_code,
        request_id=request_id,
        detail=json.dumps(safe_detail) if safe_detail else None,
    )
    try:
        db.session.add(event)
        db.session.commit()
    except Exception:  # noqa: BLE001 - see module docstring
        db.session.rollback()
        logger.exception(
            "failed to persist audit event",
            extra={"event": "audit_persist_failed", "action": action},
        )
        return None

    return event
