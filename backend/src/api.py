"""Coffee Shop drink menu API.

The five endpoints the project specification calls for:

======  =======================  ==========================  ================
Verb    Path                     Permission                  Representation
======  =======================  ==========================  ================
GET     /drinks                  (public)                    ``short()``
GET     /drinks-detail           ``get:drinks-detail``       ``long()``
POST    /drinks                  ``post:drinks``             ``long()``
PATCH   /drinks/<id>             ``patch:drinks``            ``long()``
DELETE  /drinks/<id>             ``delete:drinks``           id only
======  =======================  ==========================  ================

Two more read-only routes sit alongside them -- ``GET /drinks/<id>`` and
``GET /audit`` -- and the user-administration endpoints live in
``src/management/users_api.py``.

Error handlers are **not** defined in this module.  Every one of them,
including the 404 and the :class:`AuthError` handler the specification names,
is registered by :func:`src.errors.register_error_handlers`, which the
application factory calls during start-up.  Keeping them together is what
guarantees that a failure anywhere in the service -- a route that does not
exist, a rate limit, an unhandled exception -- comes back as the same JSON
shape rather than an HTML traceback.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from flask import jsonify, request
from sqlalchemy import exc

from . import audit
from .app_factory import create_app, limiter
from .auth.auth import AuthError, requires_auth  # noqa: F401 - AuthError re-exported
from .database.models import (
    AuditEvent,
    Drink,
    RecipeValidationError,
    db,
    validate_recipe,
    validate_title,
)
from .errors import error_response

app = create_app()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_limit() -> str:
    """Rate limit applied to the endpoints that change the menu."""
    return app.config.get("RATELIMIT_WRITE", "30 per minute")


def _admin_limit() -> str:
    """Rate limit applied to the audit trail."""
    return app.config.get("RATELIMIT_ADMIN", "60 per minute")


def _json_body() -> Dict[str, Any]:
    """Return the request's JSON object, or abort with a helpful error.

    Raises:
        _BadBody: Wrapping the response a view should return.
    """
    if not request.is_json:
        raise _BadBody(
            error_response(
                415,
                "unsupported media type: send Content-Type: application/json",
            )
        )
    body = request.get_json(silent=True)
    if body is None:
        raise _BadBody(error_response(400, "request body is not valid JSON"))
    if not isinstance(body, dict):
        raise _BadBody(error_response(400, "request body must be a JSON object"))
    return body


class _BadBody(Exception):
    """Carries a prepared error response out of :func:`_json_body`."""

    def __init__(self, response: Tuple[Any, int]) -> None:
        """Store the response the view should return."""
        super().__init__("malformed request body")
        self.response = response


def _ordered_drinks(query_args) -> Tuple[List[Drink], Optional[Dict[str, Any]]]:
    """Apply optional search, sort and pagination to the drinks query.

    Every parameter is optional and the defaults return the whole menu in id
    order, so a caller that passes nothing -- the Postman collection, the
    Ionic frontend -- sees exactly the specified behaviour.  Pagination
    engages only when ``page`` or ``per_page`` is supplied.

    Returns:
        The selected drinks, and pagination metadata when paginating.

    Raises:
        _BadBody: When a parameter is present but unusable.
    """
    query = db.session.query(Drink)

    search = (query_args.get("search") or "").strip()
    if search:
        if len(search) > 80:
            raise _BadBody(error_response(400, "search term is too long"))
        # ilike with escaped wildcards: a caller must not be able to smuggle
        # a pattern in and turn a lookup into a table scan of their design.
        pattern = "%{0}%".format(
            search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        query = query.filter(Drink.title.ilike(pattern, escape="\\"))

    sort = (query_args.get("sort") or "id").strip().lower()
    sortable = {"id": Drink.id, "title": Drink.title, "created_at": Drink.created_at}
    if sort not in sortable:
        raise _BadBody(
            error_response(
                400,
                "sort must be one of: {0}".format(", ".join(sorted(sortable))),
            )
        )
    order = (query_args.get("order") or "asc").strip().lower()
    if order not in ("asc", "desc"):
        raise _BadBody(error_response(400, "order must be 'asc' or 'desc'"))
    column = sortable[sort]
    query = query.order_by(column.desc() if order == "desc" else column.asc())

    paginating = "page" in query_args or "per_page" in query_args
    if not paginating:
        return query.all(), None

    try:
        page = int(query_args.get("page", 1))
        per_page = int(query_args.get("per_page", 10))
    except ValueError:
        raise _BadBody(error_response(400, "page and per_page must be integers"))
    if page < 1:
        raise _BadBody(error_response(400, "page must be 1 or greater"))
    if not 1 <= per_page <= 100:
        raise _BadBody(error_response(400, "per_page must be between 1 and 100"))

    total = query.count()
    drinks = query.limit(per_page).offset((page - 1) * per_page).all()
    return drinks, {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": (total + per_page - 1) // per_page if per_page else 0,
    }


# ---------------------------------------------------------------------------
# ROUTES
# ---------------------------------------------------------------------------


@app.route("/drinks", methods=["GET"])
def retrieve_drinks():
    """List every drink in its short, public form.

    Public: no token required.  The short representation carries colours and
    proportions but not ingredient names, which is what lets the menu be shown
    to a passer-by without giving away a recipe.

    Optional query parameters -- ``search``, ``sort``, ``order``, ``page`` and
    ``per_page`` -- are additive; omitting them returns the whole menu.

    Returns:
        200 with ``{"success": True, "drinks": [...]}``.
    """
    try:
        drinks, pagination = _ordered_drinks(request.args)
    except _BadBody as bad:
        return bad.response

    body: Dict[str, Any] = {
        "success": True,
        "drinks": [drink.short() for drink in drinks],
        "total": len(drinks),
    }
    if pagination:
        body["pagination"] = pagination
    return jsonify(body), 200


@app.route("/drinks/<int:drink_id>", methods=["GET"])
def retrieve_drink(drink_id: int):
    """Return one drink in its short, public form.

    Returns:
        200 with the drink, or 404 when no drink has that id.
    """
    drink = db.session.get(Drink, drink_id)
    if drink is None:
        return error_response(404, "resource not found")
    return jsonify({"success": True, "drinks": [drink.short()]}), 200


@app.route("/drinks-detail", methods=["GET"])
@requires_auth("get:drinks-detail")
def retrieve_drinks_detail(payload):
    """List every drink in its long form, including ingredient names.

    Requires the ``get:drinks-detail`` permission, which both baristas and
    managers hold.

    Returns:
        200 with ``{"success": True, "drinks": [...]}``.
    """
    try:
        drinks, pagination = _ordered_drinks(request.args)
    except _BadBody as bad:
        return bad.response

    body: Dict[str, Any] = {
        "success": True,
        "drinks": [drink.long() for drink in drinks],
        "total": len(drinks),
    }
    if pagination:
        body["pagination"] = pagination
    return jsonify(body), 200


@app.route("/drinks", methods=["POST"])
@limiter.limit(_write_limit)
@requires_auth("post:drinks")
def create_drink(payload):
    """Create a drink.

    Requires the ``post:drinks`` permission, held by managers only.

    Returns:
        200 with ``{"success": True, "drinks": [drink.long()]}``.

        The specification asks for 200 rather than the 201 a new resource
        would ordinarily warrant, and the Postman collection asserts on 200,
        so 200 it is.  The ``Location`` header still points at the new drink.
    """
    try:
        body = _json_body()
    except _BadBody as bad:
        return bad.response

    if "title" not in body or "recipe" not in body:
        return error_response(422, "both 'title' and 'recipe' are required")

    try:
        title = validate_title(body["title"])
        recipe = validate_recipe(body["recipe"])
    except RecipeValidationError as invalid:
        return error_response(422, str(invalid))

    drink = Drink(title=title, recipe=json.dumps(recipe))
    try:
        drink.insert()
    except exc.IntegrityError:
        db.session.rollback()
        # The unique constraint on title is the only one that can fire here.
        return error_response(409, "a drink titled {0!r} already exists".format(title))
    except exc.SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("failed to insert drink")
        return error_response(422, "unprocessable")

    audit.record(
        action="drink.created",
        resource_type="drink",
        resource_id=drink.id,
        status_code=200,
        detail={"title": drink.title, "ingredients": len(recipe)},
    )

    response = jsonify({"success": True, "drinks": [drink.long()]})
    response.headers["Location"] = "/drinks/{0}".format(drink.id)
    return response, 200


@app.route("/drinks/<int:drink_id>", methods=["PATCH"])
@limiter.limit(_write_limit)
@requires_auth("patch:drinks")
def update_drink(payload, drink_id: int):
    """Update a drink's title, recipe, or both.

    Requires the ``patch:drinks`` permission, held by managers only.  Fields
    that are absent from the body are left alone.

    Returns:
        200 with the updated drink, or 404 when no drink has that id.
    """
    drink = db.session.get(Drink, drink_id)
    if drink is None:
        return error_response(404, "resource not found")

    try:
        body = _json_body()
    except _BadBody as bad:
        return bad.response

    if "title" not in body and "recipe" not in body:
        return error_response(422, "provide 'title', 'recipe', or both")

    before = {"title": drink.title, "recipe": drink.recipe}

    try:
        if "title" in body:
            drink.title = validate_title(body["title"])
        if "recipe" in body:
            drink.recipe = json.dumps(validate_recipe(body["recipe"]))
    except RecipeValidationError as invalid:
        db.session.rollback()
        return error_response(422, str(invalid))

    try:
        drink.update()
    except exc.IntegrityError:
        db.session.rollback()
        return error_response(
            409, "a drink titled {0!r} already exists".format(body.get("title"))
        )
    except exc.SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("failed to update drink")
        return error_response(422, "unprocessable")

    audit.record(
        action="drink.updated",
        resource_type="drink",
        resource_id=drink.id,
        status_code=200,
        detail={
            "fields": sorted(k for k in ("title", "recipe") if k in body),
            "title_before": before["title"],
            "title_after": drink.title,
        },
    )

    return jsonify({"success": True, "drinks": [drink.long()]}), 200


@app.route("/drinks/<int:drink_id>", methods=["DELETE"])
@limiter.limit(_write_limit)
@requires_auth("delete:drinks")
def remove_drink(payload, drink_id: int):
    """Delete a drink.

    Requires the ``delete:drinks`` permission, held by managers only.

    Returns:
        200 with ``{"success": True, "delete": id}``, or 404 when no drink has
        that id.
    """
    drink = db.session.get(Drink, drink_id)
    if drink is None:
        return error_response(404, "resource not found")

    # Capture the record before it stops existing, so the audit row can say
    # what was destroyed and not merely that something was.
    snapshot = {"title": drink.title, "recipe": drink.recipe_json()}

    try:
        drink.delete()
    except exc.SQLAlchemyError:
        db.session.rollback()
        app.logger.exception("failed to delete drink")
        return error_response(422, "unprocessable")

    audit.record(
        action="drink.deleted",
        resource_type="drink",
        resource_id=drink_id,
        status_code=200,
        detail=snapshot,
    )

    return jsonify({"success": True, "delete": drink_id}), 200


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------


@app.route("/audit", methods=["GET"])
@limiter.limit(_admin_limit)
@requires_auth("get:audit")
def retrieve_audit_trail(payload):
    """Return the most recent audit events, newest first.

    Requires ``get:audit``, held by administrators only.  RBAC answers "may
    this happen?"; this endpoint answers "what has happened?", and an access
    control system without the second question is only half of one.

    Returns:
        200 with ``{"success": True, "events": [...]}``.
    """
    try:
        limit = int(request.args.get("limit", 50))
    except ValueError:
        return error_response(400, "limit must be an integer")
    if not 1 <= limit <= 500:
        return error_response(400, "limit must be between 1 and 500")

    query = db.session.query(AuditEvent)

    action = (request.args.get("action") or "").strip()
    if action:
        query = query.filter(AuditEvent.action == action)
    actor = (request.args.get("actor") or "").strip()
    if actor:
        query = query.filter(AuditEvent.actor_sub == actor)

    events = query.order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    events = events.limit(limit).all()

    return (
        jsonify(
            {
                "success": True,
                "events": [event.to_dict() for event in events],
                "total": len(events),
            }
        ),
        200,
    )


if __name__ == "__main__":  # pragma: no cover - convenience for `python -m src.api`
    app.run(host="127.0.0.1", port=5000)
