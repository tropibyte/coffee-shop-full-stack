# Coffee Shop Full Stack

A digital drink menu for the Udacity campus cafe, built to demonstrate
third-party authentication, role-based access control, and a REST API that
means it.

Anyone can see what is on the menu. Baristas can see the recipes. Managers can
change the menu and hire baristas. Administrators can hire managers and read
the record of everything that happened.

```
┌──────────────────┐   Authorization Code + PKCE   ┌──────────────────┐
│  Ionic / Angular │ ────────────────────────────► │      Auth0       │
│    (port 8100)   │ ◄──────────────────────────── │  tenant + RBAC   │
└────────┬─────────┘        access token           └────────┬─────────┘
         │                                                  │
         │  Authorization: Bearer <JWT>                     │ JWKS
         ▼                                                  │ /.well-known
┌──────────────────┐                                        │
│   Flask API      │ ◄──────────────────────────────────────┘
│   (port 5000)    │   verifies signature, issuer, audience,
│                  │   expiry, and the required permission
│  ┌────────────┐  │
│  │  SQLite    │  │   drinks + append-only audit trail
│  └────────────┘  │
└────────┬─────────┘
         │  Auth0 Management API (machine-to-machine)
         ▼
   user administration: invite, re-role, block, delete
```

---

## Contents

