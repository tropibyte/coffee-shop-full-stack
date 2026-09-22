"""Persistence layer for the Coffee Shop API.

Two entities live here:

``Drink``
    The menu item.  Its public representations, :meth:`Drink.short` and
    :meth:`Drink.long`, are byte-for-byte compatible with the shapes the
    Ionic frontend and the Postman collection expect, so they are treated as
    a frozen contract and extended only through additional methods.

``AuditEvent``
    An append-only record of every state-changing or privileged action.  The
    drinks table alone cannot answer "who deleted the Udaci-Spice Latte?",
    and an authorisation system that cannot be audited is only half built.

Two deliberate departures from the starter schema are documented inline: the
recipe column is widened to ``Text``, and recipe contents are validated before
they are ever persisted.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Column, DateTime, Integer, String, Text

database_filename = "database.db"
project_dir = os.path.dirname(os.path.abspath(__file__))
database_path = "sqlite:///{}".format(os.path.join(project_dir, database_filename))

db = SQLAlchemy()


class RecipeValidationError(ValueError):
    """Raised when a submitted recipe is not a well-formed ingredient list."""


# ---------------------------------------------------------------------------
# Recipe validation
# ---------------------------------------------------------------------------
#
# The frontend renders each ingredient as a coloured band by interpolating the
# `color` value straight into a style binding.  Persisting an arbitrary string
# there would let a manager store CSS -- or a `url(...)` callback -- that every
# later visitor executes.  Colours are therefore constrained at the boundary to
# hex triples/quads and the CSS named colours, which is all the UI can draw
# anyway.  Validation lives in the model so that no route can bypass it.

_HEX_COLOR = re.compile(
    r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$"
)
_RGB_COLOR = re.compile(
    r"^rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*"
    r"(?:,\s*(?:0|1|0?\.\d+)\s*)?\)$"
)
_CSS_NAMED_COLOR = re.compile(r"^[a-zA-Z]{3,20}$")

MAX_TITLE_LENGTH = 80
MAX_INGREDIENTS = 20
MAX_INGREDIENT_NAME_LENGTH = 60
MAX_RECIPE_JSON_LENGTH = 4000


def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp, so stored times are never ambiguous."""
    return datetime.now(timezone.utc)


def validate_title(title: Any) -> str:
    """Return a cleaned drink title or raise :class:`RecipeValidationError`."""
    if not isinstance(title, str):
        raise RecipeValidationError("title must be a string")
    cleaned = title.strip()
    if not cleaned:
        raise RecipeValidationError("title must not be empty")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise RecipeValidationError(
            "title must be {0} characters or fewer".format(MAX_TITLE_LENGTH)
        )
    return cleaned


def validate_color(color: Any) -> str:
    """Return a safe CSS colour or raise :class:`RecipeValidationError`."""
    if not isinstance(color, str):
        raise RecipeValidationError("ingredient color must be a string")
    cleaned = color.strip()
    if not cleaned:
        raise RecipeValidationError("ingredient color must not be empty")
    if (
        _HEX_COLOR.match(cleaned)
        or _RGB_COLOR.match(cleaned)
        or _CSS_NAMED_COLOR.match(cleaned)
    ):
        return cleaned
    raise RecipeValidationError(
        "ingredient color must be a hex value (#0af / #00aaff), an rgb()/rgba() "
        "value, or a CSS colour name; got {0!r}".format(color)
    )


def _validate_ingredient_name(index: int, name: Any) -> str:
    """Return a cleaned ingredient name or raise."""
    if not isinstance(name, str) or not name.strip():
        raise RecipeValidationError(
            "recipe[{0}].name must be a non-empty string".format(index)
        )
    cleaned = name.strip()
    if len(cleaned) > MAX_INGREDIENT_NAME_LENGTH:
        raise RecipeValidationError(
            "recipe[{0}].name must be {1} characters or fewer".format(
                index, MAX_INGREDIENT_NAME_LENGTH
            )
        )
    return cleaned


def _validate_ingredient_parts(index: int, parts: Any) -> Any:
    """Return a validated ``parts`` value or raise."""
    # bool is a subclass of int; True would otherwise sail through as 1.
    if isinstance(parts, bool) or not isinstance(parts, (int, float)):
        raise RecipeValidationError("recipe[{0}].parts must be a number".format(index))
    if parts <= 0:
        raise RecipeValidationError(
            "recipe[{0}].parts must be greater than zero".format(index)
        )
    if parts > 1000:
        raise RecipeValidationError(
            "recipe[{0}].parts must be 1000 or less".format(index)
        )
    return parts


