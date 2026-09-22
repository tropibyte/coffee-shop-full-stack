"""User administration and the privilege hierarchy.

The standout requirement is a three-tier ladder:

* a barista can do nothing here,
* a manager can manage baristas,
* an administrator can manage baristas and managers.

Auth0 permissions alone cannot express that, because a permission is a verb
and the rule is a *relationship*.  These tests therefore concentrate on the
second layer -- the rank comparison -- and specifically on the ways a caller
might try to step around it: promoting themselves, deleting a peer, editing a
senior, or granting a role they do not outrank.

The Auth0 Management API is replayed from ``responses``; no test reaches the
network.
"""

from __future__ import annotations

import json
import re

import pytest
import responses as responses_lib

from .conftest import (
    ADMIN_PERMISSIONS,
    ALL_ROLES,
    MANAGEMENT_BASE,
    MANAGER_PERMISSIONS,
    auth0_role,
    auth0_user,
)

pytestmark = [pytest.mark.rbac, pytest.mark.integration]


# ---------------------------------------------------------------------------
# Management API doubles
# ---------------------------------------------------------------------------


def register_roles(mock) -> None:
    """Serve the tenant's three roles."""
    mock.add(
        responses_lib.GET,
        re.compile(re.escape(MANAGEMENT_BASE) + r"/roles(\?.*)?$"),
        json=ALL_ROLES,
        status=200,
    )


def register_user(mock, user_id: str, roles, user=None) -> None:
    """Serve one user and their role list."""
    quoted = re.escape(user_id.replace("|", "%7C"))
    mock.add(
        responses_lib.GET,
        re.compile(r".*/api/v2/users/" + quoted + r"/roles(\?.*)?$"),
        json=[auth0_role(name) for name in roles],
        status=200,
    )
    mock.add(
        responses_lib.GET,
        re.compile(r".*/api/v2/users/" + quoted + r"$"),
        json=user or auth0_user(user_id=user_id),
        status=200,
    )


@pytest.fixture
def tenant(mocked_responses):
    """A tenant containing one account at each rank."""
    register_roles(mocked_responses)
    register_user(mocked_responses, "auth0|barista", ["Barista"])
    register_user(mocked_responses, "auth0|manager", ["Manager"])
    register_user(mocked_responses, "auth0|admin", ["Administrator"])
    register_user(mocked_responses, "auth0|nobody", [])
    return mocked_responses


# ---------------------------------------------------------------------------
# Capability layer: which roles may call these endpoints at all
# ---------------------------------------------------------------------------


class TestCapabilityLayer:
    """A barista holds none of the user-management permissions."""

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/users"),
            ("GET", "/users/auth0|barista"),
            ("POST", "/users"),
            ("PATCH", "/users/auth0|barista"),
            ("DELETE", "/users/auth0|barista"),
            ("GET", "/roles"),
        ],
    )
    def test_barista_is_forbidden_everywhere(
        self, client, barista_headers, method, path
    ):
        response = client.open(path, method=method, headers=barista_headers, json={})
        assert response.status_code == 403

    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/users"), ("POST", "/users"), ("GET", "/roles")],
    )
    def test_anonymous_is_unauthenticated(self, client, method, path):
        response = client.open(path, method=method, json={})
        assert response.status_code == 401

    def test_any_valid_token_may_read_its_own_identity(
        self, client, barista_headers, tenant
    ):
        body = client.get("/users/me", headers=barista_headers).get_json()
        assert body["success"] is True
        assert body["user"]["roles"] == ["Barista"]
        assert body["user"]["rank"] == 0
        assert body["user"]["permissions"] == ["get:drinks-detail"]


# ---------------------------------------------------------------------------
# Rank layer: which targets a caller may act on
# ---------------------------------------------------------------------------