| | |
|---|---|
| [Quick start](#quick-start) | Get it running in five minutes |
| [What the roles can do](#what-the-roles-can-do) | The access-control model |
| [API reference](#api-reference) | Every endpoint |
| [Project layout](#project-layout) | Where things live |
| [Testing](#testing) | 363 tests, and how to run them |
| [Documentation](#documentation) | The rest of the docs |

---

## Quick start

**Prerequisites:** Python 3.11+, Node 20+, and an Auth0 account (free).

### 1. Configure Auth0

Work through **[docs/AUTH0_SETUP.md](docs/AUTH0_SETUP.md)** — it takes about 25
minutes and covers the API, the three roles, both applications, the Action, the
test users, MFA and Google sign-in. It ends with a checklist and a
troubleshooting table keyed to the exact errors this API returns.

You can skip it to see the public menu; you cannot skip it to sign in.

### 2. Backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
copy .env.example .env         # macOS/Linux: cp .env.example .env
```

Fill in the Auth0 values in `.env`, then:

```bash
flask run
```

The API comes up on <http://127.0.0.1:5000>, creates its SQLite database,
and seeds three demo drinks so the menu is never empty on a first run.

Interactive docs: <http://127.0.0.1:5000/docs>

### 3. Frontend

```bash
cd frontend
npm install
```

Fill in `src/environments/environment.ts` with the same Auth0 values, then:

```bash
ionic serve
```

The app opens on <http://localhost:8100>. If you have no Ionic CLI,
`npm start` does the same thing.

### 4. Check it

Open <http://localhost:8100/diagnostics>. It reads both configurations and
names anything missing or mismatched — including the case where the frontend
and the API point at different Auth0 tenants, which otherwise shows up only as
an unexplained 401.

---

## What the roles can do

### Menu

| | Public | Barista | Manager | Administrator |
|---|:---:|:---:|:---:|:---:|
| See drinks and proportions | ✅ | ✅ | ✅ | ✅ |
| See ingredient names | ❌ | ✅ | ✅ | ✅ |
| Add a drink | ❌ | ❌ | ✅ | ✅ |
| Edit a drink | ❌ | ❌ | ✅ | ✅ |
| Delete a drink | ❌ | ❌ | ✅ | ✅ |

### People

| | Public | Barista | Manager | Administrator |
|---|:---:|:---:|:---:|:---:|
| See your own access | ❌ | ✅ | ✅ | ✅ |
| Administer baristas | ❌ | ❌ | ✅ | ✅ |
| Administer managers | ❌ | ❌ | ❌ | ✅ |
| Administer administrators | ❌ | ❌ | ❌ | ❌ |
| Read the audit trail | ❌ | ❌ | ❌ | ✅ |

Nobody, at any rank, may administer themselves or a peer. That is not an
oversight — see [docs/RBAC.md](docs/RBAC.md).

### Why the hierarchy is not just permissions

Auth0 permissions are verbs: *may edit users*. They cannot express *may edit
users junior to you*, because that is a relationship, not a capability. So
authorisation happens in two layers, and both must pass:

1. **Capability.** `@requires_auth('patch:users')` asks Auth0 whether the
   caller may edit users at all. A barista holds none of these permissions and
   is stopped here with a 403.
2. **Rank.** The view then compares the caller's role rank with the target's.
   A caller may act only on someone strictly more junior, and grant only a
   role strictly more junior than their own.

The second layer is what stops a manager promoting themselves to
administrator, deleting an administrator, or quietly making a barista into a
manager — all of which the first layer alone would permit.

---

## API reference

Full interactive documentation at `/docs`; machine-readable at
`/openapi.json`.

### Drinks

| Method | Route | Permission | Returns |
|---|---|---|---|
| `GET` | `/drinks` | — public — | Colours and proportions |
| `GET` | `/drinks/<id>` | — public — | One drink, short form |
| `GET` | `/drinks-detail` | `get:drinks-detail` | Full recipes |
| `POST` | `/drinks` | `post:drinks` | The created drink |
| `PATCH` | `/drinks/<id>` | `patch:drinks` | The updated drink |
| `DELETE` | `/drinks/<id>` | `delete:drinks` | The deleted id |

`GET /drinks` and `/drinks-detail` accept optional `search`, `sort`, `order`,
`page` and `per_page`. Omitting them returns the whole menu, so the documented
behaviour is the default.

### People

| Method | Route | Permission |
|---|---|---|
| `GET` | `/users/me` | any valid token |
| `GET` | `/users` | `get:users` |
| `GET` | `/users/<id>` | `get:users` |
| `POST` | `/users` | `post:users` |
| `PATCH` | `/users/<id>` | `patch:users` |
| `DELETE` | `/users/<id>` | `delete:users` |
| `GET` | `/roles` | `get:roles` |

`POST /users` accepts an email and a role and returns a one-time
password-setup link. **No password is accepted or returned by this API at
any point** — the account is created with a random secret that is discarded
immediately, and the new user chooses their own through Auth0.

### Audit and operations

| Method | Route | Permission |
|---|---|---|
| `GET` | `/audit` | `get:audit` |
| `GET` | `/health` | — public — |
| `GET` | `/health/live` | — public — |
| `GET` | `/health/ready` | — public — |
| `GET` | `/health/rbac` | — public — |
| `GET` | `/docs`, `/openapi.json` | — public — |

`/health/rbac` returns the live route-to-permission map, generated by walking
Flask's URL map at request time. It reflects the decorators on the routes
rather than a table someone remembered to update.

### Errors

Every failure returns the same shape:

```json
{ "success": false, "error": 403, "message": "Permission not found: post:drinks." }
```

Authorisation failures carry a machine-readable `code` alongside it, and every
response carries an `X-Request-Id` that ties it to the server log and to the
audit trail.

**401 and 403 mean different things.** 401 is "I do not know who you are";
403 is "I know exactly who you are, and no". Collapsing them tells a barista
to go and log in again, which they will do, successfully, to no effect.

---

## Project layout

```
coffee-shop-full-stack/
├── backend/
│   ├── src/
│   │   ├── api.py                 the five drink endpoints + audit
│   │   ├── app_factory.py         wiring: CORS, limits, logging, blueprints
│   │   ├── config.py              environment-driven, fails fast
│   │   ├── errors.py              every @app.errorhandler, in one place
│   │   ├── observability.py       structured logs, request ids, headers
│   │   ├── audit.py               the append-only trail
│   │   ├── health.py              probes + live RBAC matrix
│   │   ├── docs.py                OpenAPI document and Swagger UI
│   │   ├── auth/auth.py           ★ token verification and RBAC
│   │   ├── database/models.py     Drink, AuditEvent, recipe validation
│   │   └── management/            Auth0 Management API integration
│   ├── tests/                     326 tests
│   ├── scripts/                   collection generator, token injector
│   └── udacity-fsnd-udaspicelatte.postman_collection.json
├── frontend/
│   └── src/app/
│       ├── core/                  auth, PKCE, HTTP, guards, theme
│       ├── pages/                 menu, people, audit, profile, diagnostics
│       └── shared/                the drink graphic
├── docs/                          setup, RBAC, threat model, deployment
├── infra/                         Dockerfiles, compose, Azure Bicep
├── auth0/                         Terraform for the tenant
└── .github/workflows/             lint, test, security scan, deploy
```

---

## Testing

```bash
# Backend: 326 tests
cd backend
pytest                              # or: pytest -m security
pytest --cov=src --cov-report=term-missing

# Frontend: 37 tests
cd frontend
npm test
```

The backend suite does **not** stub out authentication. A session-scoped RSA
key pair stands in for Auth0's signing key, a matching JWKS document is served
from an intercepted HTTP layer, and the tests mint real RS256 tokens against
it. Every request therefore travels the production path — header parsing, key
lookup, signature verification, claim validation, permission check — so a
regression in any of those fails a test rather than passing one.

Outbound HTTP is blocked for the whole suite, so a test that accidentally
reaches the internet fails loudly instead of going quiet and slow.

Among the things that are asserted rather than assumed:

- a token signed with an unpublished key is refused;
- a token claiming a *published* `kid` but signed with another key is refused;
- `alg: none` is refused;
- an HS256 token signed with the tenant's own public key is refused
  (hand-assembled, because PyJWT will not build one);
- a token for a different audience, or a different issuer, is refused;
- `exp`, `iat`, `iss`, `aud` and `sub` are *required*, not merely validated
  when present;
- an Auth0 key rotation is picked up without a restart;
- a flood of forged key ids does not turn the service into a request
  amplifier pointed at Auth0.

### Postman

```bash
cd backend
python scripts/set_postman_tokens.py --check         # what is in there now
python scripts/set_postman_tokens.py --barista "eyJ..." --manager "eyJ..."
```

38 requests across five folders, every one carrying assertions. The `manager`
folder runs a complete create → read → update → delete cycle and cleans up
after itself, so the collection can be run repeatedly — including by Newman in
CI:

```bash
newman run backend/udacity-fsnd-udaspicelatte.postman_collection.json \
  --env-var host=http://127.0.0.1:5000
```

---

## Documentation

| Document | What it covers |
|---|---|
| [AUTH0_SETUP.md](docs/AUTH0_SETUP.md) | Click-by-click tenant setup, with a checklist and a troubleshooting table |
| [RBAC.md](docs/RBAC.md) | The full access-control matrix and why it is shaped this way |
| [THREAT_MODEL.md](docs/THREAT_MODEL.md) | STRIDE analysis, the attacks considered, and what stops each |
| [DEPENDENCIES.md](docs/DEPENDENCIES.md) | Why PyJWT replaced python-jose, and every other dependency decision |
| [DEPLOYMENT.md](docs/DEPLOYMENT.md) | Azure App Service + Static Web Apps, Docker, and the alternatives |
| [COMPLIANCE.md](docs/COMPLIANCE.md) | Risk register and control mapping |
| [RUBRIC.md](docs/RUBRIC.md) | Every rubric criterion, and where in the code it is met |
| [backend/README.md](backend/README.md) | Running, testing and extending the API |
| [frontend/README.md](frontend/README.md) | Running and building the Ionic app |

---

## Notable departures from the starter code

Each of these was a deliberate call, and each is explained where it happens.

| Change | Why |
|---|---|
| Flask 3 / SQLAlchemy 2 instead of Flask 2.0 | The starter imports `flask._request_ctx_stack`, deleted in Flask 2.2. The original pins do not install cleanly on a current Python. |
| PyJWT instead of python-jose | python-jose carries unfixed advisories including an algorithm-confusion issue. See [DEPENDENCIES.md](docs/DEPENDENCIES.md). |
| Angular 22 / Ionic 9 instead of Angular 7 / Ionic 4 | The 2019 toolchain does not install on Node 20+. This also covers the "unique styles or functionality" suggestion. |
| Authorization Code + PKCE instead of implicit | The implicit flow puts the access token in the URL fragment. Both are implemented; one boolean switches them. |
| `recipe` column widened to `Text` | The starter declared `String(180)`. Three ingredients with readable names exceed that, and SQLite does not enforce it — so the overflow would only appear after a move to Postgres. |
| Recipe colours validated | The frontend interpolates the colour into a style binding. An unvalidated string there is stored CSS injection. |
| Error handlers in `errors.py`, not `api.py` | Keeping them together is what guarantees *every* failure returns the same JSON, including ones no route raised. |

---

## Licence

MIT. Starter code © Udacity.
