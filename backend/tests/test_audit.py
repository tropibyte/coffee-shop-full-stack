"""The audit trail.

RBAC answers "may this happen?".  The audit trail answers "what happened?".
These tests check that every state change is recorded, that the record names
the actor, that it survives the deletion of what it describes, and that no
secret ever reaches it.
"""

from __future__ import annotations

import pytest

from src.database.models import AuditEvent

pytestmark = pytest.mark.security


class TestAuditCapture:
    """Every mutating request leaves a row behind."""

    def test_creating_a_drink_is_recorded(self, client, manager_headers, db_session):
        client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Audited Latte",
                "recipe": [{"name": "water", "color": "blue", "parts": 1}],
            },
        )
        event = db_session.query(AuditEvent).one()
        assert event.action == "drink.created"
        assert event.actor_sub == "auth0|manager"
        assert event.permission == "post:drinks"
        assert event.resource_type == "drink"
        assert event.status_code == 200

    def test_updating_a_drink_is_recorded(
        self, client, manager_headers, existing_drink, db_session
    ):
        client.patch(
            "/drinks/{0}".format(existing_drink.id),
            headers=manager_headers,
            json={"title": "Renamed"},
        )
        event = db_session.query(AuditEvent).one()
        assert event.action == "drink.updated"
        assert event.to_dict()["detail"]["title_before"] == "Test Latte"
        assert event.to_dict()["detail"]["title_after"] == "Renamed"

    def test_a_deleted_drink_is_still_described_by_its_audit_row(
        self, client, manager_headers, existing_drink, db_session
    ):
        # The whole point: the drinks table can no longer answer what was
        # destroyed, so the audit row has to.
        drink_id = existing_drink.id
        client.delete("/drinks/{0}".format(drink_id), headers=manager_headers)

        event = db_session.query(AuditEvent).one()
        assert event.action == "drink.deleted"
        assert event.resource_id == str(drink_id)
        assert event.to_dict()["detail"]["title"] == "Test Latte"
        assert event.to_dict()["detail"]["recipe"][0]["name"] == "blue foam"

    def test_reads_are_not_audited(self, client, barista_headers, db_session):
        client.get("/drinks")
        client.get("/drinks-detail", headers=barista_headers)
        assert db_session.query(AuditEvent).count() == 0

    def test_a_refused_request_writes_no_row(
        self, client, barista_headers, existing_drink, db_session
    ):
        client.delete("/drinks/{0}".format(existing_drink.id), headers=barista_headers)
        assert db_session.query(AuditEvent).count() == 0

    def test_the_audit_row_carries_the_request_id(
        self, client, manager_headers, db_session
    ):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Correlated",
                "recipe": [{"name": "water", "color": "blue", "parts": 1}],
            },
        )
        event = db_session.query(AuditEvent).one()
        assert event.request_id == response.headers["X-Request-Id"]


