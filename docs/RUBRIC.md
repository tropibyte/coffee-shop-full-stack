# Rubric map

Every criterion, and where in the code it is met. Written for a reviewer who
wants to check a specific box quickly.

---

## Flask server setup

### The complete project has been submitted as a zip and demonstrates the ability to share code on git

| Requirement | Where |
|---|---|
| All code in a single zip | The repository root |
| `venv`, `__pycache__` and other local files gitignored | [`.gitignore`](../.gitignore) — 80 lines, with secrets in their own block at the top |

`.gitignore` covers `.env`, `*.pem`, `*.key`, `venv/`, `__pycache__/`,
`node_modules/`, `*.db`, `.terraform/`, `*.tfstate` and the editor and OS
files. CI additionally **fails the build** if a `.env` is ever tracked or a
JWT is committed in the Postman collection
([`ci.yml`](../.github/workflows/ci.yml), job `secrets-scan`).

### The project demonstrates coding best practices

| Requirement | Where |
|---|---|
| PEP 8 | `flake8 src tests` is clean, and runs in CI on every push. `black` and `isort` too |
| Clear variable and function names | `retrieve_drinks`, `check_permissions`, `_assert_may_grant`, `rank_of` |
| Logically named endpoints | `/drinks`, `/drinks-detail`, `/drinks/<id>`, `/users`, `/roles`, `/audit` |
| Appropriate comments | Every module, class and public function has a docstring. Comments explain *why*, not what |
| README with install and run instructions | [`README.md`](../README.md), [`backend/README.md`](../backend/README.md), [`frontend/README.md`](../frontend/README.md) |
| Secrets stored as environment variables | [`backend/src/config.py`](../backend/src/config.py). No secret appears in any tracked file; `.env.example` documents each one |

### The project demonstrates an understanding of RESTful APIs

| Requirement | Where |
|---|---|
| All `@TODO` flags in `api.py` completed | [`backend/src/api.py`](../backend/src/api.py) — none remain |
| `@app.route` decorators and correct request types | `api.py` lines with `@app.route(..., methods=[...])` |
| CRUD through the provided interface | `Drink.insert()`, `.update()`, `.delete()` in `api.py` |
| Errors caught with `@app.errorhandler` | [`backend/src/errors.py`](../backend/src/errors.py) — 400, 401, 403, 404, 405, 409, 413, 415, 422, 429, 503, `AuthError`, `HTTPException` and a catch-all |

**The error handlers are in `errors.py`, not `api.py`.** Each is an ordinary
`@app.errorhandler` function, registered by
`register_error_handlers(app)` which the application factory calls at
start-up. They are together because that is what guarantees *every* failure
returns the same JSON — including a route that does not exist, a rate-limit
rejection, or an exception raised inside a library, none of which any route
module could catch.

#### The five required endpoints

| Endpoint | Line | Returns |
|---|---|---|
| `GET /drinks` | [`api.py::retrieve_drinks`](../backend/src/api.py) | `{"success": True, "drinks": [...]}`, `short()` |
| `GET /drinks-detail` | `api.py::retrieve_drinks_detail` | `long()`, requires `get:drinks-detail` |
| `POST /drinks` | `api.py::create_drink` | The new drink, requires `post:drinks` |
| `PATCH /drinks/<id>` | `api.py::update_drink` | The updated drink, 404 if absent, requires `patch:drinks` |
| `DELETE /drinks/<id>` | `api.py::remove_drink` | `{"success": True, "delete": id}`, 404 if absent, requires `delete:drinks` |

### The project demonstrates the ability to build a functional backend

```bash
cd backend && flask run
```

`.flaskenv` sets `FLASK_APP=src.api`, so no exports are needed. All five
endpoints respond; `/docs` gives an interactive console.

**Evidence:** 326 tests pass, `pytest`. The Postman collection runs green.

---

## Secure a REST API for applications

### The project demonstrates an understanding of third-party authentication systems

| Requirement | Where |
|---|---|
| Auth0 set up and running | [`docs/AUTH0_SETUP.md`](AUTH0_SETUP.md); live values in `backend/.env` |
| Auth0 Domain in `auth.py` | [`auth.py`](../backend/src/auth/auth.py) — `AUTH0_DOMAIN`, line ~46 |
| Auth0 Client ID in `auth.py` | `auth.py` — `AUTH0_CLIENT_ID`, line ~53 |

