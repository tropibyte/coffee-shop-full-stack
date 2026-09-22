"""The role-based access control matrix, asserted end to end.

This module encodes the access rules as data and then drives every cell of
the table through the real HTTP stack.  It is the executable counterpart of
the table in the README and of the Postman collection's three folders:

============  =====  =============  ====  =====  ======
Caller        menu   recipes        add   edit   delete
============  =====  =============  ====  =====  ======
public        200    401            401   401    401
barista       200    200            403   403    403
manager       200    200            200   200    200
admin         200    200            200   200    200
============  =====  =============  ====  =====  ======

The distinction the table turns on is 401 versus 403.  401 means "I do not
know who you are"; 403 means "I know exactly who you are, and no".  A service
that answers 401 to a barista's delete is telling them to go and log in
again, which they will do, successfully, to no effect.
"""

from __future__ import annotations

import pytest

from .conftest import ADMIN_PERMISSIONS, BARISTA_PERMISSIONS, MANAGER_PERMISSIONS

pytestmark = pytest.mark.rbac


#: (label, permissions, sub, roles) for each caller the matrix covers.
CALLERS = {
    "public": (None, None, None),
    "barista": (BARISTA_PERMISSIONS, "auth0|barista", ["Barista"]),
    "manager": (MANAGER_PERMISSIONS, "auth0|manager", ["Manager"]),
    "admin": (ADMIN_PERMISSIONS, "auth0|admin", ["Administrator"]),
}

#: (method, path, json body factory) for each protected drink operation.
OPERATIONS = {
    "list_menu": ("GET", "/drinks", None),
    "list_recipes": ("GET", "/drinks-detail", None),
    "create": (
        "POST",
        "/drinks",
        lambda: {
            "title": "RBAC Probe",
            "recipe": [{"name": "water", "color": "blue", "parts": 1}],
        },
    ),
    "update": ("PATCH", "/drinks/{id}", lambda: {"title": "RBAC Probe Renamed"}),
    "delete": ("DELETE", "/drinks/{id}", None),
    "audit": ("GET", "/audit", None),
}

#: The expected status for every (caller, operation) pair.
EXPECTED = {
    ("public", "list_menu"): 200,
    ("public", "list_recipes"): 401,
    ("public", "create"): 401,
    ("public", "update"): 401,
    ("public", "delete"): 401,
    ("public", "audit"): 401,
    ("barista", "list_menu"): 200,
    ("barista", "list_recipes"): 200,
    ("barista", "create"): 403,
    ("barista", "update"): 403,
    ("barista", "delete"): 403,
    ("barista", "audit"): 403,
    ("manager", "list_menu"): 200,
    ("manager", "list_recipes"): 200,
    ("manager", "create"): 200,
    ("manager", "update"): 200,
    ("manager", "delete"): 200,
    # Reading the audit trail is an administrator's job; a manager appears in
    # it and must not be able to read, or reason about, their own trail.
    ("manager", "audit"): 403,
    ("admin", "list_menu"): 200,
    ("admin", "list_recipes"): 200,
    ("admin", "create"): 200,
    ("admin", "update"): 200,
    ("admin", "delete"): 200,
    ("admin", "audit"): 200,
}


@pytest.mark.parametrize(
    ("caller", "operation"),
    sorted(EXPECTED),
    ids=["{0}-{1}".format(c, o) for c, o in sorted(EXPECTED)],
)
def test_rbac_matrix(client, auth_header, existing_drink, caller, operation):
    """Every cell of the access-control table behaves as documented."""
    permissions, sub, roles = CALLERS[caller]
    headers = (
        {} if permissions is None else auth_header(permissions, sub=sub, roles=roles)
    )

    method, path, body_factory = OPERATIONS[operation]
    path = path.format(id=existing_drink.id)
    body = body_factory() if body_factory else None

    response = client.open(path, method=method, headers=headers, json=body)

    assert (
        response.status_code == EXPECTED[(caller, operation)]
    ), "{0} calling {1} {2} returned {3}, body: {4}".format(
        caller, method, path, response.status_code, response.get_json()
    )


