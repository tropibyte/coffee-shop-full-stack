# Risk and compliance

The project specification lists "applying software system risk and compliance
principles" among the skills being demonstrated. This is that: a risk
register, a control mapping, and an honest account of what a real deployment
would still need.

A campus coffee menu is not a regulated system. The point of writing this down
is that the *habit* transfers — the same register, applied to something that
handles payments or health records, is what an auditor asks for first.

---

## Data inventory

You cannot protect what you have not listed.

| Data | Classification | Where it lives | Retention |
|---|---|---|---|
| Drink titles and recipes | Public / Internal | This project's database | Indefinite |
| Staff email addresses | **Personal data** | Auth0 only | While employed |
| Staff names | **Personal data** | Auth0 only | While employed |
| Auth0 `sub` identifiers | Pseudonymous | Audit trail | Life of the trail |
| Login counts, last login | Personal data | Auth0 only | Auth0's policy |
| Access tokens | Credential | Browser session storage | ≤ 24 hours |
| M2M client secret | Credential | `.env` / Azure app settings | Until rotated |

**No credential is ever stored by this project.** Passwords are Auth0's, and
the invitation flow means one never passes through this API even in transit:
the account is created with a random secret that is discarded, and the user
sets their own through an Auth0 ticket.

**The only personal data this project stores is the `sub` claim**, in audit
rows. Names and emails are read from Auth0 on demand and never persisted.
That is deliberate: it keeps the erasure story simple.

---

## Risk register

Likelihood and impact on 1–5; risk is their product. Residual risk is after
the stated controls.

| # | Risk | L | I | Risk | Controls | Residual |
|---|---|:-:|:-:|:-:|---|:-:|
| R1 | Forged or altered token accepted | 3 | 5 | **15** | RS256 against published JWKS; algorithm allow-list checked before key lookup; required claims; audience and issuer pinned | **2** |
| R2 | Privilege escalation through the user endpoints | 3 | 5 | **15** | Two-layer authorisation; strictly-junior target and grant rules; no self-administration; 11 tests | **3** |
| R3 | M2M client secret disclosed | 2 | 5 | **10** | Environment only, never in a tracked file; gitleaks in CI; nine scopes, no more; container ships no `.env` | **3** |
| R4 | Stolen access token replayed | 3 | 3 | **9** | PKCE rather than implicit; URL scrubbed after redirect; 24-hour expiry; `sessionStorage`; per-token rate limiting | **5** |
| R5 | Stored CSS injection through a recipe colour | 2 | 4 | **8** | Colours constrained at the model boundary; unknown keys dropped; CSP on every response | **2** |
| R6 | Menu defaced by a compromised manager account | 2 | 3 | **6** | Audit trail with actor and permission; MFA; an administrator can block an account immediately | **3** |
| R7 | Denial of service | 3 | 2 | **6** | Rate limits, body size cap, recipe bounds, JWKS refresh floor, cached keys | **3** |
| R8 | Data loss from ephemeral storage | 3 | 3 | **9** | Persistent path documented and configured; idempotent seeding; `DB_DROP_AND_CREATE_ALL` refused in production | **3** |
| R9 | Revoked role still effective | 4 | 2 | **8** | 24-hour ceiling; blocking in Auth0 stops new tokens; the UI detects divergence and says so | **5** |
| R10 | Dependency vulnerability | 3 | 3 | **9** | Pinned trees; `pip-audit` and `npm audit` in CI; python-jose replaced | **3** |
| R11 | Secret committed | 2 | 5 | **10** | `.gitignore`; gitleaks; a CI job that fails on a committed JWT or tracked `.env` | **2** |
| R12 | Information disclosure through errors | 3 | 2 | **6** | One opaque 500 handler; allow-listed user projection; 404 rather than 403 on senior accounts; upstream bodies never forwarded | **2** |

