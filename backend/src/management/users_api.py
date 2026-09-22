"""User administration endpoints backed by the Auth0 Management API.

This is where the three-tier requirement is actually enforced::

    Barista        can do nothing here
    Manager        can manage baristas
    Administrator  can manage baristas and managers

Auth0 permissions cannot express "only on someone junior to you" -- a
permission is a verb, not a relationship.  So authorisation here happens in
two layers, and both must pass:

1. **Capability.**  ``@requires_auth('patch:users')`` asks Auth0 whether the
   caller may edit users at all.  A barista holds none of these permissions
   and is stopped at the decorator with a 403.
2. **Scope of target.**  Every mutating view then compares the caller's role
   rank with the target's.  A caller may only act on a strictly more junior
   account, and may only grant a role strictly more junior than their own.

The second layer is what stops a manager promoting themselves to
administrator, deleting an administrator, or quietly turning a barista into a
manager -- all of which the first layer alone would happily permit.

The caller's rank comes from a custom claim that an Auth0 Action adds to the
access token (see docs/AUTH0_SETUP.md).  If that claim is absent the rank is
looked up through the Management API instead, so a tenant that has not yet
deployed the Action degrades to "slower", not to "insecure".
"""

from __future__ import annotations

import logging
import re
import secrets
import string
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from flask import Blueprint, current_app, jsonify, request

from ..audit import record as audit_record
from ..auth.auth import AuthError, current_user_sub, requires_auth
from ..errors import error_response
from .auth0_client import Auth0ManagementClient, Auth0ManagementError

logger = logging.getLogger("coffee_shop.users")

users_bp = Blueprint("users", __name__)

#: The role ladder.  Higher rank may administer strictly lower ranks.
ROLE_RANKS: Dict[str, int] = {
    "barista": 0,
    "manager": 1,
    "administrator": 2,
}

#: Rank attributed to an account that holds no recognised role.
UNRANKED = -1

#: Namespaced custom claim written by the Auth0 Action.  Auth0 discards
#: non-namespaced custom claims, so the URL prefix is mandatory, not stylistic.
ROLES_CLAIM = "https://coffee-shop.api/roles"

#: Conservative email check.  Auth0 validates properly on its side; this only
#: keeps obvious junk from turning into an upstream round trip.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

_SELF_ROLE_CACHE_TTL_SECONDS = 60


class _TTLCache:
    """A tiny thread-safe TTL cache for the caller's own role list."""

    def __init__(self, ttl: int) -> None:
        """Create a cache whose entries live for ``ttl`` seconds."""
        self._ttl = ttl
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        """Return a live entry, or ``None`` when absent or expired."""
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            stored_at, value = entry
            if time.monotonic() - stored_at > self._ttl:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key``."""
        with self._lock:
            # Bounded so a flood of distinct subs cannot grow this without end.
            if len(self._entries) > 512:
                self._entries.clear()
            self._entries[key] = (time.monotonic(), value)

    def clear(self) -> None:
        """Drop every entry."""
        with self._lock:
            self._entries.clear()


_self_roles_cache = _TTLCache(_SELF_ROLE_CACHE_TTL_SECONDS)


# ---------------------------------------------------------------------------
# Rank helpers
# ---------------------------------------------------------------------------


def rank_of(role_names: List[str]) -> int:
    """Return the highest rank among ``role_names``."""
    ranks = [
        ROLE_RANKS[name.strip().lower()]
        for name in role_names
        if name and name.strip().lower() in ROLE_RANKS
    ]
    return max(ranks) if ranks else UNRANKED


def _client() -> Auth0ManagementClient:
    """Build a Management API client, or refuse if it is not configured."""
    if not current_app.config.get("MANAGEMENT_API_ENABLED", True):
        raise Auth0ManagementError(
            "User administration is disabled on this deployment.",
            status_code=503,
        )
    return Auth0ManagementClient.from_app()


def caller_roles(payload: Dict[str, Any]) -> List[str]:
    """Return the calling user's role names.

    Prefers the namespaced custom claim written by the Auth0 Action, and falls
    back to a short-lived Management API lookup when the claim is missing.
    """
    claimed = payload.get(ROLES_CLAIM)
    if isinstance(claimed, list) and claimed:
        return [str(name) for name in claimed]

    sub = str(payload.get("sub", ""))
    if not sub:
        return []

    cached = _self_roles_cache.get(sub)
    if cached is not None:
        return cached

    try:
        roles = [role.get("name", "") for role in _client().get_user_roles(sub)]
    except Auth0ManagementError:
        logger.warning(
            "could not resolve caller roles; treating as unranked",
            extra={"event": "caller_roles_unresolved", "actor": sub},
        )
        roles = []

    _self_roles_cache.set(sub, roles)
    return roles


def caller_rank(payload: Dict[str, Any]) -> int:
    """Return the calling user's rank on the role ladder."""
    return rank_of(caller_roles(payload))


