# Auth0 setup, step by step

Everything in this project that is not code lives here. Work through it once
and you will have: an API, three roles, ten permissions, a single-page
application, a machine-to-machine application, an Action that puts role names
into the token, three test users, multi-factor authentication, and Google
sign-in.

Budget about 25 minutes. At the end there is a **[Checklist](#checklist)** and
a **[Troubleshooting](#troubleshooting)** section keyed to the exact error
messages this API returns.

> **A note on who does what.** Creating the account and typing the passwords is
> your job — those are credentials, and they should never pass through anything
> but your own keyboard. Everything downstream of that (the roles, the Action,
> the config files) is either scripted in [`auth0/`](../auth0/) or laid out
> click by click below.

---

## 0. Before you start

You need an Auth0 account. The free tier covers everything here.

1. Go to <https://auth0.com> and sign up, or sign in.
2. When asked for a **tenant domain**, pick something like
   `coffee-shop-yourname`. You cannot rename a tenant later, though you can
   make a new one.
3. Choose a **region**. Any is fine; the region becomes part of your domain
   (`coffee-shop-yourname.us.auth0.com`).

Write your full domain down. It appears in four places later.

```
AUTH0_DOMAIN = ____________________________.auth0.com
```

---

## 1. Create the API

This is the thing tokens are *for*. Its identifier becomes the `aud` claim,
and this project refuses any token issued for a different audience.

1. In the left sidebar: **Applications → APIs → + Create API**.
2. Fill in:

   | Field | Value |
   |---|---|
   | Name | `Coffee Shop` |
   | Identifier | `coffee-shop` |
   | Signing Algorithm | `RS256` |

   The identifier is not a URL and is never resolved — it is just a string
   both sides agree on. Keep it exactly `coffee-shop` and the defaults in this
   repo will work unchanged.

3. Click **Create**.

### 1a. Turn on RBAC

Open your new API, go to the **Settings** tab, scroll to **RBAC Settings**:

- [x] **Enable RBAC**
- [x] **Add Permissions in the Access Token**

Then **Save**.

> **Both switches matter.** The first makes Auth0 enforce role assignments.
> The second is what puts a `permissions` array into the token. With RBAC on
> and permissions off, every request gets `403 invalid_permissions_claim` —
> the API can tell who you are but has nothing to authorise you with.

### 1b. Add the permissions

Still in the API, go to the **Permissions** tab and add these ten, one at a
time:

| Permission | Description |
|---|---|
| `get:drinks-detail` | Read full drink recipes |
| `post:drinks` | Add a drink to the menu |
| `patch:drinks` | Edit an existing drink |
| `delete:drinks` | Remove a drink |
| `get:users` | List accounts junior to yours |
| `post:users` | Invite a new team member |
| `patch:users` | Change a junior account |
| `delete:users` | Delete a junior account |
| `get:roles` | See which roles you may grant |
| `get:audit` | Read the audit trail |

The first four are the project requirement. The rest power the user
administration and audit features.

> There is deliberately **no permission for `GET /drinks`**. The public menu is
> public; that is the point of the project.

---

## 2. Create the roles

**User Management → Roles → + Create Role.** Do this three times.

### Barista

- Name: `Barista`
- Description: `Can see the menu and full recipes.`

Create it, open it, go to **Permissions → Add Permissions**, select the
**Coffee Shop** API, and tick:

- `get:drinks-detail`

That is all. A barista reads recipes and changes nothing.

### Manager

- Name: `Manager`
- Description: `Full menu control; can administer baristas.`

Permissions:

- `get:drinks-detail`
- `post:drinks`
- `patch:drinks`
- `delete:drinks`
- `get:users`
- `post:users`
- `patch:users`
- `delete:users`
- `get:roles`

### Administrator

- Name: `Administrator`
- Description: `Everything a manager can do, plus managing managers and reading the audit trail.`

Permissions: **all ten**.

### Why Manager and Administrator hold the same user permissions

A permission is a verb — "may edit users" — and cannot express "may edit users
*junior to you*". So the API enforces the hierarchy in a second layer, by
comparing role ranks:

```
Administrator (2)  →  may administer Manager and Barista
Manager       (1)  →  may administer Barista
Barista       (0)  →  may administer nobody
```

A manager holding `patch:users` still gets `403 insufficient_rank` when they
aim it at another manager, and `403 privilege_escalation_blocked` if they try
to grant the Manager role. Nobody, at any rank, may administer themselves or a
peer. See [RBAC.md](RBAC.md) for the full matrix.

---

## 3. Create the Single Page Application

This is the Ionic frontend.

1. **Applications → Applications → + Create Application**.
2. Name: `Coffee Shop Web`.
3. Type: **Single Page Web Applications**. Click **Create**.
4. Skip the quickstart; go straight to **Settings**.

Copy the **Client ID** — you will need it shortly.

Scroll down to **Application URIs** and set all three. These are
comma-separated lists; include every origin you will run from.

| Field | Value |
|---|---|
| Allowed Callback URLs | `http://localhost:8100, http://127.0.0.1:8100` |
| Allowed Logout URLs | `http://localhost:8100, http://127.0.0.1:8100` |
| Allowed Web Origins | `http://localhost:8100, http://127.0.0.1:8100` |

When you deploy, come back and append your production origin to all three.

Then scroll to **Advanced Settings → Grant Types** and confirm:

- [x] **Authorization Code** — the flow this app uses
- [x] **Refresh Token**
- [x] **Implicit** — only needed if you set `useAuthorizationCodePkce: false`

**Save Changes.**

> **Why Authorization Code and not Implicit?** The implicit flow returns the
> access token in the URL fragment, where it lands in browser history and in
> the referrer of the next outbound link. Authorization Code with PKCE returns
> a single-use code bound to a secret that never left the browser tab. The app
> implements both; PKCE is the default. Auth0 requires no client secret for a
> SPA because a public client cannot keep one.

---

## 4. Create the Machine-to-Machine application

This is what lets the API manage users on your behalf. **Its secret is a real
secret** — it goes in `.env`, which is gitignored, and nowhere else.

1. **Applications → Applications → + Create Application**.
2. Name: `Coffee Shop Management`.
3. Type: **Machine to Machine Applications**. Click **Create**.
4. When asked which API to authorise, choose **Auth0 Management API**
   (not your Coffee Shop API).
5. In the scopes list, tick exactly these eight:

   - `read:users`
   - `update:users`
   - `create:users`
   - `delete:users`
   - `read:roles`
   - `read:role_members`
   - `create:role_members`
   - `delete:role_members`

   and this one, which lets the API issue password-setup links so no password
   ever passes through it:

   - `create:user_tickets`

6. **Authorize.**

From the application's **Settings** tab, copy the **Client ID** and the
**Client Secret**.

> Grant nothing beyond this list. These nine scopes are what the user
> administration endpoints actually call; anything more is standing authority
> you would have to defend later.

---

## 5. Add the Action that puts roles in the token

Auth0 puts *permissions* in the access token but not *role names*. The API can
work without role names — it falls back to a Management API lookup — but that
costs a round trip on requests that need to know your rank. This Action
removes that.

1. **Actions → Library → + Build Custom**.
2. Name: `Add roles to access token`.
3. Trigger: **Login / Post Login**. Runtime: the default Node version.
4. Click **Create**, then replace the editor contents with:

```javascript
/**
 * Adds the user's role names to the access token as a namespaced claim.
 *
 * Auth0 silently drops custom claims that are not namespaced with a URI, so
 * the prefix is mandatory rather than stylistic. The API reads this claim to
 * decide the caller's rank; if it is absent it falls back to a Management API
 * lookup, so a tenant without this Action is slower, not broken.
 */
exports.onExecutePostLogin = async (event, api) => {
  const namespace = 'https://coffee-shop.api';
  const roles = event.authorization?.roles ?? [];

  api.accessToken.setCustomClaim(`${namespace}/roles`, roles);
  api.idToken.setCustomClaim(`${namespace}/roles`, roles);
};
```

5. **Deploy**.
6. Go to **Actions → Triggers → post-login**, drag `Add roles to access token`
   from the right-hand panel into the flow between **Start** and **Complete**,
   and click **Apply**.

The claim name must stay `https://coffee-shop.api/roles`; it is referenced by
`ROLES_CLAIM` in `backend/src/management/users_api.py` and by
`ROLES_CLAIM` in `frontend/src/app/core/oauth.ts`.

---

## 6. Create the test users

**User Management → Users → + Create User.** Make three, all on the
`Username-Password-Authentication` connection.

| Email | Role to assign |
|---|---|
| `barista@coffeeshop.test` | Barista |
| `manager@coffeeshop.test` | Manager |
| `admin@coffeeshop.test` | Administrator |

Use a different password for each, and use a password manager. These accounts
can change your menu.

For each user: open them, go to the **Roles** tab, **Assign Roles**, pick the
matching role.

> `.test` is a reserved TLD that can never resolve, so these addresses cannot
> accidentally mail a real person.

---

## 7. Turn on multi-factor authentication

One of the project's stand-out suggestions, and about four clicks.

1. **Security → Multi-factor Auth**.
2. Enable at least one factor. **One-time Password** (any authenticator app)
   needs no phone number or paid add-on, so it is the easiest to demonstrate.
3. Under **Define policies**, choose:
   - **Always** to force MFA for everyone — best for a demo, since a reviewer
     will see it immediately; or
   - **Use Adaptive MFA** to prompt only on risky sign-ins.

Set it to **Always** while you are being marked, then relax it afterwards.

---

## 8. Turn on Google sign-in

The other half of that stand-out suggestion.

1. **Authentication → Social → + Create Connection → Google / Gmail**.
2. For a demo you may leave the Client ID and Secret blank, which uses Auth0's
   shared development keys. Auth0 will warn you that these are rate-limited
   and must not be used in production — that warning is correct, and for a
   graded demo it is fine.
3. On the **Applications** tab of the connection, enable **Coffee Shop Web**.
4. **Save**.

A Google account signing in for the first time arrives with **no role**, which
is the right default: it can see the public menu and nothing else, until a
manager or administrator grants it one.

---

## 9. Wire the values into the project

Four values, four destinations.

### Backend — `backend/.env`

```bash
cp backend/.env.example backend/.env    # PowerShell: Copy-Item backend\.env.example backend\.env
```

Then edit `backend/.env`:

```ini
AUTH0_DOMAIN=coffee-shop-yourname.us.auth0.com
AUTH0_API_AUDIENCE=coffee-shop
AUTH0_CLIENT_ID=<SPA Client ID from step 3>

MANAGEMENT_API_ENABLED=true
AUTH0_M2M_CLIENT_ID=<M2M Client ID from step 4>
AUTH0_M2M_CLIENT_SECRET=<M2M Client Secret from step 4>
```

`backend/.env` is gitignored. Keep it that way — the M2M secret can create and
delete users in your tenant.

### Frontend — `frontend/src/environments/environment.ts`

```typescript
export const environment = {
  production: false,
  apiServerUrl: 'http://127.0.0.1:5000',
  auth0: {
    url: 'coffee-shop-yourname.us',   // domain WITHOUT the .auth0.com suffix
    audience: 'coffee-shop',
    clientId: '<SPA Client ID from step 3>',
    callbackURL: 'http://localhost:8100',
    useAuthorizationCodePkce: true,
    tokenStorage: 'session',
  },
};
```

> **The one that catches everybody:** `url` is the domain *prefix*, without
> `.auth0.com`. The starter code appended the suffix itself and this project
> keeps that convention. If you paste the full domain the app handles it, but
> match the convention so the two files read the same way.

### Check your work

Start both servers and open <http://localhost:8100/diagnostics>. That page
reads both configurations and tells you exactly which value is missing or
mismatched — including the case where the frontend and the API are pointed at
different tenants, which otherwise presents only as a puzzling 401.

---

## 10. Get tokens for Postman

The reviewer needs a barista token and a manager token pasted into the
collection.

1. Start the frontend (`ionic serve` in `frontend/`).
2. Sign in as `barista@coffeeshop.test`.
3. Open **Your access** in the nav, click **Show claims**, and copy the token
   — or take it from devtools: **Application → Session Storage →
   `coffee_shop.access_token`**.
4. Sign out, sign in as the manager, repeat. Then the administrator.
5. Inject all three into the collection:

   ```bash
   python backend/scripts/set_postman_tokens.py \
     --barista "<token>" --manager "<token>" --admin "<token>"
   ```

   That writes each token into its folder's Authorization tab *and* into the
   matching collection variable, which is what the rubric asks for.

> **Tokens last 24 hours by default** (the API's **Token Expiration** setting).
> Refresh them shortly before you submit so the reviewer does not open the
> collection to a wall of 401s. To buy more room, raise **Token Expiration**
> on the Coffee Shop API to 86400 seconds.

---

## Checklist

Tick these off and everything downstream works.

- [ ] Tenant created; full domain written down
- [ ] API `Coffee Shop` with identifier `coffee-shop`, RS256
- [ ] **Enable RBAC** on
- [ ] **Add Permissions in the Access Token** on
- [ ] All ten permissions defined
- [ ] Role `Barista` with 1 permission
- [ ] Role `Manager` with 9 permissions
- [ ] Role `Administrator` with 10 permissions
- [ ] SPA `Coffee Shop Web` created; Client ID copied
- [ ] Callback, Logout and Web Origin URLs all set to the Ionic origin
- [ ] Authorization Code grant enabled
- [ ] M2M `Coffee Shop Management` created, authorised for the Management API
- [ ] Exactly the nine Management scopes granted
- [ ] Action deployed **and added to the post-login trigger**
- [ ] Three test users created and assigned roles
- [ ] MFA enabled
- [ ] Google connection enabled for the SPA
- [ ] `backend/.env` filled in
- [ ] `frontend/src/environments/environment.ts` filled in
- [ ] `/diagnostics` shows all green
- [ ] Postman collection carries fresh tokens

---

## Troubleshooting

Every message below is one this API actually emits, with the setting that
causes it.

| What you see | What it means | Fix |
|---|---|---|
| `403 invalid_permissions_claim` | The token has no `permissions` array. | Step 1a — **Add Permissions in the Access Token** is off. |
| `401 invalid_claims` — "check the audience and issuer" | The token was issued for a different API, or a different tenant. | `AUTH0_API_AUDIENCE` must equal the API Identifier exactly, and `audience` in `environment.ts` must match both. |
| `401 invalid_header` — "Unable to find the appropriate signing key" | The API is verifying against a different tenant's keys. | `AUTH0_DOMAIN` in `.env` and `url` in `environment.ts` must name the same tenant. `/diagnostics` flags this explicitly. |
| `403 unauthorized: Permission not found: post:drinks` | Correct token, role lacks the permission. | Step 2 — add it to the role, then sign out and in again for a fresh token. |
| `Callback URL mismatch` on the Auth0 page | The redirect URI is not on the allow-list. | Step 3 — it must match **exactly**, including scheme and port. `localhost` and `127.0.0.1` are different entries. |
| Sign-in loops, or lands back signed out | The authorization code could not be exchanged. | Step 3 — the application type must be **Single Page Web Applications**. A "Regular Web Application" expects a client secret. |
| `401 token_expired` | More than 24 hours since the token was issued. | Sign in again. For Postman, re-run `set_postman_tokens.py`. |
| Everything 403s right after a role change | The old token still carries the old roles. | A token is a snapshot. Sign out and in. The **Your access** page shows this explicitly when the token and the server disagree. |
| `503` on `/users` | The Management API is unreachable or unconfigured. | Step 4 — check `AUTH0_M2M_CLIENT_ID` / `SECRET`, or set `MANAGEMENT_API_ENABLED=false` to run without user administration. |
| `502` on `/users` | Auth0 rejected a Management call. | The M2M application is missing a scope. Re-check the nine in step 4. |
| CORS error in the browser console | The API does not recognise the frontend's origin. | Add it to `CORS_ORIGINS` in `backend/.env`. |

---

## Doing it as code instead

If you would rather not click, [`auth0/`](../auth0/) contains a Terraform
configuration that creates the API, the permissions, the three roles, both
applications and the Action from one `terraform apply`. It needs a
bootstrapping M2M application first, so step 4 still happens by hand; the rest
does not. See [`auth0/README.md`](../auth0/README.md).
