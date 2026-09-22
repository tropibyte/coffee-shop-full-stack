"""Self-documenting API: an OpenAPI 3.0 document and a Swagger UI page.

``GET /openapi.json`` serves the machine-readable contract; ``GET /docs``
serves a browsable, try-it-out console wired to the tenant's own Auth0
authorisation endpoint, so a reviewer can obtain a token and exercise every
endpoint without leaving the page.

The Auth0 domain and audience are injected from configuration at request
time, which keeps the document correct across local, staging and production
without a rebuild.
"""

from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, Response, current_app, jsonify

docs_bp = Blueprint("docs", __name__)


def _error_schema(description: str) -> Dict[str, Any]:
    """Build the reusable error response object."""
    return {
        "description": description,
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/Error"},
            }
        },
    }


def build_spec() -> Dict[str, Any]:
    """Assemble the OpenAPI document for the current configuration."""
    domain = current_app.config.get("AUTH0_DOMAIN", "")
    audience = current_app.config.get("AUTH0_API_AUDIENCE", "")

    drink_short = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "example": 1},
            "title": {"type": "string", "example": "Udaci-Spice Latte"},
            "recipe": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "color": {"type": "string", "example": "#2ec4f1"},
                        "parts": {"type": "number", "example": 1},
                    },
                },
            },
        },
    }
    drink_long = {
        "type": "object",
        "properties": {
            "id": {"type": "integer", "example": 1},
            "title": {"type": "string", "example": "Udaci-Spice Latte"},
            "recipe": {
                "type": "array",
                "items": {"$ref": "#/components/schemas/Ingredient"},
            },
        },
    }

    def drinks_response(schema_ref: str, description: str) -> Dict[str, Any]:
        return {
            "description": description,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean", "example": True},
                            "drinks": {
                                "type": "array",
                                "items": {"$ref": schema_ref},
                            },
                        },
                    }
                }
            },
        }

    list_params = [
        {
            "name": "search",
            "in": "query",
            "schema": {"type": "string"},
            "description": "Case-insensitive substring match on the title.",
        },
        {
            "name": "sort",
            "in": "query",
            "schema": {
                "type": "string",
                "enum": ["id", "title", "created_at"],
                "default": "id",
            },
        },
        {
            "name": "order",
            "in": "query",
            "schema": {"type": "string", "enum": ["asc", "desc"], "default": "asc"},
        },
        {
            "name": "page",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1},
            "description": "Supplying page or per_page turns on pagination.",
        },
        {
            "name": "per_page",
            "in": "query",
            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
        },
    ]

    drink_id_param = [
        {
            "name": "drink_id",
            "in": "path",
            "required": True,
            "schema": {"type": "integer"},
        }
    ]

    user_id_param = [
        {
            "name": "user_id",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
            "description": "Auth0 user id, e.g. auth0|65f3c1a2b4d5e6f7a8b9c0d1",
        }
    ]

    common_errors = {
        "400": _error_schema("Malformed request."),
        "401": _error_schema("Missing, malformed or expired token."),
        "403": _error_schema("Authenticated, but the permission is absent."),
        "404": _error_schema("No such resource."),
        "422": _error_schema("Well-formed but unprocessable."),
        "429": _error_schema("Rate limit exceeded."),
    }

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Coffee Shop API",
            "version": "1.0.0",
            "description": (
                "Drink menu and user administration for the Udacity Coffee "
                "Shop, secured with Auth0 role-based access control.\n\n"
                "**Roles**\n\n"
                "| Role | Menu | Recipes | Edit menu | Manage users |\n"
                "|---|---|---|---|---|\n"
                "| Public | yes | no | no | no |\n"
                "| Barista | yes | yes | no | no |\n"
                "| Manager | yes | yes | yes | baristas |\n"
                "| Administrator | yes | yes | yes | baristas + managers |\n"
            ),
            "license": {"name": "MIT"},
        },
        "servers": [
            {"url": "/", "description": "This deployment"},
        ],
        "tags": [
            {"name": "drinks", "description": "The menu."},
            {"name": "users", "description": "Auth0-backed user administration."},
            {"name": "audit", "description": "Who did what, and when."},
            {"name": "health", "description": "Probes and introspection."},
        ],
        "components": {
            "securitySchemes": {
                "auth0": {
                    "type": "oauth2",
                    "description": (
                        "Auth0 implicit flow. Authorise here to try the "
                        "protected endpoints with a real token."
                    ),
                    "flows": {
                        "implicit": {
                            "authorizationUrl": (
                                "https://{0}/authorize?audience={1}".format(
                                    domain, audience
                                )
                            ),
                            "scopes": {
                                "get:drinks-detail": "Read full recipes",
                                "post:drinks": "Create drinks",
                                "patch:drinks": "Edit drinks",
                                "delete:drinks": "Delete drinks",
                                "get:users": "Read users",
                                "post:users": "Create users",
                                "patch:users": "Edit users",
                                "delete:users": "Delete users",
                                "get:roles": "Read assignable roles",
                                "get:audit": "Read the audit trail",
                            },
                        }
                    },
                },
                "bearerAuth": {
                    "type": "http",
                    "scheme": "bearer",
                    "bearerFormat": "JWT",
                    "description": "Paste an Auth0 access token directly.",
                },
            },
            "schemas": {
                "Ingredient": {
                    "type": "object",
                    "required": ["name", "color", "parts"],
                    "properties": {
                        "name": {"type": "string", "example": "blue foam"},
                        "color": {
                            "type": "string",
                            "example": "#2ec4f1",
                            "description": (
                                "Hex, rgb()/rgba(), or a CSS colour name. "
                                "Anything else is rejected with 422."
                            ),
                        },
                        "parts": {"type": "number", "minimum": 0, "example": 1},
                    },
                },
                "DrinkShort": drink_short,
                "DrinkLong": drink_long,
                "Error": {
                    "type": "object",
                    "properties": {
                        "success": {"type": "boolean", "example": False},
                        "error": {"type": "integer", "example": 403},
                        "message": {"type": "string"},
                        "code": {
                            "type": "string",
                            "example": "unauthorized",
                            "description": "Present on authorisation failures.",
                        },
                        "request_id": {"type": "string"},
                    },
                },
            },
        },
        "paths": {
            "/drinks": {
                "get": {
                    "tags": ["drinks"],
                    "summary": "List drinks (public)",
                    "parameters": list_params,
                    "responses": {
                        "200": drinks_response(
                            "#/components/schemas/DrinkShort",
                            "The menu in short form.",
                        ),
                        "400": common_errors["400"],
                    },
                },
                "post": {
                    "tags": ["drinks"],
                    "summary": "Create a drink",
                    "security": [{"auth0": ["post:drinks"]}, {"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "required": ["title", "recipe"],
                                    "properties": {
                                        "title": {"type": "string"},
                                        "recipe": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Ingredient"
                                            },
                                        },
                                    },
                                },
                                "example": {
                                    "title": "Udaci-Spice Latte",
                                    "recipe": [
                                        {
                                            "name": "blue foam",
                                            "color": "#2ec4f1",
                                            "parts": 1,
                                        },
                                        {
                                            "name": "espresso",
                                            "color": "#4b2e1e",
                                            "parts": 2,
                                        },
                                    ],
                                },
                            }
                        },
                    },
                    "responses": {
                        "200": drinks_response(
                            "#/components/schemas/DrinkLong", "The created drink."
                        ),
                        "401": common_errors["401"],
                        "403": common_errors["403"],
                        "409": _error_schema("A drink with that title exists."),
                        "422": common_errors["422"],
                        "429": common_errors["429"],
                    },
                },
            },
            "/drinks/{drink_id}": {
                "get": {
                    "tags": ["drinks"],
                    "summary": "Get one drink (public)",
                    "parameters": drink_id_param,
                    "responses": {
                        "200": drinks_response(
                            "#/components/schemas/DrinkShort", "The drink."
                        ),
                        "404": common_errors["404"],
                    },
                },
                "patch": {
                    "tags": ["drinks"],
                    "summary": "Update a drink",
                    "security": [{"auth0": ["patch:drinks"]}, {"bearerAuth": []}],
                    "parameters": drink_id_param,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "recipe": {
                                            "type": "array",
                                            "items": {
                                                "$ref": "#/components/schemas/Ingredient"
                                            },
                                        },
                                    },
                                },
                                "example": {"title": "Udaci-Spice Latte v2"},
                            }
                        },
                    },
                    "responses": {
                        "200": drinks_response(
                            "#/components/schemas/DrinkLong", "The updated drink."
                        ),
                        "401": common_errors["401"],
                        "403": common_errors["403"],
                        "404": common_errors["404"],
                        "422": common_errors["422"],
                    },
                },
                "delete": {
                    "tags": ["drinks"],
                    "summary": "Delete a drink",
                    "security": [{"auth0": ["delete:drinks"]}, {"bearerAuth": []}],
                    "parameters": drink_id_param,
                    "responses": {
                        "200": {
                            "description": "Deleted.",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "success": {"type": "boolean"},
                                            "delete": {"type": "integer"},
                                        },
                                    }
                                }
                            },
                        },
                        "401": common_errors["401"],
                        "403": common_errors["403"],
                        "404": common_errors["404"],
                    },
                },
            },
            "/drinks-detail": {
                "get": {
                    "tags": ["drinks"],
                    "summary": "List drinks with full recipes",
                    "security": [
                        {"auth0": ["get:drinks-detail"]},
                        {"bearerAuth": []},
                    ],
                    "parameters": list_params,
                    "responses": {
                        "200": drinks_response(
                            "#/components/schemas/DrinkLong",
                            "The menu in long form.",
                        ),
                        "401": common_errors["401"],
                        "403": common_errors["403"],
                    },
                }
            },
            "/users": {
                "get": {
                    "tags": ["users"],
                    "summary": "List users junior to the caller",
                    "security": [{"auth0": ["get:users"]}, {"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "page",
                            "in": "query",
                            "schema": {"type": "integer", "minimum": 0},
                        },
                        {
                            "name": "per_page",
                            "in": "query",
                            "schema": {"type": "integer", "minimum": 1, "maximum": 100},
                        },
                        {
                            "name": "q",
                            "in": "query",
                            "schema": {"type": "string"},
                            "description": "Auth0 Lucene query, e.g. email:*@example.com",
                        },
                    ],
                    "responses": {
                        "200": {"description": "Visible users."},
                        "401": common_errors["401"],
                        "403": common_errors["403"],
                    },
                },
                "post": {
                    "tags": ["users"],
                    "summary": "Invite a user and grant a junior role",
                    "description": (
                        "No password is accepted or returned. The response "
                        "carries a one-time Auth0 ticket with which the new "
                        "user sets their own password."
                    ),
                    "security": [{"auth0": ["post:users"]}, {"bearerAuth": []}],
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "example": {
                                    "email": "new.barista@example.com",
                                    "name": "New Barista",
                                    "role": "Barista",
                                }
                            }
                        },
                    },
                    "responses": {
                        "201": {"description": "Created."},
                        "403": _error_schema(
                            "Granting a role at or above your own rank."
                        ),
                        "409": _error_schema("That email already exists."),
                        "422": common_errors["422"],
                    },
                },
            },
            "/users/{user_id}": {
                "get": {
                    "tags": ["users"],
                    "summary": "Get one junior user",
                    "security": [{"auth0": ["get:users"]}, {"bearerAuth": []}],
                    "parameters": user_id_param,
                    "responses": {
                        "200": {"description": "The user."},
                        "404": common_errors["404"],
                    },
                },
                "patch": {
                    "tags": ["users"],
                    "summary": "Change a junior user's role, name or block state",
                    "security": [{"auth0": ["patch:users"]}, {"bearerAuth": []}],
                    "parameters": user_id_param,
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "example": {"role": "Barista", "blocked": False}
                            }
                        },
                    },
                    "responses": {
                        "200": {"description": "The updated user."},
                        "403": _error_schema(
                            "Acting on yourself, a peer, or a senior account."
                        ),
                        "404": common_errors["404"],
                    },
                },
                "delete": {
                    "tags": ["users"],
                    "summary": "Delete a junior user",
                    "security": [{"auth0": ["delete:users"]}, {"bearerAuth": []}],
                    "parameters": user_id_param,
                    "responses": {
                        "200": {"description": "Deleted."},
                        "403": _error_schema("Target is not junior to you."),
                        "404": common_errors["404"],
                    },
                },
            },
            "/users/me": {
                "get": {
                    "tags": ["users"],
                    "summary": "The caller's own identity, roles and permissions",
                    "security": [{"auth0": []}, {"bearerAuth": []}],
                    "responses": {"200": {"description": "You."}},
                }
            },
            "/roles": {
                "get": {
                    "tags": ["users"],
                    "summary": "Roles the caller may grant",
                    "security": [{"auth0": ["get:roles"]}, {"bearerAuth": []}],
                    "responses": {"200": {"description": "Assignable roles."}},
                }
            },
            "/audit": {
                "get": {
                    "tags": ["audit"],
                    "summary": "Recent audit events, newest first",
                    "security": [{"auth0": ["get:audit"]}, {"bearerAuth": []}],
                    "parameters": [
                        {
                            "name": "limit",
                            "in": "query",
                            "schema": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 500,
                                "default": 50,
                            },
                        },
                        {
                            "name": "action",
                            "in": "query",
                            "schema": {"type": "string"},
                            "example": "drink.deleted",
                        },
                        {"name": "actor", "in": "query", "schema": {"type": "string"}},
                    ],
                    "responses": {
                        "200": {"description": "Audit events."},
                        "403": common_errors["403"],
                    },
                }
            },
            "/health": {
                "get": {
                    "tags": ["health"],
                    "summary": "Service summary",
                    "responses": {"200": {"description": "Healthy."}},
                }
            },
            "/health/ready": {
                "get": {
                    "tags": ["health"],
                    "summary": "Readiness probe",
                    "responses": {
                        "200": {"description": "Ready."},
                        "503": {"description": "Degraded."},
                    },
                }
            },
            "/health/rbac": {
                "get": {
                    "tags": ["health"],
                    "summary": "Live route-to-permission map",
                    "responses": {"200": {"description": "The RBAC matrix."}},
                }
            },
        },
    }