def _assert_may_administer(
    actor_payload: Dict[str, Any], target_user_id: str, target_roles: List[str]
) -> int:
    """Refuse unless the caller outranks the target.  Returns the caller rank.

    Raises:
        AuthError: 403 when the caller is acting on themselves, on a peer, or
            on someone senior.
    """
    actor_sub = str(actor_payload.get("sub", ""))
    actor_rank = caller_rank(actor_payload)

    if actor_sub and actor_sub == target_user_id:
        raise AuthError(
            {
                "code": "self_administration_forbidden",
                "description": (
                    "You cannot change your own roles or account through this "
                    "endpoint. Ask an administrator."
                ),
            },
            403,
        )

    target_rank = rank_of(target_roles)
    if target_rank >= actor_rank:
        raise AuthError(
            {
                "code": "insufficient_rank",
                "description": ("You may only administer accounts junior to your own."),
            },
            403,
        )

    return actor_rank


def _assert_may_grant(actor_rank: int, role_name: str) -> str:
    """Refuse unless ``role_name`` is strictly junior to the caller.

    Returns the canonical (lower-cased) role name.

    Raises:
        AuthError: 403 on an attempt to grant a peer or senior role.
    """
    canonical = role_name.strip().lower()
    if canonical not in ROLE_RANKS:
        raise AuthError(
            {
                "code": "unknown_role",
                "description": "Unknown role {0!r}. Valid roles: {1}.".format(
                    role_name, ", ".join(sorted(ROLE_RANKS))
                ),
            },
            403,
        )
    if ROLE_RANKS[canonical] >= actor_rank:
        raise AuthError(
            {
                "code": "privilege_escalation_blocked",
                "description": (
                    "You may only grant roles junior to your own. Granting "
                    "{0!r} would be an escalation.".format(role_name)
                ),
            },
            403,
        )
    return canonical


def _generate_temporary_password() -> str:
    """Return a random password that satisfies Auth0's strongest policy.

    The value is never returned to a client and never logged: it exists only
    long enough for Auth0 to accept the account creation, after which a
    password-change ticket lets the real user choose their own.
    """
    alphabet = string.ascii_letters + string.digits
    specials = "!@#$%^&*()-_=+"
    body = "".join(secrets.choice(alphabet) for _ in range(24))
    # Guarantee one of each character class regardless of what the draw gave.
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice(specials),
    ]
    characters = list(body) + required
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


def _invalid_new_user_reason(body: Dict[str, Any]) -> Optional[str]:
    """Return why a user-creation body is unusable, or ``None`` if it is fine.

    Returning the reason rather than raising keeps :func:`create_user` linear
    and puts every input rule in one readable place.
    """
    email = str(body.get("email") or "").strip()
    role_name = str(body.get("role") or "").strip()
    name = str(body.get("name") or "").strip()

    if len(email) > 254:
        return "email address is too long"
    if not email or not _EMAIL_RE.match(email):
        return "a valid email address is required"
    if not role_name:
        return "role is required"
    if len(name) > 150:
        return "name must be 150 characters or fewer"
    return None


def _role_ids_by_name(client: Auth0ManagementClient) -> Dict[str, Dict[str, Any]]:
    """Map lower-cased role name to the Auth0 role object."""
    return {
        str(role.get("name", "")).strip().lower(): role for role in client.list_roles()
    }


