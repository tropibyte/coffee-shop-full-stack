# Threat model

What this system is worth attacking for, how someone would try, and what stops
them. Organised by STRIDE, with a test named for each control so the claims
here are checkable rather than aspirational.

---

## What is being protected

| Asset | Why it matters | Loss if compromised |
|---|---|---|
| Drink recipes | The only non-public data in the menu | Competitive; low severity, but it is the thing the barista role exists to gate |
| Menu integrity | Customers act on it | Defacement, or a drink listing an allergen it does not contain |
| The Auth0 tenant | Holds every staff account | **Highest.** Tenant control is control of the business |
| The M2M client secret | Can create, re-role and delete any user | **Highest.** Equivalent to tenant control |
| Access tokens | Bearer credentials | Impersonation for the token's lifetime |
| The audit trail | The record of what happened | An attacker who can edit it can erase themselves |

## Who would attack it

| Actor | Capability | Goal |
|---|---|---|
| Curious customer | Browser devtools | See recipes; try the admin URLs |
| Disgruntled barista | A valid barista token | Edit or delete drinks |
| Ambitious manager | A valid manager token | Promote themselves; delete a rival |
| Opportunist on the network | Can read URLs, referrers, history | Capture a token |
| Automated scanner | Untargeted, high volume | Any of the standard JWT and injection flaws |

## Trust boundaries

1. **Browser → API.** Everything crossing it is untrusted, including the
   token, which is why the signature is checked every time.
2. **API → Auth0.** Trusted for identity, over TLS, pinned by audience and
   issuer. An outage here is a 503, not an open door.
3. **API → database.** Trusted, but writes are validated first so the
   database never stores something a later reader would execute.
4. **Browser ↔ browser storage.** Not a boundary at all. Anything in
   `sessionStorage` is readable by any script on the origin.

---

## S — Spoofing

### Forged or altered tokens

| Attack | Control | Test |
|---|---|---|
| Invent a token | RS256 signature checked against the tenant's published JWKS on every request | `test_signature_from_an_unknown_key` |
| Sign with your own key, claim a real `kid` | The `kid` selects the key; the signature is still verified against *that* key | `test_signature_forged_under_a_published_kid` |
| Set `alg: none` | Algorithm allow-list checked **before** key lookup; `none` is rejected at configuration load too | `test_algorithm_none_is_refused` |
| HS256 confusion: HMAC-sign with the tenant's public key | Same allow-list. Only RS256 is accepted, so a symmetric token never reaches verification | `test_hs256_confusion_is_refused` |
| Configure HS256 alongside RS256 | `Config.validate` refuses to start | `test_symmetric_algorithms_are_refused` |
| Replay a token from another API in the same tenant | `aud` is required and checked | `test_wrong_audience` |
| Token from an attacker's own Auth0 tenant | `iss` is required and checked | `test_wrong_issuer` |
| Omit a claim to skip its check | `exp`, `iat`, `iss`, `aud`, `sub` are **required**, not merely validated when present | `test_required_claims_are_required` |

That last one is the subtle one. A verifier that checks `aud` *if present*
accepts a token with no `aud` at all.

### Replay after expiry

`exp` is enforced with 10 seconds of leeway for clock skew. Expiry reads as
`401 token_expired`, never 403, so a client knows to re-authenticate rather
than conclude it lacks a permission.
*Tests:* `test_expired_token`, `test_expired_manager_token_is_401_not_403`.

### Token capture in transit

Authorization Code + PKCE is the default. The implicit flow puts the access
token in the URL fragment, where it enters browser history and the referrer of
the next outbound link. PKCE returns a single-use code bound to a verifier
that never leaves the tab.

The app also scrubs the code and any token out of the address bar with
`history.replaceState` as soon as the redirect is processed
(`auth.service.ts::scrubUrl`), and verifies the `state` parameter against a
per-attempt random value before accepting anything.

---

## T — Tampering

