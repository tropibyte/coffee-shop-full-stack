# Coffee Shop — frontend

Ionic + Angular application for the Coffee Shop menu and staff administration.

- **Runtime:** Node 20+, Angular 22, Ionic 9, TypeScript 6
- **Sign-in:** Auth0, Authorization Code + PKCE (implicit also supported)
- **Tests:** 37, `vitest`

---

## Install

### 1. Node

Angular 22 requires Node 22.22+, 24.15+, or 26+. Check with:

```bash
node --version
npm --version
```

`ng serve` refuses clearly if it is unhappy with the version.

### 2. Dependencies

```bash
cd frontend
npm install
```

That installs from the committed `package-lock.json`, so you get the exact
tree CI tested. The first run takes a minute or two.

### 3. Ionic CLI (optional)

```bash
npm install -g @ionic/cli
```

Only needed for the `ionic` command. `npm start` runs the same dev server if
you would rather not install it globally.

### 4. Configuration

Open `src/environments/environment.ts` and fill in four values:

```typescript
export const environment = {
  production: false,
  apiServerUrl: 'http://127.0.0.1:5000',   // the running Flask API
  auth0: {
    url: 'your-tenant.us',                 // domain WITHOUT .auth0.com
    audience: 'coffee-shop',               // the API Identifier
    clientId: 'aBcD...',                   // the SPA Client ID
    callbackURL: 'http://localhost:8100',  // this app's origin
    useAuthorizationCodePkce: true,
    tokenStorage: 'session',
  },
};
```

Where each value comes from: **[../docs/AUTH0_SETUP.md](../docs/AUTH0_SETUP.md)**.

> **The one that catches everybody:** `url` is the domain *prefix*, without
> `.auth0.com`. That is the starter's convention and this project keeps it. A
> full domain also works — the code tolerates both — but matching the
> convention keeps the two config files reading the same way.

None of these is a secret. A public client id and a tenant domain are both
visible in the browser's network tab, which is exactly why a SPA uses PKCE
rather than a client secret it could not keep.

`environment.prod.ts` is substituted at build time for production builds.

---

## Run

```bash
ionic serve
```

or

```bash
npm start
```

The app opens on <http://localhost:8100>. The backend must be running
separately on the port named in `apiServerUrl`.

The public menu works with no Auth0 configuration at all. Signing in does not
— and the app says so on the menu page rather than failing silently.

### Check it works

Open <http://localhost:8100/diagnostics>. That page reads both
configurations and reports exactly what is missing or mismatched, including
the case where the frontend and the API are pointed at different Auth0
tenants — which otherwise presents only as a puzzling 401.

---

## Build

```bash
npm run build          # production build into www/
npm run build:dev      # development build, unminified
```

`npm run build` applies `environment.prod.ts`, so set your production API URL
and callback there first — and add the production origin to the Auth0
application's Allowed Callback, Logout and Web Origin lists.

Output goes to `www/`, which is what Ionic and the Azure Static Web Apps
workflow expect.

---

## Test

```bash
npm test                   # all 37
npx ng test --watch        # re-run on change
```

The suite concentrates on the logic where a quiet mistake is expensive:

- **`core/oauth.spec.ts`** — JWT decoding (including non-ASCII claims and every
  malformed shape), expiry with skew, and PKCE. The challenge is checked
  against the worked example in **RFC 7636 appendix B**, so a passing test
  means the challenge really is the base64url SHA-256 of the verifier, and not
  merely something a lenient server happened to accept.
- **`core/models.spec.ts`** — the role ladder, including that it matches the
  ranks the backend defines. If one moves and the other does not, the UI and
  the API disagree about who outranks whom.

---

## Layout