@docs_bp.route("/openapi.json", methods=["GET"])
def openapi_spec():
    """Serve the OpenAPI document for this deployment."""
    return jsonify(build_spec()), 200


SWAGGER_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Coffee Shop API</title>
<link rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.18.2/swagger-ui.css">
<style>
  body { margin: 0; background: #12100e; }
  .topbar { display: none; }
  #banner { font: 15px/1.5 -apple-system, "Segoe UI", system-ui, sans-serif;
            color: #f4e6cd; background: #3b2314; padding: 14px 20px; }
  #banner code { background: rgba(255,255,255,.12); padding: 1px 5px;
                 border-radius: 3px; }
  .swagger-ui { background: #fff; }
</style>
</head>
<body>
<div id="banner">
  <strong>Coffee Shop API.</strong>
  Click <em>Authorize</em> to sign in with Auth0, or paste an access token
  into <code>bearerAuth</code>. Public endpoints need neither.
</div>
<div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.18.2/swagger-ui-bundle.js"
        crossorigin="anonymous"></script>
<script>
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    persistAuthorization: true,
    presets: [SwaggerUIBundle.presets.apis],
    layout: "BaseLayout"
  });
</script>
</body>
</html>
"""


@docs_bp.route("/docs", methods=["GET"])
def swagger_ui():
    """Serve a Swagger UI console bound to this deployment's spec."""
    response = Response(SWAGGER_PAGE, mimetype="text/html")
    # The global policy is `default-src 'none'`, which would block the CDN
    # assets this page needs.  Set a narrower policy here instead of loosening
    # the default that protects every JSON response.
    response.headers["Content-Security-Policy"] = (
        "default-src 'none'; "
        "script-src 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'unsafe-inline' https://cdn.jsdelivr.net; "
        "img-src 'self' data: https:; "
        "connect-src 'self' https:; "
        "font-src https://cdn.jsdelivr.net data:; "
        "frame-ancestors 'none'; base-uri 'none'"
    )
    return response