R4 and R9 are the two that stay at 5. Both are inherent to stateless bearer
tokens, both are named in [THREAT_MODEL.md](THREAT_MODEL.md#deliberate-residual-risks),
and fixing either means an architecture this project does not have.

---

## Control mapping

### OWASP Top 10 (2021)

| Category | How it is addressed |
|---|---|
| **A01 Broken Access Control** | The centre of the design. Two-layer authorisation, an executed RBAC matrix, 401/403 kept distinct, senior accounts answering 404 to juniors, server-side filtering rather than UI hiding |
| **A02 Cryptographic Failures** | RS256 only; symmetric algorithms refused at start-up; HTTPS enforced; HSTS over TLS; no credential stored |
| **A03 Injection** | Parameterised queries throughout; `sort` an allow-list of column objects; LIKE wildcards escaped; colours constrained; unknown keys dropped |
| **A04 Insecure Design** | Threat model written before hardening; least privilege on the M2M scopes; no-role as the default for a new arrival; fail-closed configuration |
| **A05 Security Misconfiguration** | `Config.validate` refuses to start on a bad configuration; production refuses a wildcard CORS origin, a weak `SECRET_KEY` and a destructive boot flag; security headers everywhere; container runs non-root |
| **A06 Vulnerable Components** | Pinned trees, `pip-audit` and `npm audit` in CI, python-jose replaced on maintenance grounds |
| **A07 Authentication Failures** | Auth0 handles authentication; MFA available; brute-force protection is Auth0's; expiry enforced with bounded skew; `state` verified on every redirect |
| **A08 Integrity Failures** | Lockfiles committed; CI verifies the Postman collection still matches its generator; deployment package checked for stray secrets |
| **A09 Logging Failures** | Structured JSON logs with correlation ids; an append-only audit trail; secrets scrubbed before storage; authorisation decisions logged |
| **A10 SSRF** | The only outbound calls are to the configured Auth0 domain, built from configuration rather than from any request value |

### NIST CSF 2.0

| Function | Evidence |
|---|---|
| **Govern** | This document; the risk register; documented residual risks |
| **Identify** | Data inventory; asset list and trust boundaries in the threat model |
| **Protect** | RBAC; MFA; least-privilege scopes; input validation; security headers; secret management |
| **Detect** | Audit trail; structured logs; health probes; CI security scanning |
| **Respond** | Correlation ids for tracing; block-user for immediate containment; the audit trail answers "what did they touch" |
| **Recover** | Idempotent seeding; persistent storage documented; infrastructure as code, so a rebuild is a command |

### CIS Controls v8 (the ones that apply)

| Control | Evidence |
|---|---|
| 3 — Data protection | Classification above; no credential stored; minimal personal data |
| 4 — Secure configuration | Fail-fast validation; production-specific refusals; non-root container |
| 5 — Account management | Auth0-backed; invitation flow; block and delete |
| 6 — Access control management | Two-layer RBAC; least privilege; no self-administration |
| 8 — Audit log management | Append-only trail, dual-written to the log |
| 16 — Application security | Threat model; 363 tests; bandit; dependency scanning; CI gating |

---

## Privacy

### GDPR-shaped questions, answered

| Question | Answer |
|---|---|
| **Lawful basis** | Legitimate interest — staff accounts for a staff system |
| **Data minimisation** | Only the `sub` claim is persisted here. Names and emails stay in Auth0 |
| **Purpose limitation** | Audit rows exist to answer "who did this". Nothing else reads them |
| **Storage limitation** | ⚠️ **Gap.** No automatic expiry on audit rows. See below |
| **Right of access** | `GET /users/me` returns everything this system holds about the caller |
| **Right to erasure** | `DELETE /users/<id>` removes the Auth0 account. Audit rows retain the `sub` |
| **Data portability** | `/users/me` and `/audit` both return JSON |
| **Security** | The whole of [THREAT_MODEL.md](THREAT_MODEL.md) |

### On erasure and the audit trail

Deleting a user removes them from Auth0. Their `sub` remains in the audit
trail, by design: an audit record that can be erased by the person it
describes is not an audit record.

This is the usual tension, and the usual answer is that the legitimate
interest in an accurate security record outweighs erasure of a pseudonymous
identifier — particularly one that no longer resolves to a person once the
account is gone. A production system should say so in its privacy notice
rather than leaving it implicit.

---

## Gaps

What a real deployment would still need. Naming them is the point.

| Gap | Impact | What it would take |
|---|---|---|
| **No audit retention policy** | Rows accumulate forever | A scheduled job deleting rows past a stated retention period, and the period agreed first |
| **No alerting** | Detection without notification | Application Insights alerts on bursts of 403s or on `user.deleted` |
| **No formal secret rotation** | A leaked M2M secret stays valid until noticed | Key Vault with rotation; the code already re-reads its configuration at start-up |
| **No backups** | SQLite on a single volume | Scheduled blob snapshots, or Postgres with point-in-time restore |
| **No penetration test** | Controls verified only by the tests that were thought of | An independent test is the only thing that finds what the author did not consider |
| **No DPIA** | Not required at this scale | Would be, for anything handling payments or health data |
| **In-memory rate limiting** | Per-process budgets | `RATELIMIT_STORAGE_URI` set to Redis. Configuration only |
| **No WAF** | No layer-7 filtering in front | Azure Front Door or Cloudflare |

---

## How to check any of this

| Claim | Command |
|---|---|
| No secrets in the repository | `git ls-files \| xargs grep -lE "AUTH0_M2M_CLIENT_SECRET=."` — should be empty |
| Access control behaves as documented | `cd backend && pytest -m rbac -v` |
| The attacks are still refused | `cd backend && pytest -m security -v` |
| No known source-level issues | `cd backend && bandit -r src -c pyproject.toml` |
| No known dependency advisories | `cd backend && pip-audit` |
| The live matrix matches the docs | `curl localhost:5000/health/rbac \| python -m json.tool` |
| Errors disclose nothing | `cd backend && pytest tests/test_errors.py -v` |

Every claim in this document is either a code path you can read or a test you
can run. That is the only kind of compliance document worth writing.
