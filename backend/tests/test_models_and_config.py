"""Model-level validation and start-up configuration.

Validation is tested directly, not only through the API, because the model is
where the guarantee lives: no route can write an invalid recipe because no
route constructs one without going through :func:`validate_recipe`.

The configuration tests assert the *refusals* -- the cases where the service
must decline to start rather than boot into something insecure.
"""

from __future__ import annotations

import json

import pytest

from src.config import (
    Config,
    ConfigurationError,
    DevelopmentConfig,
    ProductionConfig,
    TestingConfig,
    get_config,
)
from src.database.models import (
    MAX_INGREDIENTS,
    Drink,
    RecipeValidationError,
    validate_color,
    validate_recipe,
    validate_title,
)


class TestValidateTitle:
    """Titles are trimmed, bounded and required."""

    def test_trims(self):
        assert validate_title("  Latte  ") == "Latte"

    @pytest.mark.parametrize("bad", ["", "   ", None, 42, [], {"a": 1}])
    def test_rejects(self, bad):
        with pytest.raises(RecipeValidationError):
            validate_title(bad)

    def test_length_bound(self):
        assert validate_title("x" * 80) == "x" * 80
        with pytest.raises(RecipeValidationError):
            validate_title("x" * 81)


class TestValidateColor:
    """Colours are constrained to what the UI can actually draw."""

    @pytest.mark.parametrize(
        "value",
        [
            "#abc",
            "#abcd",
            "#aabbcc",
            "#aabbccdd",
            "rgb(1,2,3)",
            "rgb( 10 , 20 , 30 )",
            "rgba(1,2,3,0.5)",
            "rgba(1,2,3,1)",
            "blue",
            "rebeccapurple",
            "  blue  ",
        ],
    )
    def test_accepts(self, value):
        assert validate_color(value) == value.strip()

    @pytest.mark.parametrize(
        "value",
        [
            "#ab",
            "#abcde",
            "#gggggg",
            "rgb(1,2)",
            "rgb(1,2,3,4,5)",
            "url(x)",
            "var(--x)",
            "expression(1)",
            "blue; color: red",
            "a" * 40,
            "",
            "   ",
            42,
            None,
        ],
    )
    def test_rejects(self, value):
        with pytest.raises(RecipeValidationError):
            validate_color(value)


class TestValidateRecipe:
    """Recipes are normalised into one canonical shape."""

    def test_normalises_a_single_object(self):
        result = validate_recipe({"name": " water ", "color": "blue", "parts": 1})
        assert result == [{"name": "water", "color": "blue", "parts": 1}]

    def test_decodes_a_json_string(self):
        result = validate_recipe('[{"name":"water","color":"blue","parts":2}]')
        assert result[0]["parts"] == 2

    def test_does_not_mutate_the_caller_object(self):
        original = [{"name": " water ", "color": "blue", "parts": 1, "extra": "x"}]
        validate_recipe(original)
        assert original[0]["name"] == " water "
        assert "extra" in original[0]

    def test_drops_unknown_keys(self):
        # An ingredient is exactly three fields; anything else a client sends
        # is discarded rather than stored and echoed back later.
        result = validate_recipe(
            [{"name": "water", "color": "blue", "parts": 1, "note": "<script>"}]
        )
        assert set(result[0]) == {"name", "color", "parts"}

    def test_float_parts_are_allowed(self):
        assert (
            validate_recipe([{"name": "water", "color": "blue", "parts": 1.5}])[0][
                "parts"
            ]
            == 1.5
        )

    def test_boolean_parts_are_not_a_number(self):
        # bool subclasses int, so `isinstance(True, int)` is True and a naive
        # check lets `"parts": true` through as 1.
        with pytest.raises(RecipeValidationError, match="must be a number"):
            validate_recipe([{"name": "water", "color": "blue", "parts": True}])

    @pytest.mark.parametrize("bad", [[], "not json", 5, None, [[]], [None]])
    def test_rejects_malformed(self, bad):
        with pytest.raises(RecipeValidationError):
            validate_recipe(bad)

    def test_ingredient_count_bound(self):
        ok = [{"name": "i", "color": "blue", "parts": 1}] * MAX_INGREDIENTS
        assert len(validate_recipe(ok)) == MAX_INGREDIENTS
        with pytest.raises(RecipeValidationError):
            validate_recipe(ok + [{"name": "i", "color": "blue", "parts": 1}])

    def test_error_names_the_offending_index(self):
        with pytest.raises(RecipeValidationError, match=r"recipe\[1\]"):
            validate_recipe(
                [
                    {"name": "ok", "color": "blue", "parts": 1},
                    {"name": "bad", "color": "blue"},
                ]
            )


class TestDrinkRepresentations:
    """short() and long() are a frozen contract."""

    def test_short_omits_names(self, app, existing_drink):
        short = existing_drink.short()
        assert set(short) == {"id", "title", "recipe"}
        assert all(set(item) == {"color", "parts"} for item in short["recipe"])

    def test_long_includes_names(self, app, existing_drink):
        long_form = existing_drink.long()
        assert set(long_form) == {"id", "title", "recipe"}
        assert all(
            set(item) == {"color", "parts", "name"} for item in long_form["recipe"]
        )

    def test_long_detailed_adds_timestamps_without_changing_long(
        self, app, existing_drink
    ):
        detailed = existing_drink.long_detailed()
        assert detailed["created_at"] and detailed["updated_at"]
        # long() itself must stay exactly the specified three keys.
        assert set(existing_drink.long()) == {"id", "title", "recipe"}

    def test_repr_is_the_short_form(self, app, existing_drink):
        assert json.loads(repr(existing_drink)) == existing_drink.short()

    def test_a_legacy_single_object_blob_still_reads(self, app):
        # Rows written before the list-normalising validator existed store a
        # bare object.  They must not crash the public menu.
        legacy = Drink(
            title="Legacy", recipe='{"name":"water","color":"blue","parts":1}'
        )
        legacy.insert()
        assert legacy.short()["recipe"] == [{"color": "blue", "parts": 1}]


