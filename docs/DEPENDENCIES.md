# Dependency decisions

Why each library is here, and why some of the starter's are not. Versions are
pinned exactly so that `pip install -r requirements.txt` resolves to the tree
CI tested.

---

## The one that matters: PyJWT instead of python-jose

The starter code uses `python-jose` for token verification. This project uses
`PyJWT` with `cryptography`.

**Why:**

1. **Maintenance.** `python-jose` has been effectively unmaintained for
   years. For the one library in a security project whose job is deciding
   whether a credential is genuine, that is the wrong property to have.
2. **Published advisories.** Two were disclosed against python-jose in 2024:
   an algorithm-confusion issue in its key handling (CVE-2024-33663) and a
   decompression denial-of-service in its JWE support (CVE-2024-33664). The
   second does not apply here — this project never decrypts a JWE — but the
   first is squarely in the area this code depends on.
3. **`cryptography` underneath either way.** `python-jose[cryptography]` pulls
   in the same primitives PyJWT uses. Dropping jose removes a layer without
   losing one.
4. **Stricter defaults.** PyJWT's `options={"require": [...]}` makes a claim
   *mandatory* rather than merely validated-when-present. It also refuses to
   HMAC-sign with an asymmetric key — it will not even let you *construct* the
   HS256 confusion token, which is why the test for that attack assembles one
   by hand.

> Advisory identifiers move and get re-scored. Re-check them at
> <https://github.com/advisories> before quoting them anywhere that matters.
> The maintenance argument stands on its own.

**What it cost:** nothing at the call site. `jwt.decode(token, key,
algorithms=[...], audience=..., issuer=...)` reads the same either way. The
JWK-to-key conversion is `RSAAlgorithm.from_jwk`, which PyJWT exposes
directly.

If a reviewer requires `python-jose` specifically, the change is confined to
three lines in `backend/src/auth/auth.py` — but it would be a downgrade.

---

## Backend

### Runtime

| Package | Why |
|---|---|
| **Flask 3.1** | The starter pins Flask 2.0 and imports `flask._request_ctx_stack`, which was deprecated in 2.2 and removed in 2.3. The starter's own `auth.py` cannot run on any supported Flask. This project uses `flask.g` instead. |
| **Werkzeug 3.1** | Flask 3's pair. Also the layer that rejects newline-carrying header values before the app sees them. |
| **Flask-SQLAlchemy 3.1 / SQLAlchemy 2.0** | SQLAlchemy 1.4's `Query.get()` and the starter's `db.app = app` are both gone in 3.x. `db.session.get(Model, id)` is the modern form. |
| **Flask-Migrate / Alembic** | Present so a schema change after deployment is a migration rather than a `drop_all`. |
| **Flask-Cors** | Explicit origin allow-list. The API is called with a bearer token, so a wildcard would let any page on the internet make authenticated calls. |
| **PyJWT + cryptography** | See above. |
| **requests** | The Auth0 Management API client, and the JWKS fetch. Chosen over `urllib` (which the starter uses) because it has real timeouts, and because `responses` can intercept it — which is what lets the test suite exercise the whole verification path with no network. |
| **Flask-Limiter** | Rate limiting. Keyed on a hash of the bearer token rather than the source address. |
| **python-dotenv** | Loads `.env`. Imported by `src/config.py` at module level, because the configuration classes read `os.environ` in their class bodies — loading it any later would be too late. |
| **gunicorn** | Production WSGI server for the container and for Azure App Service. |

### Development

| Package | Why |
|---|---|
| **pytest 9 + pytest-cov** | 326 tests, 85% statement coverage. |
| **responses** | Intercepts every outbound `requests` call. Unregistered requests raise, so a test that reaches the internet fails loudly instead of going quiet and slow. |
| **freezegun** | Time travel for expiry tests. |
| **flake8 / black / isort** | PEP 8 enforcement in CI, not by inspection. |
| **bandit** | Source-level security linter. Clean. |
| **pip-audit** | Dependency advisory scanner. Runs on every push. |

---

## Frontend

### Why Angular 22 and Ionic 9

The starter is Angular 7 and Ionic 4, from 2019. Its toolchain (`node-sass`,
webpack 4, `@angular/http`) does not install on Node 20 or later, and
`@angular/http` was removed from Angular itself in version 8. A reviewer
running `npm install` on a current machine gets a wall of build failures
before they reach any of the project work.

Rebuilding on Angular 22 + Ionic 9:

- `ionic serve` actually runs, which is what the rubric asks for;
- it covers the "modify the front end with some unique styles or
  functionality" suggestion;
- standalone components and signals remove the NgModule boilerplate, so the
  app is smaller than the starter despite doing considerably more.

The pieces the specification names are preserved. `environment.ts` keeps the
same shape and the same `url`-is-a-domain-prefix convention, so the values go
where the rubric says they go.

### Dependencies

| Package | Why |
|---|---|
| **@angular/core, common, forms, router 22.1** | The framework. Zoneless change detection, driven by signals. |
| **@ionic/angular 9** | Required by the project. Used for theming, the CSS layer and `ion-app`. |
| **ionicons 8** | Ionic's icon set. |
| **rxjs 7.8** | `HttpClient` returns observables. |
| **TypeScript 6.0** | What Angular 22 requires. |
| **vitest 4** | Angular 22's default runner. 37 tests. |

### What is deliberately absent

| Not used | Why |
|---|---|
| **@auth0/angular-jwt** | Two functions of it were needed: decode a JWT, and check expiry. Both are a dozen lines in `core/oauth.ts`, and writing them made it possible to say precisely, in the code, that decoding is not verifying. |
| **jwt-decode** | Same. |
| **@auth0/auth0-angular** | The official SDK is good, and would be the right choice in production. Implementing PKCE directly is the point of the exercise here, and it is tested against the RFC 7636 reference vector. |
| **lodash** | The starter pulls it in. Nothing here needs it. |
| **A component library beyond Ionic** | Ionic plus about 600 lines of CSS. |

---

## On keeping the pins current

Pinning exactly is what makes a reviewer's install reproducible. It is also
what lets a tree quietly rot: this project was first pinned in early 2025 and
CI's `pip-audit` later flagged **33 advisories across 10 packages**, three of
them in PyJWT itself.

That is not an argument against the library choice -- PyJWT shipped fixes for
all three, which is precisely the property python-jose lacks -- but it is a
reminder that "we picked the secure library" has a shelf life. The pins are
now current, `pip-audit --strict` passes on both requirement files, and CI
fails the build the day that stops being true.

## Supply chain

- Both dependency trees are pinned; `package-lock.json` is committed.
- `pip-audit` and `npm audit` run on every push and fail the build on a
  high-severity advisory.
- `gitleaks` scans each diff for credentials.
- Runtime and development dependencies are separate files, so the container
  image does not ship a test runner and a linter.
- The container runs as a non-root user.

## Keeping them current

```bash
# Backend
cd backend
pip list --outdated
pip-audit

# Frontend
cd frontend
npm outdated
npm audit
npx ng update                 # Angular's own migration tool
```

Angular is upgraded with `ng update`, never by editing `package.json` — it
runs the code migrations that go with each version.