def _validate_ingredient(index: int, ingredient: Any) -> Dict[str, Any]:
    """Return one normalised ingredient, or raise explaining which is wrong.

    Errors name the offending index so that a client editing a five-ingredient
    recipe is told which row to fix rather than that "the recipe" is invalid.
    """
    if not isinstance(ingredient, dict):
        raise RecipeValidationError(
            "recipe[{0}] must be an object with name, color and parts".format(index)
        )

    missing = [key for key in ("name", "color", "parts") if key not in ingredient]
    if missing:
        raise RecipeValidationError(
            "recipe[{0}] is missing: {1}".format(index, ", ".join(missing))
        )

    # Only these three keys survive.  Anything else a client sends is dropped
    # rather than stored and echoed back to some later reader.
    return {
        "name": _validate_ingredient_name(index, ingredient["name"]),
        "color": validate_color(ingredient["color"]),
        "parts": _validate_ingredient_parts(index, ingredient["parts"]),
    }


def _decode_recipe(recipe: Any) -> Any:
    """Accept the several shapes clients send and return a list."""
    if isinstance(recipe, str):
        # Some clients send the recipe pre-serialised.  Decode rather than
        # storing a JSON string nested inside a JSON string.
        try:
            recipe = json.loads(recipe)
        except json.JSONDecodeError as exc:
            raise RecipeValidationError("recipe must be valid JSON") from exc

    if isinstance(recipe, dict):
        return [recipe]
    return recipe


def validate_recipe(recipe: Any) -> List[Dict[str, Any]]:
    """Normalise a recipe into the canonical ingredient-list form.

    Accepts either a list of ingredients or a single ingredient object, since
    the Ionic frontend has historically sent both.  Returns a new list; the
    caller's object is never mutated.
    """
    recipe = _decode_recipe(recipe)

    if not isinstance(recipe, list):
        raise RecipeValidationError("recipe must be a list of ingredient objects")
    if not recipe:
        raise RecipeValidationError("recipe must contain at least one ingredient")
    if len(recipe) > MAX_INGREDIENTS:
        raise RecipeValidationError(
            "recipe must contain {0} ingredients or fewer".format(MAX_INGREDIENTS)
        )

    normalised = [
        _validate_ingredient(index, ingredient)
        for index, ingredient in enumerate(recipe)
    ]

    serialised = json.dumps(normalised)
    if len(serialised) > MAX_RECIPE_JSON_LENGTH:
        raise RecipeValidationError(
            "recipe is too large once serialised ({0} bytes, limit {1})".format(
                len(serialised), MAX_RECIPE_JSON_LENGTH
            )
        )
    return normalised


# ---------------------------------------------------------------------------
# Database wiring
# ---------------------------------------------------------------------------


def setup_db(app) -> None:
    """Bind a Flask application to the SQLAlchemy service.

    ``SQLALCHEMY_DATABASE_URI`` is honoured when the application has already
    been configured, which is how tests reach an in-memory database and how
    Azure App Service points the file at persistent storage.  The starter's
    hard-coded path remains the fallback.
    """
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_path)
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    db.init_app(app)


def db_drop_and_create_all() -> None:
    """Drop every table and rebuild the schema from scratch.

    Destructive by design and used only for a clean start.  Requires an active
    application context, which :func:`seed_demo_drinks` also needs.
    """
    db.drop_all()
    db.create_all()
    seed_demo_drinks()


DEMO_DRINKS = (
    ("water", [{"name": "water", "color": "blue", "parts": 1}]),
    (
        "Udaci-Spice Latte",
        [
            {"name": "blue foam", "color": "#2ec4f1", "parts": 1},
            {"name": "espresso", "color": "#4b2e1e", "parts": 2},
            {"name": "steamed milk", "color": "#f4e6cd", "parts": 3},
        ],
    ),
    (
        "Matcha Cloud",
        [
            {"name": "matcha", "color": "#6f9b4a", "parts": 2},
            {"name": "oat milk", "color": "#efe2c8", "parts": 3},
        ],
    ),
)