class TestConfiguration:
    """The service declines to start rather than start up insecure."""

    def test_testing_config_resolves(self):
        assert get_config("testing") is TestingConfig
        assert get_config("development") is DevelopmentConfig
        assert get_config("production") is ProductionConfig

    def test_unknown_name_is_rejected(self):
        with pytest.raises(ConfigurationError, match="Unknown FLASK_CONFIG"):
            get_config("staging")

    def test_missing_auth0_settings_are_fatal(self):
        class Broken(Config):
            AUTH0_DOMAIN = ""
            AUTH0_API_AUDIENCE = ""

        with pytest.raises(ConfigurationError, match="Missing required Auth0"):
            Broken.validate()

    @pytest.mark.security
    def test_symmetric_algorithms_are_refused(self):
        class Broken(TestingConfig):
            AUTH0_ALGORITHMS = ["RS256", "HS256"]

        with pytest.raises(ConfigurationError, match="Symmetric algorithms"):
            Broken.validate()

    @pytest.mark.security
    def test_the_none_algorithm_is_refused(self):
        class Broken(TestingConfig):
            AUTH0_ALGORITHMS = ["none"]

        with pytest.raises(ConfigurationError, match="never acceptable"):
            Broken.validate()

    def test_empty_algorithm_list_is_refused(self):
        class Broken(TestingConfig):
            AUTH0_ALGORITHMS = []

        with pytest.raises(ConfigurationError, match="must not be empty"):
            Broken.validate()

    def test_management_api_needs_credentials(self):
        class Broken(TestingConfig):
            MANAGEMENT_API_ENABLED = True
            AUTH0_M2M_CLIENT_ID = ""
            AUTH0_M2M_CLIENT_SECRET = ""

        with pytest.raises(ConfigurationError, match="MANAGEMENT_API_ENABLED"):
            Broken.validate()

    @pytest.mark.security
    def test_production_requires_a_real_secret_key(self):
        class Broken(ProductionConfig):
            AUTH0_DOMAIN = "t.us.auth0.com"
            AUTH0_API_AUDIENCE = "coffee-shop"
            MANAGEMENT_API_ENABLED = False
            SECRET_KEY = "short"

        with pytest.raises(ConfigurationError, match="SECRET_KEY"):
            Broken.validate()

    @pytest.mark.security
    def test_production_refuses_a_destructive_boot(self):
        class Broken(ProductionConfig):
            AUTH0_DOMAIN = "t.us.auth0.com"
            AUTH0_API_AUDIENCE = "coffee-shop"
            MANAGEMENT_API_ENABLED = False
            SECRET_KEY = "k" * 48
            DB_DROP_AND_CREATE_ALL = True

        with pytest.raises(ConfigurationError, match="DB_DROP_AND_CREATE_ALL"):
            Broken.validate()

    @pytest.mark.security
    def test_production_refuses_a_wildcard_cors_origin(self):
        class Broken(ProductionConfig):
            AUTH0_DOMAIN = "t.us.auth0.com"
            AUTH0_API_AUDIENCE = "coffee-shop"
            MANAGEMENT_API_ENABLED = False
            SECRET_KEY = "k" * 48
            DB_DROP_AND_CREATE_ALL = False
            CORS_ORIGINS = ["*"]

        with pytest.raises(ConfigurationError, match="CORS_ORIGINS"):
            Broken.validate()

    def test_a_valid_production_config_passes(self):
        class Good(ProductionConfig):
            AUTH0_DOMAIN = "t.us.auth0.com"
            AUTH0_API_AUDIENCE = "coffee-shop"
            MANAGEMENT_API_ENABLED = False
            SECRET_KEY = "k" * 48
            DB_DROP_AND_CREATE_ALL = False
            CORS_ORIGINS = ["https://coffee.example.com"]

        Good.validate()

    def test_derived_urls(self):
        assert TestingConfig.issuer().endswith("/")
        assert TestingConfig.jwks_url().endswith("/.well-known/jwks.json")
        assert TestingConfig.management_audience().endswith("/api/v2/")


class TestSeeding:
    """Seeding is idempotent, so a cold start never duplicates the menu."""

    def test_seeds_once(self, app, db_session):
        from src.database.models import DEMO_DRINKS, seed_demo_drinks

        assert seed_demo_drinks() == len(DEMO_DRINKS)
        assert seed_demo_drinks() == 0
        assert db_session.query(Drink).count() == len(DEMO_DRINKS)

    def test_does_not_touch_existing_rows(self, app, db_session):
        from src.database.models import seed_demo_drinks

        seed_demo_drinks()
        water = db_session.query(Drink).filter_by(title="water").one()
        water.title = "water"
        water.recipe = json.dumps([{"name": "h2o", "color": "cyan", "parts": 9}])
        water.update()

        seed_demo_drinks()
        db_session.expire_all()
        assert (
            db_session.query(Drink)
            .filter_by(title="water")
            .one()
            .recipe_json()[0]["parts"]
            == 9
        )
