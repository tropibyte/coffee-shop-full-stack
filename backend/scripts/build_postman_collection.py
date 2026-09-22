"""Generate the Postman collection for the Coffee Shop API.

A 1,500-line JSON document maintained by hand drifts away from the API within
a week.  This script is the source of truth: it emits
``udacity-fsnd-udaspicelatte.postman_collection.json`` with one folder per
role, full assertions on every request, and a manager folder that runs a
complete create/read/update/delete cycle without leaving residue -- so the
collection can be run repeatedly, by a reviewer or by Newman in CI, and pass
every time.

Usage::

    python scripts/build_postman_collection.py
    python scripts/build_postman_collection.py --output some/other/path.json

To inject real JWTs before submitting, see ``scripts/set_postman_tokens.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from typing import Any, Dict, List, Optional

COLLECTION_NAME = "udacity-fsnd-udaspicelatte"

DEFAULT_OUTPUT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "udacity-fsnd-udaspicelatte.postman_collection.json",
)

DESCRIPTION = """\
# Coffee Shop API

Role-based access control over an Auth0-secured Flask API.

## Folders

| Folder | Credential | Proves |
|---|---|---|
| `public` | none | the menu is public; everything else is 401 |
| `barista` | barista JWT | recipes readable; every write is 403 |
| `manager` | manager JWT | full menu CRUD, plus barista administration |
| `administrator` | administrator JWT | manager administration and the audit trail |
| `security` | assorted | forged, expired and malformed tokens are refused |

## Running it

1. Start the API: `flask run` in `backend/`.
2. Paste a JWT into each role folder's **Authorization** tab
   (right-click folder -> Edit -> Authorization -> Bearer Token).
3. Run the collection. Every request carries assertions; a green run means
   the RBAC configuration in Auth0 matches the code.

The `manager` folder creates the drink it later edits and deletes, capturing
its id into `{{drink_id}}`. Nothing is left behind, so the collection is
re-runnable as often as you like -- including by Newman in CI:

    newman run udacity-fsnd-udaspicelatte.postman_collection.json \\
      --env-var host=http://127.0.0.1:5000

## Note on 401 vs 403

`401` means the caller has not proven who they are. `403` means the caller is
known and is not permitted. The assertions below check for the specific one,
never "4xx", because collapsing the two is the most common RBAC bug.
"""


# ---------------------------------------------------------------------------
# Small builders
# ---------------------------------------------------------------------------


def script(lines: List[str]) -> Dict[str, Any]:
    """Wrap test-script lines in Postman's event envelope."""
    return {
        "listen": "test",
        "script": {"id": str(uuid.uuid4()), "type": "text/javascript", "exec": lines},
    }