def _present_user(
    user: Dict[str, Any], roles: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Project an Auth0 user into the fields this API publishes.

    An allow-list rather than a redaction list: Auth0 user objects carry
    identity provider tokens and app metadata, and none of that belongs in a
    response just because a future Auth0 release adds a field.
    """
    role_names = [str(role.get("name", "")) for role in (roles or [])]
    return {
        "user_id": user.get("user_id"),
        "email": user.get("email"),
        "name": user.get("name") or user.get("nickname"),
        "picture": user.get("picture"),
        "blocked": bool(user.get("blocked", False)),
        "email_verified": bool(user.get("email_verified", False)),
        "logins_count": user.get("logins_count", 0),
        "last_login": user.get("last_login"),
        "created_at": user.get("created_at"),
        "roles": role_names,
        "rank": rank_of(role_names),
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@users_bp.route("/users/me", methods=["GET"])
@requires_auth()
def whoami(payload):
    """Return the caller's own identity, roles and permissions.

    Requires nothing but a valid token, and deliberately exposes only the
    caller's own record -- it is the one user endpoint a barista may call.
    """
    roles = caller_roles(payload)
    return (
        jsonify(
            {
                "success": True,
                "user": {
                    "sub": payload.get("sub"),
                    "roles": roles,
                    "rank": rank_of(roles),
                    "permissions": sorted(payload.get("permissions", []) or []),
                    "issued_at": payload.get("iat"),
                    "expires_at": payload.get("exp"),
                },
            }
        ),
        200,
    )


@users_bp.route("/roles", methods=["GET"])
@requires_auth("get:roles")
def list_assignable_roles(payload):
    """List the roles the caller is permitted to grant.

    A manager sees only ``Barista``; an administrator sees ``Barista`` and
    ``Manager``.  Filtering here keeps a UI from offering a choice the API
    would then reject.
    """
    actor_rank = caller_rank(payload)
    client = _client()

    assignable = []
    for role in client.list_roles():
        name = str(role.get("name", ""))
        rank = ROLE_RANKS.get(name.strip().lower(), UNRANKED)
        if rank == UNRANKED or rank >= actor_rank:
            continue
        assignable.append(
            {
                "id": role.get("id"),
                "name": name,
                "description": role.get("description"),
                "rank": rank,
            }
        )

    assignable.sort(key=lambda item: item["rank"], reverse=True)
    return jsonify({"success": True, "roles": assignable, "your_rank": actor_rank}), 200


@users_bp.route("/users", methods=["GET"])
@requires_auth("get:users")
def list_users(payload):
    """List tenant users the caller is allowed to see.

    Accounts at or above the caller's own rank are filtered out, so a manager
    cannot enumerate administrators.
    """
    actor_rank = caller_rank(payload)
    client = _client()

    try:
        page = int(request.args.get("page", 0))
        per_page = int(request.args.get("per_page", 25))
    except ValueError:
        return error_response(400, "page and per_page must be integers")
    if page < 0 or per_page < 1:
        return error_response(400, "page must be >= 0 and per_page must be >= 1")

    query = request.args.get("q") or None
    result = client.list_users(page=page, per_page=per_page, query=query)
    users = result.get("users", result if isinstance(result, list) else [])

    visible = []
    for user in users:
        user_id = user.get("user_id")
        roles = client.get_user_roles(user_id) if user_id else []
        presented = _present_user(user, roles)
        if presented["rank"] >= actor_rank:
            continue
        visible.append(presented)

    return (
        jsonify(
            {
                "success": True,
                "users": visible,
                "page": page,
                "per_page": per_page,
                "total_visible": len(visible),
                "total_in_tenant": result.get("total"),
                "your_rank": actor_rank,
            }
        ),
        200,
    )


@users_bp.route("/users/<path:user_id>", methods=["GET"])
@requires_auth("get:users")
def get_user(payload, user_id):
    """Return one user, provided the caller outranks them."""
    actor_rank = caller_rank(payload)
    client = _client()

    user = client.get_user(user_id)
    roles = client.get_user_roles(user_id)
    presented = _present_user(user, roles)

    if presented["rank"] >= actor_rank and str(payload.get("sub")) != user_id:
        # Answer 404 rather than 403: confirming that a senior account exists
        # is itself information a junior caller has no business collecting.
        return error_response(404, "resource not found")

    return jsonify({"success": True, "user": presented}), 200


@users_bp.route("/users", methods=["POST"])
@requires_auth("post:users")
def create_user(payload):
    """Create a user and grant them a role junior to the caller's.

    No password crosses this boundary in either direction.  The account is
    created with a random secret that is discarded immediately, and the
    response carries a one-time ticket URL with which the new user sets their
    own password.
    """
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(400, "request body must be a JSON object")

    invalid = _invalid_new_user_reason(body)
    if invalid:
        return error_response(422, invalid)

    email = str(body.get("email", "")).strip()
    role_name = str(body.get("role", "")).strip()
    name = (str(body.get("name") or "")).strip() or None

    actor_rank = caller_rank(payload)
    canonical_role = _assert_may_grant(actor_rank, role_name)

    client = _client()
    roles_by_name = _role_ids_by_name(client)
    role = roles_by_name.get(canonical_role)
    if not role:
        return error_response(
            422,
            "role {0!r} is not defined in the Auth0 tenant".format(role_name),
        )

    created = client.create_user(
        email=email, password=_generate_temporary_password(), name=name
    )
    user_id = created.get("user_id")

    try:
        client.assign_roles(user_id, [role["id"]])
    except Auth0ManagementError:
        # A user with no role is a user who can do nothing, which is a worse
        # outcome than no user at all.  Roll the creation back.
        logger.error(
            "role assignment failed; removing the half-created account",
            extra={"event": "user_create_rollback", "resource_id": user_id},
        )
        try:
            client.delete_user(user_id)
        except Auth0ManagementError:
            logger.exception("rollback delete also failed")
        raise

    ticket_url = None
    try:
        ticket = client.create_password_change_ticket(
            user_id, result_url=current_app.config.get("FRONTEND_URL") or None
        )
        ticket_url = ticket.get("ticket")
    except Auth0ManagementError:
        # The account is valid; the invitation simply has to be re-sent.
        logger.warning(
            "user created but password ticket failed",
            extra={"event": "password_ticket_failed", "resource_id": user_id},
        )

    audit_record(
        action="user.created",
        resource_type="auth0_user",
        resource_id=user_id,
        status_code=201,
        detail={"email": email, "role": canonical_role, "actor_rank": actor_rank},
    )

    return (
        jsonify(
            {
                "success": True,
                "user": _present_user(created, [role]),
                "password_setup_url": ticket_url,
                "note": (
                    "Send the password_setup_url to the new user. No password "
                    "was transmitted or stored by this API."
                ),
            }
        ),
        201,
    )


@users_bp.route("/users/<path:user_id>", methods=["PATCH"])
@requires_auth("patch:users")
def update_user(payload, user_id):
    """Change a junior user's role, display name, or blocked state."""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return error_response(400, "request body must be a JSON object")
    if not any(key in body for key in ("role", "blocked", "name")):
        return error_response(422, "provide at least one of: role, blocked, name")

    client = _client()
    existing_roles = client.get_user_roles(user_id)
    existing_role_names = [str(r.get("name", "")) for r in existing_roles]
    actor_rank = _assert_may_administer(payload, user_id, existing_role_names)

    changes: Dict[str, Any] = {}
    if "blocked" in body:
        if not isinstance(body["blocked"], bool):
            return error_response(422, "blocked must be true or false")
        changes["blocked"] = body["blocked"]
    if "name" in body:
        name = body["name"]
        if not isinstance(name, str) or not name.strip():
            return error_response(422, "name must be a non-empty string")
        if len(name.strip()) > 150:
            return error_response(422, "name must be 150 characters or fewer")
        changes["name"] = name.strip()

    updated_user = (
        client.update_user(user_id, changes) if changes else client.get_user(user_id)
    )

    if "role" in body:
        canonical_role = _assert_may_grant(actor_rank, str(body["role"]))
        roles_by_name = _role_ids_by_name(client)
        target_role = roles_by_name.get(canonical_role)
        if not target_role:
            return error_response(
                422,
                "role {0!r} is not defined in the Auth0 tenant".format(body["role"]),
            )

        # Strip every recognised role before granting, so a promotion never
        # leaves the old role behind and silently widens the account.
        stale = [
            role["id"]
            for role in existing_roles
            if str(role.get("name", "")).strip().lower() in ROLE_RANKS
            and role.get("id") != target_role["id"]
        ]
        if stale:
            client.remove_roles(user_id, stale)
        if target_role["id"] not in {r.get("id") for r in existing_roles}:
            client.assign_roles(user_id, [target_role["id"]])

        # A role change invalidates any cached view of that user's rank.
        _self_roles_cache.clear()

    final_roles = client.get_user_roles(user_id)

    audit_record(
        action="user.updated",
        resource_type="auth0_user",
        resource_id=user_id,
        status_code=200,
        detail={
            "changes": sorted(changes),
            "roles_before": existing_role_names,
            "roles_after": [str(r.get("name", "")) for r in final_roles],
            "actor_rank": actor_rank,
        },
    )

    return (
        jsonify({"success": True, "user": _present_user(updated_user, final_roles)}),
        200,
    )


@users_bp.route("/users/<path:user_id>", methods=["DELETE"])
@requires_auth("delete:users")
def delete_user(payload, user_id):
    """Delete a junior user from the tenant."""
    client = _client()
    existing_roles = client.get_user_roles(user_id)
    existing_role_names = [str(r.get("name", "")) for r in existing_roles]
    actor_rank = _assert_may_administer(payload, user_id, existing_role_names)

    # Read the record before destroying it: an audit row that cannot say who
    # was deleted is barely an audit row.
    try:
        doomed = client.get_user(user_id)
    except Auth0ManagementError:
        doomed = {}

    client.delete_user(user_id)
    _self_roles_cache.clear()

    audit_record(
        action="user.deleted",
        resource_type="auth0_user",
        resource_id=user_id,
        status_code=200,
        detail={
            "email": doomed.get("email"),
            "roles": existing_role_names,
            "actor_rank": actor_rank,
        },
    )

    return jsonify({"success": True, "delete": user_id}), 200


# ---------------------------------------------------------------------------
# Upstream error translation
# ---------------------------------------------------------------------------


@users_bp.app_errorhandler(Auth0ManagementError)
def handle_auth0_error(error: Auth0ManagementError):
    """Render an upstream Auth0 failure without leaking its body."""
    logger.warning(
        "auth0 management error",
        extra={
            "event": "auth0_management_error",
            "status": error.status_code,
            "upstream_status": error.upstream_status,
            "actor": current_user_sub(),
        },
    )
    return error_response(error.status_code, error.message)