class TestAuthorizationSemantics:
    """401 and 403 must not be interchangeable."""

    def test_no_token_is_401_with_a_reason(self, client, existing_drink):
        response = client.delete("/drinks/{0}".format(existing_drink.id))
        body = response.get_json()
        assert response.status_code == 401
        assert body["success"] is False
        assert body["code"] == "authorization_header_missing"

    def test_valid_token_missing_permission_is_403(
        self, client, barista_headers, existing_drink
    ):
        response = client.delete(
            "/drinks/{0}".format(existing_drink.id), headers=barista_headers
        )
        body = response.get_json()
        assert response.status_code == 403
        assert body["code"] == "unauthorized"
        assert "delete:drinks" in body["description"]

    def test_expired_manager_token_is_401_not_403(
        self, client, auth_header, existing_drink
    ):
        headers = auth_header(MANAGER_PERMISSIONS, expires_in=-7200)
        response = client.delete(
            "/drinks/{0}".format(existing_drink.id), headers=headers
        )
        assert response.status_code == 401
        assert response.get_json()["code"] == "token_expired"

    def test_a_forbidden_request_changes_nothing(
        self, client, barista_headers, existing_drink, db_session
    ):
        from src.database.models import Drink

        drink_id = existing_drink.id
        client.delete("/drinks/{0}".format(drink_id), headers=barista_headers)
        client.patch(
            "/drinks/{0}".format(drink_id),
            headers=barista_headers,
            json={"title": "Should Not Happen"},
        )

        db_session.expire_all()
        survivor = db_session.get(Drink, drink_id)
        assert survivor is not None
        assert survivor.title == "Test Latte"

    def test_permissions_for_another_api_do_not_carry_over(
        self, client, auth_header, existing_drink
    ):
        # A token carrying a plausible-looking but different permission set
        # must not be able to talk its way in.
        headers = auth_header(["delete:everything", "admin", "*"])
        response = client.delete(
            "/drinks/{0}".format(existing_drink.id), headers=headers
        )
        assert response.status_code == 403

    def test_public_endpoint_ignores_a_broken_token(self, client, existing_drink):
        # GET /drinks is public.  A malformed Authorization header should not
        # make it fail: the endpoint never asked for one.
        response = client.get(
            "/drinks", headers={"Authorization": "Bearer not-a-token"}
        )
        assert response.status_code == 200

    def test_short_form_never_leaks_ingredient_names(self, client, existing_drink):
        response = client.get("/drinks")
        recipe = response.get_json()["drinks"][0]["recipe"]
        assert all(set(item) == {"color", "parts"} for item in recipe)
        assert "espresso" not in response.get_data(as_text=True)

    def test_long_form_includes_ingredient_names(
        self, client, barista_headers, existing_drink
    ):
        response = client.get("/drinks-detail", headers=barista_headers)
        recipe = response.get_json()["drinks"][0]["recipe"]
        assert all(set(item) == {"color", "parts", "name"} for item in recipe)


class TestRBACIntrospection:
    """The published matrix is generated from the code, not maintained by hand."""

    def test_health_rbac_reflects_the_decorators(self, client):
        routes = {
            (route["rule"], tuple(route["methods"])): route["permission"]
            for route in client.get("/health/rbac").get_json()["routes"]
        }
        assert routes[("/drinks", ("GET",))] is None
        assert routes[("/drinks-detail", ("GET",))] == "get:drinks-detail"
        assert routes[("/drinks", ("POST",))] == "post:drinks"
        assert routes[("/audit", ("GET",))] == "get:audit"

    def test_every_protected_route_names_a_known_permission(self, client):
        from src.auth.auth import KNOWN_PERMISSIONS

        for route in client.get("/health/rbac").get_json()["routes"]:
            permission = route["permission"]
            if permission:
                assert permission in KNOWN_PERMISSIONS, route["rule"]