| Attack | Control |
|---|---|
| Store CSS in an ingredient colour, which the UI interpolates into a style binding | Colours are constrained at the model boundary to hex, `rgb()`/`rgba()`, or a CSS colour name. `url(...)`, `expression(...)`, `var(--x)` and `red; background: url(...)` are all rejected with 422 |
| Smuggle extra fields into a recipe | Validation returns a new object with exactly `name`, `color`, `parts`. Anything else is dropped, not stored |
| `"parts": true` | `bool` is a subclass of `int` in Python, so a naive numeric check accepts `True` as 1. Checked explicitly |
| SQL injection via `search` or `sort` | Parameterised queries throughout; `sort` is an allow-list of column objects, never string interpolation |
| `%` in a search term to match the whole table | LIKE wildcards are escaped |
| Oversized body to exhaust memory | `MAX_CONTENT_LENGTH` rejects before parsing; recipe size and ingredient count bounded after |
| Forge log lines via `X-Request-Id` | Client-supplied ids are accepted only if alphanumeric-with-hyphens and ≤64 characters; anything else is replaced |

*Tests:* `TestRecipeColourValidation`, `test_search_wildcards_are_escaped`,
`test_invalid_sort_is_400`, `test_boolean_parts_are_not_a_number`,
`test_a_dirty_client_supplied_id_is_replaced`.

### Tampering with the audit trail

The `AuditEvent` model has no `update` or `delete` helper, and no endpoint
writes to one. Every event is also emitted to the structured log, which leaves
the process — an attacker with database access still has to reach the log
collector separately.

---

## R — Repudiation

"I did not delete that drink."

Every state-changing and privileged request writes an audit row naming the
actor's `sub`, the permission that authorised it, the resource, the HTTP
status, the request id, and a scrubbed detail blob. Deletes capture the full
record *before* destroying it, so the trail can still say what was lost.

Reading the trail requires `get:audit`, which only administrators hold — a
manager appears in this log and should not be the one reading it.

Secrets are stripped recursively before storage (`audit.py::_scrub`), so no
token, password or client secret can end up in a detail blob.

*Tests:* `TestAuditCapture`, `TestAuditRedaction`,
`test_a_deleted_drink_is_still_described_by_its_audit_row`.

---

## I — Information disclosure

| Leak | Control |
|---|---|
| Recipes to the public | `short()` omits `name`. Withholding it is the endpoint's whole purpose, and it is asserted, not assumed |
| Stack traces and SQL in error bodies | One handler renders every exception as an opaque 500; the trace goes to the log with a correlation id |
| Auth0 user objects carrying IdP tokens and app metadata | `_present_user` is an allow-list of eleven fields, so a field Auth0 adds later cannot appear by default |
| Existence of senior accounts | A junior caller reading a senior account gets **404**, not 403. 403 would confirm it exists |
| Upstream Auth0 error bodies | Translated to generic messages; the detail goes to the log |
| Secrets in `/health` | Publishes only the tenant domain (public), audience (in every token) and feature flags. Asserted to contain no `secret`, `password` or `client_secret` |
| Tokens in logs | Nothing logs the Authorization header. The rate limiter keys on a SHA-256 *hash* of the token |

*Tests:* `TestErrorDisclosure`, `test_user_projection_is_an_allow_list`,
`test_a_senior_account_reads_as_absent_not_forbidden`,
`test_health_publishes_no_secrets`,
`test_upstream_500_becomes_502_without_the_body`.

---

## D — Denial of service

| Attack | Control |
|---|---|
| Flood the write endpoints | Rate limit, keyed per token rather than per IP so one noisy client cannot spend another's budget or throttle a whole office behind one NAT address |
| Large request bodies | `MAX_CONTENT_LENGTH` = 256 KB |
| Deeply nested or enormous recipes | 20 ingredients, 4 KB serialised, 60-character names |
| **Turn the API into a JWKS request amplifier** | An unknown `kid` triggers at most one JWKS refresh per `JWKS_MIN_REFRESH_INTERVAL_SECONDS`. Without that floor, a stream of forged key ids would make this service attack Auth0 on the attacker's behalf |
| Auth0 slowness stalling every request | 5-second JWKS timeout, 10-minute cache, so the common path never touches the network |
| Unbounded caches | The caller-role cache is bounded at 512 entries and cleared when it overflows |