def seed_demo_drinks() -> int:
    """Insert the demo drinks, skipping any title that already exists.

    Returns the number of rows actually inserted.  Idempotent, so a restart on
    a platform with ephemeral storage always comes back with a usable menu
    while a restart on persistent storage changes nothing.
    """
    inserted = 0
    for title, recipe in DEMO_DRINKS:
        existing = db.session.query(Drink).filter(Drink.title == title).one_or_none()
        if existing is not None:
            continue
        drink = Drink(title=title, recipe=json.dumps(recipe))
        db.session.add(drink)
        inserted += 1
    if inserted:
        db.session.commit()
    return inserted


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Drink(db.Model):
    """A persistent drink entity."""

    __tablename__ = "drink"

    # Autoincrementing, unique primary key
    id = Column(Integer().with_variant(Integer, "sqlite"), primary_key=True)
    # String title, unique across the menu
    title = Column(String(MAX_TITLE_LENGTH), unique=True, nullable=False)
    # The ingredient blob: a JSON array of
    # {'color': string, 'name': string, 'parts': number}.
    #
    # The starter declared this String(180).  Three ingredients with readable
    # names serialise past 180 characters, and SQLite does not enforce the
    # limit, so the overflow would only surface after a move to Postgres or
    # MySQL.  Text removes the trap; MAX_RECIPE_JSON_LENGTH bounds it instead.
    recipe = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    # -- Representations ----------------------------------------------------

    def short(self) -> Dict[str, Any]:
        """Public representation: colours and proportions, no ingredients.

        This is what an unauthenticated visitor sees.  Withholding `name` is
        the whole point of the endpoint, so the omission is load-bearing.
        """
        short_recipe = [
            {"color": r["color"], "parts": r["parts"]} for r in self.recipe_json()
        ]
        return {"id": self.id, "title": self.title, "recipe": short_recipe}

    def long(self) -> Dict[str, Any]:
        """Full representation, including ingredient names."""
        return {"id": self.id, "title": self.title, "recipe": self.recipe_json()}

    def long_detailed(self) -> Dict[str, Any]:
        """:meth:`long` plus timestamps, for the extended admin views.

        Kept separate so that :meth:`long` stays exactly the shape the project
        specification and the Postman collection assert on.
        """
        payload = self.long()
        payload["created_at"] = _isoformat(self.created_at)
        payload["updated_at"] = _isoformat(self.updated_at)
        return payload

    def recipe_json(self) -> List[Dict[str, Any]]:
        """Decode the stored recipe, tolerating a legacy single-object blob."""
        decoded = json.loads(self.recipe)
        if isinstance(decoded, dict):
            return [decoded]
        return decoded

    # -- Persistence helpers ------------------------------------------------

    def insert(self) -> None:
        """Insert this model into the database."""
        db.session.add(self)
        db.session.commit()

    def delete(self) -> None:
        """Delete this model from the database."""
        db.session.delete(self)
        db.session.commit()

    def update(self) -> None:
        """Flush pending changes to this model."""
        db.session.commit()

    def __repr__(self) -> str:
        """Return the short representation, matching the starter's behaviour."""
        return json.dumps(self.short())


class AuditEvent(db.Model):
    """An append-only record of a privileged or state-changing request.

    Deliberately denormalised and free of foreign keys: an audit trail has to
    survive the deletion of whatever it describes.  There is no ``update`` or
    ``delete`` helper, because rows here are not meant to be rewritten.
    """

    __tablename__ = "audit_event"

    id = Column(Integer, primary_key=True)
    # ISO-8601 UTC, recorded server-side and never taken from the client.
    occurred_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )
    # Auth0 `sub` claim, e.g. auth0|65f... or the literal 'anonymous'.
    actor_sub = Column(String(128), nullable=False, index=True)
    # The permission that authorised the action, e.g. 'delete:drinks'.
    permission = Column(String(64), nullable=True)
    # Verb on the resource: created, updated, deleted, role-assigned, ...
    action = Column(String(64), nullable=False, index=True)
    resource_type = Column(String(64), nullable=False)
    resource_id = Column(String(128), nullable=True)
    # HTTP status the request ultimately returned.
    status_code = Column(Integer, nullable=True)
    # Correlates the audit row with the structured log line for the request.
    request_id = Column(String(64), nullable=True, index=True)
    # A short JSON blob of non-sensitive context.  Never tokens or secrets.
    detail = Column(Text, nullable=True)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise for the audit-trail endpoint."""
        return {
            "id": self.id,
            "occurred_at": _isoformat(self.occurred_at),
            "actor_sub": self.actor_sub,
            "permission": self.permission,
            "action": self.action,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "status_code": self.status_code,
            "request_id": self.request_id,
            "detail": json.loads(self.detail) if self.detail else None,
        }

    def __repr__(self) -> str:
        """Return a compact, log-friendly summary."""
        return "<AuditEvent {0} {1} {2} by {3}>".format(
            self.id, self.action, self.resource_type, self.actor_sub
        )


def _isoformat(value: Optional[datetime]) -> Optional[str]:
    """Render a datetime as ISO-8601 UTC, tolerating naive values.

    SQLite hands back naive datetimes even for ``DateTime(timezone=True)``
    columns, so the UTC marker is reattached on the way out.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()