class TestRankLayer:
    """A caller may act only on accounts strictly junior to their own."""

    def test_manager_may_edit_a_barista(self, client, manager_headers, tenant):
        tenant.add(
            responses_lib.PATCH,
            re.compile(r".*/api/v2/users/auth0%7Cbarista$"),
            json=auth0_user(user_id="auth0|barista", blocked=True),
            status=200,
        )
        response = client.patch(
            "/users/auth0|barista", headers=manager_headers, json={"blocked": True}
        )
        assert response.status_code == 200
        assert response.get_json()["user"]["blocked"] is True

    def test_manager_may_not_edit_another_manager(self, client, auth_header, tenant):
        headers = auth_header(
            MANAGER_PERMISSIONS, sub="auth0|manager-two", roles=["Manager"]
        )
        response = client.patch(
            "/users/auth0|manager", headers=headers, json={"blocked": True}
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "insufficient_rank"

    def test_manager_may_not_edit_an_administrator(
        self, client, manager_headers, tenant
    ):
        response = client.patch(
            "/users/auth0|admin", headers=manager_headers, json={"blocked": True}
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "insufficient_rank"

    def test_manager_may_not_delete_an_administrator(
        self, client, manager_headers, tenant
    ):
        response = client.delete("/users/auth0|admin", headers=manager_headers)
        assert response.status_code == 403

    def test_administrator_may_edit_a_manager(self, client, admin_headers, tenant):
        tenant.add(
            responses_lib.PATCH,
            re.compile(r".*/api/v2/users/auth0%7Cmanager$"),
            json=auth0_user(user_id="auth0|manager", blocked=True),
            status=200,
        )
        response = client.patch(
            "/users/auth0|manager", headers=admin_headers, json={"blocked": True}
        )
        assert response.status_code == 200

    def test_administrator_may_not_edit_another_administrator(
        self, client, auth_header, tenant
    ):
        # Peers do not outrank each other.  Two administrators cannot fight.
        headers = auth_header(
            ADMIN_PERMISSIONS, sub="auth0|admin-two", roles=["Administrator"]
        )
        response = client.patch(
            "/users/auth0|admin", headers=headers, json={"blocked": True}
        )
        assert response.status_code == 403

    def test_nobody_may_administer_themselves(self, client, manager_headers, tenant):
        # Self-service would let a manager unblock or re-role their own
        # account, which defeats the point of having someone senior.
        response = client.patch(
            "/users/auth0|manager", headers=manager_headers, json={"blocked": False}
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "self_administration_forbidden"

    def test_nobody_may_delete_themselves(self, client, admin_headers, tenant):
        response = client.delete("/users/auth0|admin", headers=admin_headers)
        assert response.status_code == 403
        assert response.get_json()["code"] == "self_administration_forbidden"

    def test_an_unranked_account_is_manageable(self, client, manager_headers, tenant):
        tenant.add(
            responses_lib.PATCH,
            re.compile(r".*/api/v2/users/auth0%7Cnobody$"),
            json=auth0_user(user_id="auth0|nobody"),
            status=200,
        )
        response = client.patch(
            "/users/auth0|nobody", headers=manager_headers, json={"name": "Nobody"}
        )
        assert response.status_code == 200


# ---------------------------------------------------------------------------
# Privilege escalation
# ---------------------------------------------------------------------------


@pytest.mark.security
class TestPrivilegeEscalation:
    """Granting a role at or above the caller's own rank is always refused."""

    def test_manager_cannot_create_a_manager(self, client, manager_headers, tenant):
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "role": "Manager"},
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "privilege_escalation_blocked"

    def test_manager_cannot_create_an_administrator(
        self, client, manager_headers, tenant
    ):
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "role": "Administrator"},
        )
        assert response.status_code == 403

    def test_administrator_cannot_create_an_administrator(
        self, client, admin_headers, tenant
    ):
        response = client.post(
            "/users",
            headers=admin_headers,
            json={"email": "new@example.com", "role": "Administrator"},
        )
        assert response.status_code == 403

    def test_manager_cannot_promote_a_barista_to_manager(
        self, client, manager_headers, tenant
    ):
        response = client.patch(
            "/users/auth0|barista", headers=manager_headers, json={"role": "Manager"}
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "privilege_escalation_blocked"

    def test_administrator_may_promote_a_barista_to_manager(
        self, client, admin_headers, tenant
    ):
        tenant.add(
            responses_lib.POST,
            re.compile(r".*/api/v2/users/auth0%7Cbarista/roles$"),
            status=204,
        )
        tenant.add(
            responses_lib.DELETE,
            re.compile(r".*/api/v2/users/auth0%7Cbarista/roles$"),
            status=204,
        )
        response = client.patch(
            "/users/auth0|barista", headers=admin_headers, json={"role": "Manager"}
        )
        assert response.status_code == 200

    def test_an_unknown_role_is_refused(self, client, admin_headers, tenant):
        response = client.post(
            "/users",
            headers=admin_headers,
            json={"email": "new@example.com", "role": "Owner"},
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "unknown_role"

    @pytest.mark.parametrize("spelling", ["Manager", "manager", "MANAGER", " MaNaGeR "])
    def test_role_case_does_not_open_a_side_door(
        self, client, manager_headers, tenant, spelling
    ):
        # "manager", "Manager" and "MANAGER" must all resolve to the same
        # role, or a casing difference becomes a way past the rank check.
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "role": spelling},
        )
        assert response.status_code == 403
        assert response.get_json()["code"] == "privilege_escalation_blocked"

    def test_a_forged_roles_claim_does_not_grant_capability(
        self, client, auth_header, tenant
    ):
        # The roles claim only narrows what a caller may do; capability still
        # comes from the Auth0-signed permissions array.  Claiming to be an
        # administrator without the permissions gets you nothing.
        headers = auth_header([], sub="auth0|liar", roles=["Administrator"])
        assert client.get("/users", headers=headers).status_code == 403
        assert client.delete("/users/auth0|barista", headers=headers).status_code == 403


