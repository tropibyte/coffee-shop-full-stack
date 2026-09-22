# Coffee Shop API

Flask REST API secured with Auth0 role-based access control.

- **Runtime:** Python 3.11+, Flask 3, SQLAlchemy 2
- **Database:** SQLite by default; any SQLAlchemy URL via `DATABASE_URL`
- **Tests:** 326, `pytest`
- **Docs:** `/docs` (Swagger UI), `/openapi.json`

---

## Install

### 1. Python

Python **3.11 or newer**. Check with:

```bash
python --version
```

On Windows, `py -3.11 --version` selects a specific interpreter if you have
several.

### 2. Virtual environment

Always work in one — it keeps this project's pinned versions away from
everything else on the machine.

**Windows (PowerShell)**

```powershell
cd backend
python -m venv venv
venv\Scripts\Activate.ps1
```

If PowerShell refuses to run the activation script:

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

**macOS / Linux**

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
```

Your prompt should now start with `(venv)`.

### 3. Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

For development, add the test and lint tooling:

```bash
pip install -r requirements.txt -r requirements-dev.txt
```

`requirements.txt` is the runtime tree and is what the container installs.
`requirements-dev.txt` includes it and adds pytest, flake8, black, bandit and
pip-audit. Every version is pinned exactly.

### 4. Configuration

```bash
cp .env.example .env          # PowerShell: Copy-Item .env.example .env
```

Open `.env` and fill in the Auth0 values. At minimum:

```ini
AUTH0_DOMAIN=your-tenant.us.auth0.com
AUTH0_API_AUDIENCE=coffee-shop
```

For the user-administration endpoints, also:

```ini
MANAGEMENT_API_ENABLED=true
AUTH0_M2M_CLIENT_ID=...
AUTH0_M2M_CLIENT_SECRET=...
```

Where each value comes from: **[../docs/AUTH0_SETUP.md](../docs/AUTH0_SETUP.md)**.

`.env` is gitignored and must stay that way. No secret is ever read from
anywhere else, and none is hard-coded.

> The API **refuses to start** if `AUTH0_DOMAIN` or `AUTH0_API_AUDIENCE` is
> missing, and tells you which. That is deliberate: a service that cannot
> verify tokens must not serve protected data, and a refusal is far easier to
> diagnose than a service that silently authorises everyone.

---

## Run

```bash
flask run
```

`.flaskenv` sets `FLASK_APP=src.api`, so no exports are needed. The API
listens on <http://127.0.0.1:5000>.

Alternatives:

```bash
flask run --reload                 # restart on source changes
python run.py                      # same thing, runnable from any directory
python run.py --port 5001          # if something already holds 5000
gunicorn --bind 0.0.0.0:8000 wsgi:app   # production
```

On first run the API creates its SQLite database at
`src/database/database.db` and seeds three demo drinks, so the menu is never
empty. Seeding is idempotent and skips any title that already exists — it
will not overwrite your data.

To start completely fresh:

```bash
# Windows:  del src\database\database.db
rm src/database/database.db
flask run
```

Or set `DB_DROP_AND_CREATE_ALL=true` in `.env`. That is destructive, and
`ProductionConfig` refuses to start with it enabled.

### Check it works

```bash
curl http://127.0.0.1:5000/health
curl http://127.0.0.1:5000/drinks
```

Then open <http://127.0.0.1:5000/docs> for the interactive console — you can
sign in with Auth0 from that page and exercise every protected endpoint.

---

## Test

```bash
pytest                                        # all 326
pytest -v                                     # with names
pytest -m security                            # attack-specific
pytest -m rbac                                # the access-control matrix
pytest tests/test_auth.py                     # one module
pytest -k "escalation"                        # by name
pytest --cov=src --cov-report=term-missing    # coverage
pytest --cov=src --cov-report=html            # then open htmlcov/index.html
```

No test touches the network. A session-scoped RSA key pair stands in for
Auth0's signing key, a matching JWKS document is served from an intercepted
HTTP layer, and the tests mint real RS256 tokens against it — so each request
travels the production verification path rather than a stub of it.

| Module | Covers |
|---|---|
| `test_auth.py` | Header parsing, signature, claims, JWKS caching and rotation, the forgery attempts |
| `test_rbac.py` | Every cell of the access-control matrix, driven through HTTP |
| `test_drinks.py` | CRUD, validation, colour constraints, the error paths |
| `test_users_api.py` | The privilege hierarchy and the escalation attempts |
| `test_audit.py` | Audit capture, redaction, and the administrator-only read |
| `test_errors.py` | Error shape, disclosure, security headers, CORS |
| `test_models_and_config.py` | Validation in isolation; the start-up refusals |
| `test_health_and_docs.py` | Probes; that the OpenAPI document matches the routes |

---

## Lint

```bash
flake8 src tests          # PEP 8; clean
black src tests           # format
isort src tests           # import order
bandit -r src -c pyproject.toml   # security linter; clean
pip-audit                 # dependency advisories
```

All four run in CI on every push.

---

## Layout

```
backend/
├── src/
│   ├── api.py              the five drink endpoints, plus /audit
│   ├── app_factory.py      CORS, limits, logging, blueprints, first boot
│   ├── config.py           environment-driven; validates and fails fast
│   ├── errors.py           every @app.errorhandler
│   ├── observability.py    structured logs, request ids, security headers
│   ├── audit.py            the append-only trail
│   ├── health.py           probes and the live RBAC matrix
│   ├── docs.py             OpenAPI document and Swagger UI
│   ├── auth/auth.py        ★ token verification and the RBAC decorator
│   ├── database/models.py  Drink, AuditEvent, recipe validation
│   └── management/         Auth0 Management API integration
│       ├── auth0_client.py    the HTTP client
│       └── users_api.py       ★ the privilege hierarchy
├── tests/                  326 tests
├── scripts/
│   ├── build_postman_collection.py   generates the collection
│   └── set_postman_tokens.py         injects JWTs before submitting
├── .env.example
├── requirements.txt
├── run.py                  development entry point
└── wsgi.py                 production entry point
```

### Where the error handlers are

**Not in `api.py`.** Every one of them — including the 404 and the `AuthError`
handler the specification names — is registered by
`errors.py::register_error_handlers`, which the application factory calls at
start-up. Each is an ordinary `@app.errorhandler` function.

They are together because that is what guarantees *every* failure leaves as
the same JSON: a route that does not exist, a rate-limit rejection, an
unhandled exception in a library. Scattering them across route modules is how
a JSON API ends up returning an HTML traceback to one particular caller on one
particular path.

---

## Configuration reference

Every setting, its default, and what it does. All are read from the
environment; `.env` is a convenience, not a separate mechanism.

### Required

| Variable | Description |
|---|---|
| `AUTH0_DOMAIN` | Tenant domain, e.g. `coffee-shop.us.auth0.com` |
| `AUTH0_API_AUDIENCE` | The API Identifier, e.g. `coffee-shop` |

### Auth0

| Variable | Default | Description |
|---|---|---|
| `AUTH0_ALGORITHMS` | `RS256` | Signing-algorithm allow-list. A symmetric algorithm here is refused at start-up |
| `AUTH0_LEEWAY_SECONDS` | `10` | Clock skew tolerated on `exp`/`iat`/`nbf` |
| `AUTH0_CLIENT_ID` | — | SPA client id. Not a secret; surfaced in `/health` |
| `JWKS_CACHE_TTL_SECONDS` | `600` | How long signing keys are cached |
| `JWKS_FETCH_TIMEOUT_SECONDS` | `5` | Timeout on the JWKS request |
| `JWKS_MIN_REFRESH_INTERVAL_SECONDS` | `30` | Floor between forced refreshes. Stops forged key ids becoming a request amplifier |

### User administration

| Variable | Default | Description |
|---|---|---|
| `MANAGEMENT_API_ENABLED` | `true` | Registers the `/users` and `/roles` endpoints |
| `AUTH0_M2M_CLIENT_ID` | — | **Secret.** Machine-to-machine client id |
| `AUTH0_M2M_CLIENT_SECRET` | — | **Secret.** Its secret |
| `AUTH0_CONNECTION` | `Username-Password-Authentication` | Connection new users are created on |
| `MANAGEMENT_HTTP_TIMEOUT_SECONDS` | `10` | Timeout on Management API calls |

### Database

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///src/database/database.db` | Any SQLAlchemy URL |
| `DB_DROP_AND_CREATE_ALL` | `false` | **Destructive.** Rebuilds the schema on boot. Refused in production |
| `DB_SEED_IF_EMPTY` | `true` | Inserts demo drinks only when the table is empty |

