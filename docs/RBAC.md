# Access control

How this system decides who may do what, and why it is shaped this way.

---

## The two layers

Authorisation happens twice, and both must pass.

### Layer 1 — capability

`@requires_auth('patch:users')` asks Auth0 a yes/no question: *may this caller
edit users at all?* The answer comes from the `permissions` array Auth0 puts in
the access token, which it derives from the roles assigned to that user.

This is the layer the project specification describes, and on its own it is
enough for the menu.

### Layer 2 — rank

It is not enough for people.

A permission is a **verb**. `patch:users` says "may edit users". It cannot say
"may edit users *junior to you*", because that is a **relationship** between
two accounts, and a token can only describe one of them.

So every view that touches a user compares the caller's rank with the target's:

```
Administrator  rank 2   may administer rank 0 and 1
Manager        rank 1   may administer rank 0
Barista        rank 0   may administer nobody
(no role)      rank -1  may administer nobody
```

Three rules, enforced in `backend/src/management/users_api.py`:

1. **Strictly junior targets only.** `target_rank >= actor_rank` → `403
   insufficient_rank`. Peers are included: two administrators cannot fight.
2. **Strictly junior grants only.** Granting a role at or above your own rank
   → `403 privilege_escalation_blocked`. An administrator may create a manager
   but not another administrator.
3. **Never yourself.** Acting on your own `sub` → `403
   self_administration_forbidden`, whatever your rank. Self-service would let
   a manager unblock their own suspended account, which defeats the point of
   having someone senior.

---

## The matrix

### Menu

| Operation | Route | Permission | Public | Barista | Manager | Administrator |
|---|---|---|:---:|:---:|:---:|:---:|
| See the menu | `GET /drinks` | — | 200 | 200 | 200 | 200 |
| See recipes | `GET /drinks-detail` | `get:drinks-detail` | **401** | 200 | 200 | 200 |
| Add a drink | `POST /drinks` | `post:drinks` | **401** | **403** | 200 | 200 |
| Edit a drink | `PATCH /drinks/<id>` | `patch:drinks` | **401** | **403** | 200 | 200 |
| Delete a drink | `DELETE /drinks/<id>` | `delete:drinks` | **401** | **403** | 200 | 200 |

### People

| Operation | Route | Permission | Public | Barista | Manager | Administrator |
|---|---|---|:---:|:---:|:---:|:---:|
| Own identity | `GET /users/me` | any token | **401** | 200 | 200 | 200 |
| List users | `GET /users` | `get:users` | **401** | **403** | 200 † | 200 † |
| Grantable roles | `GET /roles` | `get:roles` | **401** | **403** | 200 ‡ | 200 ‡ |
| Invite | `POST /users` | `post:users` | **401** | **403** | 201 ‡ | 201 ‡ |
| Re-role / block | `PATCH /users/<id>` | `patch:users` | **401** | **403** | 200 † | 200 † |
| Delete | `DELETE /users/<id>` | `delete:users` | **401** | **403** | 200 † | 200 † |
| Audit trail | `GET /audit` | `get:audit` | **401** | **403** | **403** | 200 |

† Only for accounts strictly junior to the caller. Anything else is `403
insufficient_rank`, except a *read* of a senior account, which is `404` —
confirming that an administrator exists is itself information a junior caller
has no business collecting.

‡ Only for roles strictly junior to the caller. `GET /roles` filters the list
before returning it, so the UI never offers a choice the API would refuse.

The whole of this table is executed as a test:
`backend/tests/test_rbac.py::test_rbac_matrix` drives every cell through the
real HTTP stack.

---

## Worked examples

### A manager tries to promote themselves

```http
PATCH /users/auth0|manager-self
Authorization: Bearer <manager token>

{"role": "Administrator"}
```

Layer 1 passes: the manager holds `patch:users`.
Layer 2 rejects it twice over — self-administration, and escalation:

```json
{ "success": false, "error": 403, "code": "self_administration_forbidden" }
```

### A manager tries to promote a barista to manager

```http
PATCH /users/auth0|barista
{"role": "Manager"}
```

The target is junior, so rule 1 passes. Rule 2 does not: `Manager` is rank 1
and so is the caller.

```json
{ "success": false, "error": 403, "code": "privilege_escalation_blocked" }
```

An administrator making the same call succeeds — rank 2 outranks rank 1.

### A manager tries to list administrators

```http
GET /users
```

Returns 200 with the administrators filtered out. The filtering happens in the
API, not the UI, so it holds for `curl` as well as for the browser.

### Someone forges a roles claim

The roles claim is a *narrowing* input, never a widening one. Capability still
comes from the Auth0-signed `permissions` array, which cannot be edited without
invalidating the signature. A token claiming `["Administrator"]` with no
permissions gets 403 at the decorator, before any rank is considered.

Asserted in `test_users_api.py::test_a_forged_roles_claim_does_not_grant_capability`.

---

## Why "no role" is rank -1 rather than an error

A Google account signing in for the first time has no role. Treating that as
an error would mean a broken page; treating it as rank 0 would silently make
every new arrival a barista.

Rank −1 means: you are authenticated, you can see the public menu, and a
manager can give you a role when they are ready. It is the least-privilege
default, and it makes the Google sign-in flow work without a special case.

---

## Where each rule lives

| Rule | File |
|---|---|
| Header parsing, 401 vs 403 | `backend/src/auth/auth.py` |
| Signature, issuer, audience, expiry | `backend/src/auth/auth.py::verify_decode_jwt` |
| Permission lookup | `backend/src/auth/auth.py::check_permissions` |
| Rank ladder | `backend/src/management/users_api.py::ROLE_RANKS` |
| Target rank rule | `users_api.py::_assert_may_administer` |
| Grant rank rule | `users_api.py::_assert_may_grant` |
| Live route map | `backend/src/health.py::rbac_matrix` |
| UI gating | `frontend/src/app/core/guards.ts` |

The frontend's checks decide what to *render*. They are not a control: every
page they protect calls an API that checks the same permission again, and a
user who edits their token in devtools gets buttons that return 403. The
**Your access** page says so in as many words.

---

## Adding a permission

1. Define it on the Auth0 API (**Permissions** tab).
2. Add it to whichever roles should hold it.
3. Add it to `KNOWN_PERMISSIONS` in `backend/src/auth/auth.py`.
4. Use it: `@requires_auth('your:permission')`.
5. Add it to the `Permission` union in `frontend/src/app/core/models.ts`.

Step 3 is not optional. `requires_auth` raises `ValueError` at import time for
a permission it does not recognise, so a typo becomes a startup failure rather
than an endpoint nobody can reach and nobody notices.