*Tests:* `test_repeated_unknown_kids_do_not_hammer_auth0`,
`test_keys_are_fetched_once_across_many_tokens`,
`test_oversized_body_is_rejected`.

Auth0 being unreachable returns **503**, not 401 — an upstream outage is not
the caller's fault and should not send a perfectly good client off to
re-authenticate pointlessly. *Test:* `test_unreachable_jwks_is_503_not_401`.

---

## E — Elevation of privilege

The centre of this design. See [RBAC.md](RBAC.md) for the full matrix.

| Attack | Control | Test |
|---|---|---|
| Manager promotes themselves | Self-administration refused at any rank | `test_nobody_may_administer_themselves` |
| Manager promotes a barista to manager | May only grant strictly junior roles | `test_manager_cannot_promote_a_barista_to_manager` |
| Administrator creates another administrator | Same rule; peers are not junior | `test_administrator_cannot_create_an_administrator` |
| Manager deletes an administrator | May only act on strictly junior targets | `test_manager_may_not_delete_an_administrator` |
| Administrator deletes a peer | Same | `test_administrator_may_not_edit_another_administrator` |
| Spell the role `MANAGER` to dodge the check | Role names are canonicalised before comparison | `test_role_case_does_not_open_a_side_door` |
| Forge the roles claim | Capability comes from the signed `permissions` array; the roles claim only narrows | `test_a_forged_roles_claim_does_not_grant_capability` |
| Promotion leaves the old role attached | Every recognised role is stripped before the new one is granted | `test_administrator_may_promote_a_barista_to_manager` |
| Send a `password` when inviting | The field is ignored; a random secret is generated and discarded | `test_no_password_is_accepted_from_the_caller` |
| Half-created account with no role | Creation is rolled back if the role assignment fails | `test_a_failed_role_assignment_rolls_the_account_back` |

That last one is a privilege issue in the other direction: an account with no
role is an account nobody can clean up through the UI.

---

## Deliberate residual risks

Naming them is part of the model.

### 1. Tokens live in browser storage

`sessionStorage` by default, cleared when the tab closes. It is readable by
any script on the origin, so this is XSS-dependent — which is why the app has
no `innerHTML` sink, constrains the one user-controlled value that reaches
CSS, and ships `default-src 'none'` on every JSON response.

*Not mitigated:* a successful XSS still yields the token. The real fix is
refresh tokens in an httpOnly cookie with a backend-for-frontend, which is a
larger architecture than this project.

### 2. A revoked role stays valid until the token expires

Tokens are stateless by design. Demoting someone does not invalidate the token
they already hold; they keep their old permissions for up to 24 hours.

*Partly mitigated:* the **Your access** page detects the divergence and tells
the user to sign in again. Blocking a user in Auth0 stops new tokens
immediately.
*Fully fixing it* needs token introspection on every request, trading the
statelessness the design is built on.

### 3. SQLite

Fine for this project and for the deployment target. It does not survive
horizontal scaling. `DATABASE_URL` accepts any SQLAlchemy URL, so the move to
Postgres is configuration, not code.

### 4. In-memory rate limiting

`memory://` is per-process. Two instances mean two independent budgets. Set
`RATELIMIT_STORAGE_URI` to a Redis URL to fix it; the code needs no change.

### 5. Auth0 development keys for Google sign-in

The setup guide suggests leaving Google's client id and secret blank, which
uses Auth0's shared development keys. Auth0 rate-limits those and says not to
use them in production. Correct — for a graded demo it is fine, and the guide
says so.

---

## What is checked automatically

| Check | Tool | Where |
|---|---|---|
| Source-level security patterns | `bandit` | CI, every push |
| Dependency advisories | `pip-audit`, `npm audit` | CI, every push |
| The whole RBAC matrix | `pytest -m rbac` | CI |
| Attack-specific regressions | `pytest -m security` | CI |
| Secrets in the diff | `gitleaks` | CI |