### Everything else

| Variable | Default | Description |
|---|---|---|
| `FLASK_CONFIG` | `development` | `development` / `testing` / `production` |
| `SECRET_KEY` | — | Flask session key. Production requires ≥32 characters |
| `CORS_ORIGINS` | localhost 8100/4200 | Comma-separated allow-list. `*` is refused in production |
| `FRONTEND_URL` | — | Where an invited user lands after setting their password |
| `RATELIMIT_ENABLED` | `true` | |
| `RATELIMIT_DEFAULT` | `200 per minute` | |
| `RATELIMIT_WRITE` | `30 per minute` | Applied to POST/PATCH/DELETE |
| `RATELIMIT_ADMIN` | `60 per minute` | Applied to `/audit` |
| `RATELIMIT_STORAGE_URI` | `memory://` | Use a Redis URL for more than one instance |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FORMAT` | `json` | `json` for collectors, `console` for humans |
| `AUDIT_LOG_ENABLED` | `true` | Persists audit rows. The log line is written either way |
| `DOCS_ENABLED` | `true` | Serves `/docs`, `/openapi.json` and `/health/rbac` |

---

## Postman

The collection lives at
`udacity-fsnd-udaspicelatte.postman_collection.json` — five folders, 38
requests, assertions on every one.

```bash
python scripts/set_postman_tokens.py --check
python scripts/set_postman_tokens.py --barista "eyJ..." --manager "eyJ..." --admin "eyJ..."
python scripts/set_postman_tokens.py --clear      # before committing
```

The script writes each token into both its folder's Authorization tab and the
matching collection variable, and refuses anything expired, malformed, or
plainly belonging to a different role.

Regenerate the collection itself after changing the API:

```bash
python scripts/build_postman_collection.py
```

Run it headlessly:

```bash
newman run udacity-fsnd-udaspicelatte.postman_collection.json \
  --env-var host=http://127.0.0.1:5000