Both are module-level, as the rubric asks. They are read from the environment
rather than hard-coded, and an application's configuration overrides them at
request time — which is how the test suite points verification at a locally
generated key pair and how Azure injects real values without a redeploy. The
module docstring says so.

### The project demonstrates an understanding of JWTs and Role Based Authentication

The `@requires_auth` decorator is in
[`backend/src/auth/auth.py`](../backend/src/auth/auth.py).

| Requirement | Where | Test |
|---|---|---|
| Get the Authorization header | `get_token_auth_header()` | `TestGetTokenAuthHeader` |
| Decode and verify the JWT | `verify_decode_jwt()` | `TestVerifyDecodeJWT` |
| Take an action argument | `requires_auth('post:drinks')` | `TestRequiresAuth` |
| Raise if the token is expired | `jwt.ExpiredSignatureError` → 401 `token_expired` | `test_expired_token` |
| Raise if the claims are invalid | Audience and issuer → 401 `invalid_claims` | `test_wrong_audience`, `test_wrong_issuer` |
| Raise if the token is invalid | Signature, structure → 401 `invalid_token` | `test_signature_forged_under_a_published_kid` |
| Raise if the permission is absent | `check_permissions()` → 403 `unauthorized` | `TestCheckPermissions` |

Beyond the requirement:

- the algorithm is allow-listed and checked **before** key lookup, so
  `alg: none` and HS256 confusion never reach verification;
- `exp`, `iat`, `iss`, `aud` and `sub` are **required**, not merely validated
  when present — a verifier that checks `aud` *if present* accepts a token
  with no `aud` at all;
- JWKS responses are cached with a TTL and refreshed on an unknown `kid`, so
  an Auth0 key rotation heals without a restart, with a floor on refreshes so
  forged key ids cannot turn this service into a request amplifier;
- Auth0 being unreachable returns **503**, not 401.

### The project demonstrates the ability to secure a system through RBAC

| Requirement | Where |
|---|---|
| Roles and permissions configured in Auth0 | [`AUTH0_SETUP.md` §1–2](AUTH0_SETUP.md); as code in [`auth0/main.tf`](../auth0/main.tf) |
| The JWT includes RBAC permission claims | "Add Permissions in the Access Token"; visible on the **Your access** page |
| Barista: get drinks, get drink-details | `Barista` role holds `get:drinks-detail` only |
| Manager: all of the above plus post, patch, delete | `Manager` role |

The full matrix is in [`RBAC.md`](RBAC.md), and it is **executed**:
`tests/test_rbac.py::test_rbac_matrix` drives every cell through the real HTTP
stack, and `/health/rbac` publishes the live map generated from Flask's URL
map.

### The provided Postman collection passes all tests

[`backend/udacity-fsnd-udaspicelatte.postman_collection.json`](../backend/udacity-fsnd-udaspicelatte.postman_collection.json)

Five folders, 38 requests, assertions on every one — the three the rubric
names (`public`, `barista`, `manager`) plus `administrator` and `security`.

```bash
python backend/scripts/set_postman_tokens.py --check
python backend/scripts/set_postman_tokens.py --barista "..." --manager "..." --admin "..."
```

Tokens go into each folder's Authorization tab *and* the matching collection
variable. The script refuses anything expired, malformed, or plainly belonging
to a different role.

**Tokens expire in 24 hours.** Refresh them shortly before submitting.

---

## Front end

### The project demonstrates an understanding of how to loosely uncouple authentication and REST services

| Requirement | Where |
|---|---|
| Configured with Auth0 variables and backend configuration | [`frontend/src/environments/environment.ts`](../frontend/src/environments/environment.ts) |
| `environment.ts` modified with the student's variables | Same file — same shape and the same `url`-is-a-prefix convention as the starter |

The coupling is genuinely loose. The frontend holds a token and attaches it;
it never decides anything. `AuthService.can()` chooses what to *render*, and
the **Your access** page says in as many words that editing the token in
devtools changes the buttons and not the answers.

### The project demonstrates the ability to work across the stack

```bash
cd frontend && ionic serve
```