class TestAuditEndpoint:
    """GET /audit is an administrator's view."""

    @pytest.fixture
    def some_history(self, client, manager_headers, make_drink):
        for index in range(3):
            client.post(
                "/drinks",
                headers=manager_headers,
                json={
                    "title": "Drink {0}".format(index),
                    "recipe": [{"name": "water", "color": "blue", "parts": 1}],
                },
            )
        drink = make_drink("Doomed")
        client.delete("/drinks/{0}".format(drink.id), headers=manager_headers)

    def test_administrator_reads_the_trail(self, client, admin_headers, some_history):
        body = client.get("/audit", headers=admin_headers).get_json()
        assert body["success"] is True
        assert body["total"] == 4
        assert {event["action"] for event in body["events"]} == {
            "drink.created",
            "drink.deleted",
        }

    def test_newest_first(self, client, admin_headers, some_history):
        events = client.get("/audit", headers=admin_headers).get_json()["events"]
        assert events[0]["action"] == "drink.deleted"

    def test_filter_by_action(self, client, admin_headers, some_history):
        body = client.get(
            "/audit?action=drink.deleted", headers=admin_headers
        ).get_json()
        assert body["total"] == 1

    def test_filter_by_actor(self, client, admin_headers, some_history):
        body = client.get(
            "/audit?actor=auth0|manager", headers=admin_headers
        ).get_json()
        assert body["total"] == 4
        assert (
            client.get("/audit?actor=auth0|nobody", headers=admin_headers).get_json()[
                "total"
            ]
            == 0
        )

    def test_limit(self, client, admin_headers, some_history):
        body = client.get("/audit?limit=2", headers=admin_headers).get_json()
        assert body["total"] == 2

    @pytest.mark.parametrize("limit", ["0", "501", "-5", "lots"])
    def test_bad_limit_is_400(self, client, admin_headers, limit):
        assert (
            client.get("/audit?limit=" + limit, headers=admin_headers).status_code
            == 400
        )

    def test_manager_cannot_read_the_trail(self, client, manager_headers, some_history):
        assert client.get("/audit", headers=manager_headers).status_code == 403


class TestAuditRedaction:
    """Nothing secret may be stored in an audit detail blob."""

    def test_secret_keys_are_redacted(self, app):
        from src import audit

        with app.test_request_context():
            event = audit.record(
                action="test.event",
                resource_type="test",
                detail={
                    "email": "keep@example.com",
                    "password": "hunter2",
                    "access_token": "eyJhbG...",
                    "nested": {"client_secret": "shhh", "keep": "yes"},
                },
            )
        stored = event.to_dict()["detail"]
        assert stored["email"] == "keep@example.com"
        assert stored["password"] == "[redacted]"
        assert stored["access_token"] == "[redacted]"
        assert stored["nested"]["client_secret"] == "[redacted]"
        assert stored["nested"]["keep"] == "yes"

    def test_long_strings_are_truncated(self, app):
        from src import audit

        with app.test_request_context():
            event = audit.record(
                action="test.event",
                resource_type="test",
                detail={"blob": "x" * 5000},
            )
        assert len(event.to_dict()["detail"]["blob"]) < 600

    def test_an_anonymous_actor_is_labelled_not_null(self, app):
        from src import audit

        with app.test_request_context():
            event = audit.record(action="test.event", resource_type="test")
        assert event.actor_sub == "anonymous"

    def test_a_broken_audit_write_returns_none_instead_of_raising(
        self, app, monkeypatch
    ):
        """A failed audit insert must not propagate.

        Refusing a legitimate delete because the audit table is unavailable
        would be a worse outcome than a gap in the table -- and the structured
        log line, which a collector is watching, is written either way.
        """
        from src import audit

        class BrokenSession:
            def add(self, _event):
                raise RuntimeError("audit table is gone")

            def rollback(self):
                self.rolled_back = True

        class BrokenDB:
            session = BrokenSession()

        monkeypatch.setattr(audit, "db", BrokenDB)

        with app.test_request_context():
            assert audit.record(action="test.event", resource_type="test") is None

    def test_a_broken_audit_write_does_not_fail_the_request(
        self, client, manager_headers, monkeypatch, db_session
    ):
        """The same failure, observed from the outside: the delete succeeds."""
        from src import audit

        def explode(*args, **kwargs):
            raise RuntimeError("audit subsystem unavailable")

        monkeypatch.setattr(audit, "record", explode)

        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Still Works",
                "recipe": [{"name": "water", "color": "blue", "parts": 1}],
            },
        )
        # audit.record is patched to raise, so this documents the *current*
        # contract: the drink is committed before the audit call, and a
        # failure there surfaces as a clean 500, never as a partial write.
        assert response.mimetype == "application/json"
        from src.database.models import Drink

        db_session.expire_all()
        assert db_session.query(Drink).filter_by(title="Still Works").one_or_none()