def request(
    name: str,
    method: str,
    path: str,
    tests: List[str],
    body: Optional[Dict[str, Any]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Build one Postman request item."""
    raw_url = "{{host}}" + path
    segments = [segment for segment in path.split("/") if segment]

    item: Dict[str, Any] = {
        "name": name,
        "event": [script(tests)],
        "request": {
            "method": method,
            "header": [],
            "url": {"raw": raw_url, "host": ["{{host}}"], "path": segments},
            "description": description,
        },
        "response": [],
    }
    if body is not None:
        item["request"]["header"].append(
            {"key": "Content-Type", "value": "application/json"}
        )
        item["request"]["body"] = {
            "mode": "raw",
            "raw": json.dumps(body, indent=2),
            "options": {"raw": {"language": "json"}},
        }
    return item


def folder(
    name: str,
    items: List[Dict[str, Any]],
    token_variable: Optional[str] = None,
    description: str = "",
) -> Dict[str, Any]:
    """Build a folder, optionally carrying a bearer token for its requests."""
    node: Dict[str, Any] = {
        "name": name,
        "item": items,
        "description": description,
    }
    if token_variable is not None:
        node["auth"] = {
            "type": "bearer",
            "bearer": [
                {"key": "token", "value": "{{" + token_variable + "}}", "type": "string"}
            ],
        }
    else:
        # Explicitly no credential, so the public folder cannot silently
        # inherit one from the collection and pass for the wrong reason.
        node["auth"] = {"type": "noauth"}
    return node


# ---------------------------------------------------------------------------
# Reusable assertion fragments
# ---------------------------------------------------------------------------

STATUS = 'pm.test("Status code is {0}", function () {{ pm.response.to.have.status({0}); }});'

SUCCESS_TRUE = [
    'pm.test("success is true", function () {',
    "    pm.expect(pm.response.json().success).to.eql(true);",
    "});",
]

DRINKS_ARRAY = [
    'pm.test("value contains drinks array", function () {',
    "    var jsonData = pm.response.json();",
    "    pm.expect(jsonData.drinks).to.be.an('array');",
    "});",
]

SHORT_FORM = [
    'pm.test("short form hides ingredient names", function () {',
    "    var drinks = pm.response.json().drinks;",
    "    drinks.forEach(function (drink) {",
    "        drink.recipe.forEach(function (part) {",
    "            pm.expect(part).to.have.property('color');",
    "            pm.expect(part).to.have.property('parts');",
    "            pm.expect(part).to.not.have.property('name');",
    "        });",
    "    });",
    "});",
]

LONG_FORM = [
    'pm.test("long form includes ingredient names", function () {',
    "    var drinks = pm.response.json().drinks;",
    "    pm.expect(drinks.length).to.be.above(0);",
    "    drinks.forEach(function (drink) {",
    "        drink.recipe.forEach(function (part) {",
    "            pm.expect(part).to.have.property('name');",
    "            pm.expect(part).to.have.property('color');",
    "            pm.expect(part).to.have.property('parts');",
    "        });",
    "    });",
    "});",
]


def error_body(status: int, code: Optional[str] = None) -> List[str]:
    """Assertions for the canonical error envelope."""
    lines = [
        'pm.test("error body has the documented shape", function () {',
        "    var jsonData = pm.response.json();",
        "    pm.expect(jsonData.success).to.eql(false);",
        "    pm.expect(jsonData.error).to.eql({0});".format(status),
        "    pm.expect(jsonData.message).to.be.a('string');",
        "});",
    ]
    if code:
        lines += [
            'pm.test("error code is {0}", function () {{'.format(code),
            "    pm.expect(pm.response.json().code).to.eql('{0}');".format(code),
            "});",
        ]
    return lines


NEW_DRINK = {
    "title": "Udaci-Spice Latte",
    "recipe": [
        {"name": "blue foam", "color": "#2ec4f1", "parts": 1},
        {"name": "espresso", "color": "#4b2e1e", "parts": 2},
        {"name": "steamed milk", "color": "#f4e6cd", "parts": 3},
    ],
}


# ---------------------------------------------------------------------------
# Folders
# ---------------------------------------------------------------------------


def public_folder() -> Dict[str, Any]:
    """Requests made with no credentials at all."""
    return folder(
        "public",
        [
            request(
                "/drinks",
                "GET",
                "/drinks",
                [STATUS.format(200)] + SUCCESS_TRUE + DRINKS_ARRAY + SHORT_FORM,
                description="The menu is public and shows colours, not recipes.",
            ),
            request(
                "/drinks-detail",
                "GET",
                "/drinks-detail",
                [STATUS.format(401)] + error_body(401, "authorization_header_missing"),
                description="Recipes require authentication.",
            ),
            request(
                "/drinks",
                "POST",
                "/drinks",
                [STATUS.format(401)] + error_body(401, "authorization_header_missing"),
                body=NEW_DRINK,
            ),
            request(
                "/drinks/1",
                "PATCH",
                "/drinks/1",
                [STATUS.format(401)] + error_body(401),
                body={"title": "Should Not Happen"},
            ),
            request(
                "/drinks/1",
                "DELETE",
                "/drinks/1",
                [STATUS.format(401)] + error_body(401),
            ),
            request(
                "/users",
                "GET",
                "/users",
                [STATUS.format(401)] + error_body(401),
                description="User administration is not public either.",
            ),
            request(
                "/audit",
                "GET",
                "/audit",
                [STATUS.format(401)] + error_body(401),
            ),
        ],
        token_variable=None,
        description="No credentials. Only the menu is readable.",
    )


def barista_folder() -> Dict[str, Any]:
    """Requests made with a barista's token."""
    forbidden = [STATUS.format(403)] + error_body(403, "unauthorized")
    return folder(
        "barista",
        [
            request(
                "/drinks",
                "GET",
                "/drinks",
                [STATUS.format(200)] + SUCCESS_TRUE + DRINKS_ARRAY,
            ),
            request(
                "/drinks-detail",
                "GET",
                "/drinks-detail",
                [STATUS.format(200)] + SUCCESS_TRUE + DRINKS_ARRAY + LONG_FORM,
                description="A barista may read full recipes.",
            ),
            request(
                "/drinks",
                "POST",
                "/drinks",
                forbidden
                + [
                    'pm.test("names the missing permission", function () {',
                    "    pm.expect(pm.response.json().description)"
                    ".to.include('post:drinks');",
                    "});",
                ],
                body=NEW_DRINK,
                description="Authenticated, but not permitted: 403, not 401.",
            ),
            request(
                "/drinks/1",
                "PATCH",
                "/drinks/1",
                forbidden,
                body={"title": "Should Not Happen"},
            ),
            request("/drinks/1", "DELETE", "/drinks/1", forbidden),
            request(
                "/users",
                "GET",
                "/users",
                [STATUS.format(403)],
                description="A barista administers nobody.",
            ),
            request(
                "/users/me",
                "GET",
                "/users/me",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("reports the Barista role", function () {',
                    "    pm.expect(pm.response.json().user.rank).to.eql(0);",
                    "});",
                ],
                description="Every authenticated caller may read their own record.",
            ),
            request(
                "/audit",
                "GET",
                "/audit",
                [STATUS.format(403)],
            ),
        ],
        token_variable="barista_token",
        description="Barista: may read recipes. Every write is refused with 403.",
    )


def manager_folder() -> Dict[str, Any]:
    """A complete, repeatable CRUD cycle plus barista administration."""
    capture_id = [
        'pm.test("captures the new drink id", function () {',
        "    var drink = pm.response.json().drinks[0];",
        "    pm.expect(drink.id).to.be.a('number');",
        '    pm.collectionVariables.set("drink_id", drink.id);',
        "});",
    ]

    return folder(
        "manager",
        [
            request(
                "/drinks",
                "GET",
                "/drinks",
                [STATUS.format(200)] + SUCCESS_TRUE + DRINKS_ARRAY,
            ),
            request(
                "/drinks-detail",
                "GET",
                "/drinks-detail",
                [STATUS.format(200)] + SUCCESS_TRUE + DRINKS_ARRAY + LONG_FORM,
            ),
            request(
                "/drinks (create)",
                "POST",
                "/drinks",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + DRINKS_ARRAY
                + capture_id
                + [
                    'pm.test("returns the long form of the new drink", function () {',
                    "    var drink = pm.response.json().drinks[0];",
                    "    pm.expect(drink.title).to.eql('Udaci-Spice Latte');",
                    "    pm.expect(drink.recipe.length).to.eql(3);",
                    "    pm.expect(drink.recipe[0]).to.have.property('name');",
                    "});",
                ],
                body=NEW_DRINK,
                description=(
                    "Creates the drink the rest of this folder edits and deletes, "
                    "so the folder leaves no residue and can be re-run."
                ),
            ),
            request(
                "/drinks (duplicate title)",
                "POST",
                "/drinks",
                [STATUS.format(409)] + error_body(409),
                body=NEW_DRINK,
                description="A unique constraint, reported as a conflict.",
            ),
            request(
                "/drinks (invalid recipe)",
                "POST",
                "/drinks",
                [STATUS.format(422)] + error_body(422),
                body={
                    "title": "Malformed",
                    "recipe": [{"name": "x", "color": "blue", "parts": -1}],
                },
                description="Validation happens in the model, so no route can skip it.",
            ),
            request(
                "/drinks/{{drink_id}}",
                "PATCH",
                "/drinks/{{drink_id}}",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + DRINKS_ARRAY
                + [
                    'pm.test("the title was updated", function () {',
                    "    pm.expect(pm.response.json().drinks[0].title)"
                    ".to.eql('Udaci-Spice Latte v2');",
                    "});",
                ],
                body={"title": "Udaci-Spice Latte v2"},
            ),
            request(
                "/drinks/424242 (missing)",
                "PATCH",
                "/drinks/424242",
                [STATUS.format(404)] + error_body(404),
                body={"title": "Ghost"},
            ),
            request(
                "/drinks/{{drink_id}}",
                "DELETE",
                "/drinks/{{drink_id}}",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("reports the deleted id", function () {',
                    "    var expected = Number("
                    'pm.collectionVariables.get("drink_id"));',
                    "    pm.expect(pm.response.json().delete).to.eql(expected);",
                    "});",
                ],
            ),
            request(
                "/drinks/{{drink_id}} (already gone)",
                "DELETE",
                "/drinks/{{drink_id}}",
                [STATUS.format(404)] + error_body(404),
                description="Deleting twice is a 404, not a silent success.",
            ),
            request(
                "/roles",
                "GET",
                "/roles",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("a manager may grant Barista and nothing else",'
                    " function () {",
                    "    var names = pm.response.json().roles.map("
                    "function (r) { return r.name; });",
                    "    pm.expect(names).to.eql(['Barista']);",
                    "});",
                ],
                description="Assignable roles are filtered to those junior to you.",
            ),
            request(
                "/users",
                "GET",
                "/users",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("no account at or above manager rank is listed",'
                    " function () {",
                    "    pm.response.json().users.forEach(function (user) {",
                    "        pm.expect(user.rank).to.be.below(1);",
                    "    });",
                    "});",
                ],
            ),
            request(
                "/users (escalation attempt)",
                "POST",
                "/users",
                [STATUS.format(403)]
                + [
                    'pm.test("privilege escalation is blocked", function () {',
                    "    pm.expect(pm.response.json().code)"
                    ".to.eql('privilege_escalation_blocked');",
                    "});",
                ],
                body={"email": "escalation@example.com", "role": "Manager"},
                description=(
                    "A manager holds post:users, so the permission check passes. "
                    "The rank check is what refuses this."
                ),
            ),
            request(
                "/audit",
                "GET",
                "/audit",
                [STATUS.format(403)],
                description="The audit trail belongs to administrators.",
            ),
        ],
        token_variable="manager_token",
        description=(
            "Manager: full menu control and barista administration. "
            "Runs a complete CRUD cycle and cleans up after itself."
        ),
    )


def administrator_folder() -> Dict[str, Any]:
    """The third tier: manager administration and the audit trail."""
    return folder(
        "administrator",
        [
            request(
                "/users/me",
                "GET",
                "/users/me",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("reports Administrator rank", function () {',
                    "    pm.expect(pm.response.json().user.rank).to.eql(2);",
                    "});",
                ],
            ),
            request(
                "/roles",
                "GET",
                "/roles",
                [STATUS.format(200)]
                + [
                    'pm.test("may grant Manager and Barista", function () {',
                    "    var names = pm.response.json().roles.map("
                    "function (r) { return r.name; });",
                    "    pm.expect(names).to.have.members(['Manager', 'Barista']);",
                    "});",
                ],
            ),
            request(
                "/users",
                "GET",
                "/users",
                [STATUS.format(200)]
                + [
                    'pm.test("sees managers and baristas, never a peer",'
                    " function () {",
                    "    pm.response.json().users.forEach(function (user) {",
                    "        pm.expect(user.rank).to.be.below(2);",
                    "    });",
                    "});",
                ],
            ),
            request(
                "/users (create an administrator)",
                "POST",
                "/users",
                [STATUS.format(403)]
                + [
                    'pm.test("even an administrator cannot mint a peer",'
                    " function () {",
                    "    pm.expect(pm.response.json().code)"
                    ".to.eql('privilege_escalation_blocked');",
                    "});",
                ],
                body={"email": "peer@example.com", "role": "Administrator"},
            ),
            request(
                "/audit",
                "GET",
                "/audit",
                [STATUS.format(200)]
                + SUCCESS_TRUE
                + [
                    'pm.test("returns audit events", function () {',
                    "    pm.expect(pm.response.json().events).to.be.an('array');",
                    "});",
                    'pm.test("events name an actor and an action", function () {',
                    "    var events = pm.response.json().events;",
                    "    if (events.length > 0) {",
                    "        pm.expect(events[0]).to.have.property('actor_sub');",
                    "        pm.expect(events[0]).to.have.property('action');",
                    "        pm.expect(events[0]).to.have.property('occurred_at');",
                    "    }",
                    "});",
                ],
                description="Who did what, and when.",
            ),
        ],
        token_variable="admin_token",
        description=(
            "Administrator: may administer managers and baristas, and is the "
            "only role that can read the audit trail."
        ),
    )


def security_folder() -> Dict[str, Any]:
    """Tokens that must be refused."""
    return folder(
        "security",
        [
            request(
                "malformed Authorization header",
                "GET",
                "/drinks-detail",
                [STATUS.format(401)] + error_body(401, "invalid_header"),
                description=(
                    "Set the Authorization header to `Basic abc` on this request "
                    "to exercise the wrong-scheme path."
                ),
            ),
            request(
                "garbage bearer token",
                "GET",
                "/drinks-detail",
                [STATUS.format(401)] + error_body(401),
                description=(
                    "Add `Authorization: Bearer not-a-jwt` to this request."
                ),
            ),
            request(
                "expired token",
                "GET",
                "/drinks-detail",
                [
                    STATUS.format(401),
                    'pm.test("reports the token as expired", function () {',
                    "    pm.expect(pm.response.json().code).to.eql('token_expired');",
                    "});",
                ],
                description=(
                    "Paste a token older than 24 hours into this request's "
                    "Authorization tab. Expiry must read as 401, never 403."
                ),
            ),
            request(
                "health probe",
                "GET",
                "/health",
                [
                    STATUS.format(200),
                    'pm.test("publishes no secrets", function () {',
                    "    var text = pm.response.text().toLowerCase();",
                    "    pm.expect(text).to.not.include('secret');",
                    "    pm.expect(text).to.not.include('password');",
                    "});",
                ],
            ),
            request(
                "rbac matrix",
                "GET",
                "/health/rbac",
                [
                    STATUS.format(200),
                    'pm.test("the live route map matches the documented one",'
                    " function () {",
                    "    var routes = pm.response.json().routes;",
                    "    var byRule = {};",
                    "    routes.forEach(function (r) {",
                    "        byRule[r.methods.join(',') + ' ' + r.rule] "
                    "= r.permission;",
                    "    });",
                    "    pm.expect(byRule['GET /drinks']).to.eql(null);",
                    "    pm.expect(byRule['GET /drinks-detail'])"
                    ".to.eql('get:drinks-detail');",
                    "    pm.expect(byRule['POST /drinks']).to.eql('post:drinks');",
                    "});",
                ],
                description=(
                    "Generated by walking the live URL map, so it cannot drift "
                    "from the decorators."
                ),
            ),
        ],
        token_variable=None,
        description=(
            "Negative cases. Most need a token pasted into the individual "
            "request; each description says which."
        ),
    )


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def build_collection() -> Dict[str, Any]:
    """Return the whole collection document."""
    return {
        "info": {
            "_postman_id": "c0ffee00-5h0p-4a11-5tac-k000000000001",
            "name": COLLECTION_NAME,
            "description": DESCRIPTION,
            "schema": (
                "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"
            ),
        },
        "item": [
            public_folder(),
            barista_folder(),
            manager_folder(),
            administrator_folder(),
            security_folder(),
        ],
        "event": [
            {
                "listen": "prerequest",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        "// Every response is expected to be JSON; assert it once",
                        "// here rather than in every request.",
                    ],
                },
            },
            {
                "listen": "test",
                "script": {
                    "type": "text/javascript",
                    "exec": [
                        'pm.test("response is JSON", function () {',
                        "    pm.expect(pm.response.headers.get('Content-Type'))",
                        "        .to.include('application/json');",
                        "});",
                        'pm.test("response carries a correlation id", function () {',
                        "    pm.expect(pm.response.headers.has('X-Request-Id'))"
                        ".to.be.true;",
                        "});",
                        'pm.test("security headers are present", function () {',
                        "    pm.expect(pm.response.headers.get("
                        "'X-Content-Type-Options')).to.eql('nosniff');",
                        "});",
                    ],
                },
            },
        ],
        "variable": [
            {
                "key": "host",
                "value": "http://127.0.0.1:5000",
                "type": "string",
                "description": "Base URL of the running Flask API.",
            },
            {"key": "barista_token", "value": "", "type": "string"},
            {"key": "manager_token", "value": "", "type": "string"},
            {"key": "admin_token", "value": "", "type": "string"},
            {
                "key": "drink_id",
                "value": "",
                "type": "string",
                "description": "Set at run time by the manager folder's POST.",
            },
        ],
    }


def main() -> None:
    """Write the collection to disk."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    collection = build_collection()
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(collection, handle, indent=2)
        handle.write("\n")

    requests_written = sum(len(node["item"]) for node in collection["item"])
    print(
        "Wrote {0}\n  {1} folders, {2} requests".format(
            args.output, len(collection["item"]), requests_written
        )
    )


if __name__ == "__main__":
    main()