```

---

## Extending it

### A new endpoint

```python
@app.route("/drinks/<int:drink_id>/history", methods=["GET"])
@requires_auth("get:audit")
def drink_history(payload, drink_id: int):
    """Return the audit events for one drink."""
    ...
```

The decorated view receives the verified JWT payload as its first argument.

### A new permission

1. Define it on the Auth0 API and add it to the roles that should hold it.
2. Add it to `KNOWN_PERMISSIONS` in `src/auth/auth.py`.
3. Add it to the `Permission` union in the frontend's `core/models.ts`.

Step 2 is not optional: `requires_auth` raises at import time for an unknown
permission, so a typo is a startup failure rather than an endpoint nobody can
reach.

### A schema change

```bash
flask db migrate -m "add a column"
flask db upgrade
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| `ConfigurationError: Missing required Auth0 settings` | No `.env`, or the values are blank. Working as intended |
| `ModuleNotFoundError: No module named 'src'` | Run `flask run` from `backend/`, not from `backend/src/` |
| `403 invalid_permissions_claim` | "Add Permissions in the Access Token" is off on the Auth0 API |
| `401 invalid_header` — cannot find signing key | `AUTH0_DOMAIN` names a different tenant than the token |
| CORS error in the browser | Add the frontend origin to `CORS_ORIGINS` |
| `503` on `/users` | Management API not configured; check the M2M credentials |
| Port 5000 in use | `python run.py --port 5001`, and update `apiServerUrl` in the frontend |

Full table, keyed to Auth0 settings: **[../docs/AUTH0_SETUP.md](../docs/AUTH0_SETUP.md#troubleshooting)**.