```
frontend/src/
├── environments/
│   ├── environment.ts          ← your Auth0 values go here
│   └── environment.prod.ts
├── theme/variables.scss        the Coffee Shop palette, light and dark
├── styles.scss                 Ionic CSS, theme, shared controls
└── app/
    ├── app.ts / .html / .scss  shell: toolbar, nav, session controls
    ├── app.config.ts           providers
    ├── app.routes.ts           lazy routes with permission guards
    ├── core/
    │   ├── auth.service.ts     sign-in, PKCE, token storage, can()
    │   ├── oauth.ts            PKCE and JWT helpers
    │   ├── api.ts              interceptor + typed services
    │   ├── guards.ts           route guards
    │   ├── theme.service.ts    light / dark / follow-system
    │   └── models.ts           shared types and the role ladder
    ├── shared/
    │   └── drink-graphic.component.ts   the glass
    └── pages/
        ├── menu.page.*         the menu, and editing it
        ├── drink-form.*        create/edit dialog with live preview
        ├── people.page.*       user administration
        ├── audit.page.*        the audit trail
        ├── profile.page.*      "Your access"
        └── diagnostics.page.*  configuration checks and the RBAC matrix
```

Every page is lazily loaded, so a barista never downloads the administration
bundle and the first paint carries only the menu.

---

## What the pages do

| Route | Needs | Shows |
|---|---|---|
| `/menu` | — | The menu. Recipes appear with `get:drinks-detail`; editing controls with `post:`/`patch:`/`delete:drinks` |
| `/people` | `get:users` | Accounts junior to yours: invite, re-role, block, delete |
| `/audit` | `get:audit` | Every state-changing action, newest first, expandable |
| `/profile` | any token | What your token claims, and what it grants |
| `/diagnostics` | — | Configuration checks and the live RBAC matrix from the API |

Route guards keep a signed-out visitor off a page that would only show them an
error. They are **not** a security control: every page they protect calls an
API that checks the same permission again. The **Your access** page states
this plainly, and invites you to prove it in devtools.

---

## Design notes

### The drink graphic

Each ingredient becomes a band of a glass, its height proportional to its share
of the total parts — so "one part foam to three parts milk" is legible at a
glance rather than something you work out from a legend.

It is accessible as well as decorative: the SVG carries a text description
listing every layer and its percentage, each band is labelled where there is
room, and the legend repeats the same information as text. Label colour is
chosen by WCAG relative luminance rather than a naive RGB average, so a label
on saturated green and one on saturated blue are both readable.

### Theming

Three states, not two: light, dark, and *follow the system*, which is the
default. Once you pick a side it is pinned on `<html data-theme>` and
remembered, and the system preference stops being consulted.

Every colour is a CSS custom property defined once in `theme/variables.scss`,
so dark mode is a redefinition rather than overrides scattered through
component stylesheets.

### `ion-app` and scrolling

Ionic styles `body` as `position: fixed; overflow: hidden`, and `ion-app` as an
absolutely positioned `contain: size` viewport box, on the assumption that an
`ion-content` inside does the scrolling. This app uses a sticky header over
ordinary document flow, so `styles.scss` returns both to normal layout.

The override targets `ion-app.ion-page` because `.ion-page` — a class Ionic
adds at runtime — is what actually applies those rules, and a class outranks
an element selector. Ionic's theming, components and CSS variables are
untouched.

### Why PKCE

The implicit flow returns the access token in the URL fragment, where it lands
in browser history and in the referrer of the next outbound link. Authorization
Code + PKCE returns a single-use code bound to a verifier that never leaves the
tab. The app implements both; `useAuthorizationCodePkce: false` switches to
implicit if your tenant requires it.

The app also verifies the `state` parameter against a per-attempt random value,
and scrubs the code out of the address bar as soon as it is exchanged.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Menu is empty, no error | The API is not running. `/diagnostics` will say so |
| "Auth0 is not configured yet" | `environment.ts` still has blanks |
| `Callback URL mismatch` from Auth0 | The origin is not in Allowed Callback URLs. `localhost` and `127.0.0.1` are different entries |
| Sign-in returns you signed out | The Auth0 application is not type **Single Page Web Applications** |
| CORS error in the console | Add this origin to `CORS_ORIGINS` in `backend/.env` |
| Signed in but nothing new appears | The role has no permissions, or "Add Permissions in the Access Token" is off. Check **Your access** |
| Buttons appear but return 403 | The token is stale after a role change. Sign out and in; **Your access** flags this explicitly |
| `ng serve` refuses to start | Node is too old for Angular 22 |