# ---------------------------------------------------------------------------
# Listing and visibility
# ---------------------------------------------------------------------------


class TestListing:
    """Listings are filtered by rank, so juniors cannot enumerate seniors."""

    @pytest.fixture
    def populated_tenant(self, tenant):
        tenant.add(
            responses_lib.GET,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users(\?.*)?$"),
            json={
                "users": [
                    auth0_user("auth0|barista", "barista@example.com", "Bea Barista"),
                    auth0_user("auth0|manager", "manager@example.com", "Mal Manager"),
                    auth0_user("auth0|admin", "admin@example.com", "Ada Admin"),
                ],
                "total": 3,
                "start": 0,
                "limit": 25,
            },
            status=200,
        )
        return tenant

    def test_manager_sees_only_baristas(
        self, client, manager_headers, populated_tenant
    ):
        body = client.get("/users", headers=manager_headers).get_json()
        assert [user["user_id"] for user in body["users"]] == ["auth0|barista"]
        assert body["your_rank"] == 1

    def test_administrator_sees_baristas_and_managers(
        self, client, admin_headers, populated_tenant
    ):
        body = client.get("/users", headers=admin_headers).get_json()
        assert {user["user_id"] for user in body["users"]} == {
            "auth0|barista",
            "auth0|manager",
        }

    def test_a_senior_account_reads_as_absent_not_forbidden(
        self, client, manager_headers, tenant
    ):
        # 403 would confirm the account exists.  404 declines to say.
        response = client.get("/users/auth0|admin", headers=manager_headers)
        assert response.status_code == 404

    def test_user_projection_is_an_allow_list(
        self, client, manager_headers, tenant, mocked_responses
    ):
        # Auth0 user objects carry identity-provider tokens.  A future field
        # must not appear in a response simply because Auth0 added it.
        mocked_responses.add(
            responses_lib.GET,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users(\?.*)?$"),
            json={
                "users": [
                    dict(
                        auth0_user("auth0|barista"),
                        identities=[{"access_token": "SECRET-IDP-TOKEN"}],
                        app_metadata={"internal_note": "do not publish"},
                    )
                ],
                "total": 1,
            },
            status=200,
        )
        response = client.get("/users", headers=manager_headers)
        assert "SECRET-IDP-TOKEN" not in response.get_data(as_text=True)
        assert "internal_note" not in response.get_data(as_text=True)
        assert set(response.get_json()["users"][0]) == {
            "user_id",
            "email",
            "name",
            "picture",
            "blocked",
            "email_verified",
            "logins_count",
            "last_login",
            "created_at",
            "roles",
            "rank",
        }

    def test_assignable_roles_are_filtered_by_rank(
        self, client, manager_headers, admin_headers, tenant
    ):
        manager_view = client.get("/roles", headers=manager_headers).get_json()
        assert [role["name"] for role in manager_view["roles"]] == ["Barista"]

        admin_view = client.get("/roles", headers=admin_headers).get_json()
        assert [role["name"] for role in admin_view["roles"]] == [
            "Manager",
            "Barista",
        ]


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