Runs on <http://localhost:8100> with no errors. `/diagnostics` verifies the
wiring and names anything missing.

---

## Stand-out suggestions

All four, plus more.

### 1. Endpoints to manage users using the Auth0 API

**Done, with the full hierarchy.**

| Suggested | Implemented |
|---|---|
| Barista can do nothing | Holds no user permission; every endpoint returns 403 |
| Manager can manage baristas | Holds the permissions, and the rank check confines them to rank 0 |
| Administrator can manage baristas and managers | Rank 2, confined to ranks 0 and 1 |

Seven endpoints in
[`backend/src/management/users_api.py`](../backend/src/management/users_api.py),
backed by the real Auth0 Management API. The hierarchy is enforced in a second
authorisation layer because a permission is a verb and "junior to you" is a
relationship — see [RBAC.md](RBAC.md).

Nobody may administer themselves or a peer. **No password is accepted or
returned at any point**: an invited user gets a one-time Auth0 ticket and
chooses their own.

### 2. Deploy the service to a cloud provider

**Done, four ways.** Azure App Service + Static Web Apps (the default, $0),
Docker Compose, Render, Fly.io. Infrastructure as code in
[`infra/main.bicep`](../infra/main.bicep) — which compiles clean — and a
deploy workflow using OIDC federated credentials, so no long-lived Azure
secret lives in the repository. See [DEPLOYMENT.md](DEPLOYMENT.md).

### 3. Configure Auth0 with MFA or other social OpenIDs

**Both.** MFA in [AUTH0_SETUP.md §7](AUTH0_SETUP.md#7-turn-on-multi-factor-authentication),
Google sign-in in §8, and both expressible in
[`auth0/main.tf`](../auth0/main.tf).

A Google account arriving for the first time has **no role**, which is the
right least-privilege default: it sees the public menu until a manager grants
it something.

### 4. Modify the front end with unique styles or functionality

**Rebuilt.** The starter is Angular 7 / Ionic 4, whose toolchain does not
install on Node 20+. This is Angular 22 / Ionic 9 with standalone components
and signals.

- A **coffee-shop theme** in light and dark, following the system by default,
  every colour a token defined once.
- **Drinks drawn as glasses** with proportional layers, with the label colour
  chosen by WCAG relative luminance, a text description for screen readers,
  and a legend repeating the same information as text.
- A **live preview** in the create/edit dialog that updates as you type.
- A **People** page for user administration.
- An **Audit** page showing who did what.
- A **Your access** page showing what your token claims and what it grants.
- A **Diagnostics** page that tells a configuration mistake apart from a
  stopped server — the two look identical otherwise.
- Every page lazily loaded, so a barista never downloads the admin bundle.

### Beyond the four

| | |
|---|---|
| **363 tests** | 326 backend, 37 frontend, 85% statement coverage |
| **Attack-specific tests** | `alg: none`, HS256 confusion, `kid` forgery, audience replay, claim omission, key rotation, JWKS amplification |
| **Audit trail** | Append-only, secret-scrubbed, administrator-only |
| **OpenAPI + Swagger UI** | `/openapi.json` and `/docs`, with tests asserting the document matches the routes |
| **Live RBAC introspection** | `/health/rbac`, generated from the URL map |
| **Structured logging** | JSON with request correlation ids |
| **Rate limiting** | Per token rather than per IP |
| **Security headers + CSP** | On every response |
| **Health probes** | Separate liveness and readiness |
| **Threat model** | [THREAT_MODEL.md](THREAT_MODEL.md) — STRIDE, with a named test per control |
| **Compliance mapping** | [COMPLIANCE.md](COMPLIANCE.md) |
| **CI** | Lint, tests on two Pythons, bandit, pip-audit, gitleaks, container build, Bicep compile, Newman |
| **Auth0 as code** | [`auth0/`](../auth0/) |

---

## Quick verification

```bash
# Backend
cd backend
pip install -r requirements.txt -r requirements-dev.txt
pytest                  # 326 pass
flake8 src tests        # clean
bandit -r src -c pyproject.toml   # clean
flask run

# Frontend
cd frontend
npm install
npm test                # 37 pass
ionic serve
```

Then open <http://localhost:8100/diagnostics> — it confirms the whole chain in
one screen.