class TestCreateUser:
    """Invitations carry no password in either direction."""

    @pytest.fixture
    def creatable(self, tenant):
        tenant.add(
            responses_lib.POST,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users$"),
            json=auth0_user("auth0|new", "new@example.com", "New Barista"),
            status=201,
        )
        tenant.add(
            responses_lib.POST,
            re.compile(r".*/api/v2/users/auth0%7Cnew/roles$"),
            status=204,
        )
        tenant.add(
            responses_lib.POST,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/tickets/password-change$"),
            json={"ticket": "https://tenant.auth0.com/lo/reset?ticket=abc"},
            status=201,
        )
        return tenant

    def test_manager_creates_a_barista(self, client, manager_headers, creatable):
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "name": "New Barista", "role": "Barista"},
        )
        assert response.status_code == 201
        body = response.get_json()
        assert body["user"]["email"] == "new@example.com"
        assert body["user"]["roles"] == ["Barista"]
        assert body["password_setup_url"].startswith("https://")

    def test_no_password_is_accepted_from_the_caller(
        self, client, manager_headers, creatable
    ):
        # A password in the request body must be ignored, not honoured.
        client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "role": "Barista", "password": "hunter2"},
        )
        create_call = next(
            call
            for call in creatable.calls
            if call.request.method == "POST"
            and call.request.url.endswith("/api/v2/users")
        )
        sent = json.loads(create_call.request.body)
        assert sent["password"] != "hunter2"
        assert len(sent["password"]) >= 24

    def test_the_generated_password_never_reaches_the_caller(
        self, client, manager_headers, creatable
    ):
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "new@example.com", "role": "Barista"},
        )
        create_call = next(
            call
            for call in creatable.calls
            if call.request.method == "POST"
            and call.request.url.endswith("/api/v2/users")
        )
        generated = json.loads(create_call.request.body)["password"]

        assert generated not in response.get_data(as_text=True)
        # Nor may the response carry a field named `password` at all; only the
        # one-time ticket URL is handed back.
        assert "password" not in response.get_json()["user"]
        assert set(response.get_json()) == {
            "success",
            "user",
            "password_setup_url",
            "note",
        }

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"role": "Barista"},
            {"email": "new@example.com"},
            {"email": "not-an-email", "role": "Barista"},
            {"email": "a@b.co", "role": ""},
            {"email": "x" * 300 + "@example.com", "role": "Barista"},
        ],
    )
    def test_invalid_bodies_are_422(self, client, admin_headers, tenant, body):
        response = client.post("/users", headers=admin_headers, json=body)
        assert response.status_code == 422

    def test_duplicate_email_is_409(self, client, manager_headers, tenant):
        tenant.add(
            responses_lib.POST,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users$"),
            json={"statusCode": 409, "message": "The user already exists."},
            status=409,
        )
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "taken@example.com", "role": "Barista"},
        )
        assert response.status_code == 409

    def test_a_failed_role_assignment_rolls_the_account_back(
        self, client, manager_headers, tenant
    ):
        # An account with no role can do nothing and clutters the tenant.
        tenant.add(
            responses_lib.POST,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users$"),
            json=auth0_user("auth0|orphan", "orphan@example.com"),
            status=201,
        )
        tenant.add(
            responses_lib.POST,
            re.compile(r".*/api/v2/users/auth0%7Corphan/roles$"),
            json={"statusCode": 500},
            status=500,
        )
        deleted = tenant.add(
            responses_lib.DELETE,
            re.compile(r".*/api/v2/users/auth0%7Corphan$"),
            status=204,
        )
        response = client.post(
            "/users",
            headers=manager_headers,
            json={"email": "orphan@example.com", "role": "Barista"},
        )
        assert response.status_code == 502
        assert deleted.call_count == 1


# ---------------------------------------------------------------------------
# Upstream failure handling
# ---------------------------------------------------------------------------


class TestUpstreamFailures:
    """Auth0's problems are translated, never forwarded verbatim."""

    def test_rate_limiting_is_surfaced_as_429(
        self, client, manager_headers, mocked_responses
    ):
        register_roles(mocked_responses)
        mocked_responses.add(
            responses_lib.GET,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users(\?.*)?$"),
            json={"statusCode": 429},
            status=429,
        )
        response = client.get("/users", headers=manager_headers)
        assert response.status_code == 429

    def test_upstream_500_becomes_502_without_the_body(
        self, client, manager_headers, mocked_responses
    ):
        register_roles(mocked_responses)
        mocked_responses.add(
            responses_lib.GET,
            re.compile(re.escape(MANAGEMENT_BASE) + r"/users(\?.*)?$"),
            json={"message": "internal detail that must not escape"},
            status=500,
        )
        response = client.get("/users", headers=manager_headers)
        assert response.status_code == 502
        assert "internal detail" not in response.get_data(as_text=True)

    def test_unknown_user_is_404(self, client, manager_headers, mocked_responses):
        register_roles(mocked_responses)
        mocked_responses.add(
            responses_lib.GET,
            re.compile(r".*/api/v2/users/auth0%7Cghost/roles(\?.*)?$"),
            json={"statusCode": 404},
            status=404,
        )
        response = client.delete("/users/auth0|ghost", headers=manager_headers)
        assert response.status_code == 404
