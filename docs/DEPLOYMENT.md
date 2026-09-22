# Deployment

Four routes, in the order they are worth trying.

| Route | Cost | Database survives restarts | Effort |
|---|---|:---:|---|
| **[Azure App Service + Static Web Apps](#azure)** | **$0** on F1 + Free | **Yes** | Bicep + one workflow |
| [Docker Compose](#docker) | $0 locally | Yes (volume) | One command |
| [Render](#render) | $0 | **No** on the free tier | Simplest of all |
| [Fly.io](#flyio) | ~$2–4/mo | Yes (volume) | Small |

Prices move. Re-check whichever you pick before you commit to it.

---

## Azure

The default, and the one the repository is set up for. On F1 App Service and
Free Static Web Apps it costs nothing, and — unlike most free tiers —
App Service persists `/home`, so the SQLite database survives a restart.

**What you get:** the API on `https://coffee-shop-api-xxxxxx.azurewebsites.net`,
the frontend on `https://coffee-shop-web-xxxxxx.azurestaticapps.net`, HTTPS on
both, Application Insights, and 30 days of log retention.

**What F1 costs you:** 60 CPU-minutes a day and no always-on, so the first
request after an idle period takes perhaps 20 seconds. For a reviewer clicking
through a demo that is plenty. `B1` (~$13/month) removes both limits and is a
one-word change in `main.parameters.json`.

### 1. Provision

```bash
az login
az group create --name coffee-shop-rg --location eastus
```

Edit `infra/main.parameters.json` with your Auth0 domain and client ids. Then:

```bash
SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(48))")

az deployment group create \
  --resource-group coffee-shop-rg \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters secretKey="$SECRET_KEY" \
  --parameters auth0M2mClientSecret="$AUTH0_M2M_CLIENT_SECRET"
```

Secrets go on the command line, never into the parameters file. For anything
long-lived, put them in Key Vault and reference them
(`@Microsoft.KeyVault(SecretUri=...)`) instead.

Collect the outputs:

```bash
az deployment group show \
  --resource-group coffee-shop-rg \
  --name main \
  --query properties.outputs
```

You want `apiUrl`, `frontendUrl`, `apiAppName` and `staticWebAppName`.

### 2. Tell Auth0 about the new origin

In the Auth0 dashboard, open **Coffee Shop Web → Settings** and append the
`frontendUrl` to all three lists:

- Allowed Callback URLs
- Allowed Logout URLs
- Allowed Web Origins

Skip this and sign-in fails with `Callback URL mismatch`. It is the single
most common deployment mistake with Auth0.

### 3. Set up the deploy workflow

The workflow authenticates with **OIDC federated credentials**, so there is no
long-lived Azure secret in the repository.

```bash
# An app registration for GitHub to act as
az ad app create --display-name coffee-shop-deploy
APP_ID=$(az ad app list --display-name coffee-shop-deploy --query '[0].appId' -o tsv)
az ad sp create --id "$APP_ID"

# Let it deploy into the resource group, and nothing else
SUB=$(az account show --query id -o tsv)
az role assignment create \
  --assignee "$APP_ID" \
  --role Contributor \
  --scope "/subscriptions/$SUB/resourceGroups/coffee-shop-rg"

# Trust this repository's production environment, and only that
az ad app federated-credential create --id "$APP_ID" --parameters '{
  "name": "github-main",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "repo:YOUR-GITHUB-USER/coffee-shop-full-stack:environment:production",
  "audiences": ["api://AzureADTokenExchange"]
}'
```

Then, in **GitHub → Settings → Secrets and variables → Actions**:

| Secrets | From |
|---|---|
| `AZURE_CLIENT_ID` | `$APP_ID` |
| `AZURE_TENANT_ID` | `az account show --query tenantId -o tsv` |
| `AZURE_SUBSCRIPTION_ID` | `az account show --query id -o tsv` |
| `AZURE_STATIC_WEB_APPS_API_TOKEN` | `az staticwebapp secrets list --name <staticWebAppName> --query properties.apiKey -o tsv` |

| Variables | From |
|---|---|
| `AZURE_RESOURCE_GROUP` | `coffee-shop-rg` |
| `AZURE_API_APP_NAME` | the `apiAppName` output |
| `API_URL` | the `apiUrl` output |
| `FRONTEND_URL` | the `frontendUrl` output |
| `AUTH0_DOMAIN_PREFIX` | your domain **without** `.auth0.com` |
| `AUTH0_AUDIENCE` | `coffee-shop` |
| `AUTH0_CLIENT_ID` | the SPA client id |

The variables are what the workflow compiles into `environment.prod.ts` — an
Angular bundle has no runtime configuration, so those URLs are baked in at
build time. The workflow refuses to build if any of them is empty, because a
blank client id produces an app that looks fine and cannot sign anybody in.

### 4. Deploy

Push to `main`, or run the workflow by hand. It will:

1. run the backend test suite, and stop if anything fails;
2. package the API, refusing to proceed if `.env` or a local database somehow
   made it into the archive;
3. deploy and wait for `/health/ready` to answer 200;
4. smoke-test that the menu is public and that recipes and writes are not;
5. build the frontend with the deployment's configuration and upload it.

### Operating it

```bash
# Live log tail
az webapp log tail --name <apiAppName> --resource-group coffee-shop-rg

# Restart
az webapp restart --name <apiAppName> --resource-group coffee-shop-rg

# Change a setting without redeploying
az webapp config appsettings set \
  --name <apiAppName> --resource-group coffee-shop-rg \
  --settings LOG_LEVEL=DEBUG
```

Logs are newline-delimited JSON, so this finds every failed authorisation:

```kusto
AppServiceConsoleLogs
| where ResultDescription has "audit"
| extend log = parse_json(ResultDescription)
| where log.status >= 400
| project TimeGenerated, log.actor, log.action, log.status, log.request_id
```

### The database

SQLite at `/home/data/database.db`. `/home` is backed by Azure Files, which
means it survives restarts, deployments and scale operations — and also means
it is shared between instances, which SQLite does not like. **Do not scale the
API past one instance without moving to Postgres first:**

```bash
az postgres flexible-server create \
  --resource-group coffee-shop-rg --name coffee-shop-db \
  --tier Burstable --sku-name Standard_B1ms

az webapp config appsettings set \
  --name <apiAppName> --resource-group coffee-shop-rg \
  --settings DATABASE_URL="postgresql+psycopg://user:pass@coffee-shop-db.postgres.database.azure.com/coffee"
```

Add `psycopg[binary]` to `requirements.txt` and redeploy. No code changes —
`DATABASE_URL` accepts any SQLAlchemy URL.

---

## Docker

The closest thing to the Azure setup you can run on a laptop: gunicorn, a
non-root user, production configuration, Redis-backed rate limiting, and a
volume at the same `/home/data` path.

```bash
cd infra
cp .env.example .env         # fill in Auth0 and a SECRET_KEY
docker compose up --build
```

- Frontend: <http://localhost:8100>
- API: <http://localhost:5000>
- Docs: <http://localhost:5000/docs>

```bash
docker compose logs -f api
docker compose down          # keeps the volume
docker compose down -v       # deletes the menu too
```

Worth doing once before deploying: the compose stack catches a
production-configuration problem — a `SECRET_KEY` that is too short, a
wildcard CORS origin — on your machine rather than in Azure.

---

## Render

The fastest route to a public URL, with one caveat.

`infra/render.yaml` describes both services. Connect the repository at
<https://dashboard.render.com>, choose **Blueprint**, and set `AUTH0_DOMAIN`,
`AUTH0_API_AUDIENCE` and `SECRET_KEY` in the dashboard.

**The caveat:** a free Render web service has an ephemeral filesystem and
spins down after 15 minutes of inactivity. The SQLite database resets on every
restart. `DB_SEED_IF_EMPTY` means the menu comes back rather than being empty,
but anything a reviewer added will be gone. Either attach a paid persistent
disk, or point `DATABASE_URL` at Render's free Postgres — which itself expires
after 30 days.

Good for a quick demo. Not good for one someone will come back to.

---

## Fly.io

`infra/fly.toml` is included. No genuinely free tier since late 2024; expect
roughly $2–4 a month for a 256 MB instance with a 1 GB volume.

```bash
fly launch --config infra/fly.toml --no-deploy
fly secrets set AUTH0_DOMAIN=... AUTH0_API_AUDIENCE=... SECRET_KEY=...
fly volumes create coffee_data --size 1
fly deploy
```

Worth it if you want the app close to users in several regions. Otherwise
Azure F1 is free and does the same job.

---

## Checklist, whichever route

- [ ] `SECRET_KEY` is 32+ random characters and not the one in any example
- [ ] `FLASK_CONFIG=production` — which refuses a wildcard CORS origin and a
      destructive boot flag
- [ ] `CORS_ORIGINS` names the deployed frontend, not `*`
- [ ] `DATABASE_URL` points at persistent storage
- [ ] `DB_DROP_AND_CREATE_ALL` is false or absent
- [ ] The deployed frontend origin is in all three Auth0 URL lists
- [ ] `environment.prod.ts` has the deployed API URL and callback
- [ ] HTTPS enforced
- [ ] `/health/ready` answers 200
- [ ] `GET /drinks` is public; `/drinks-detail` and `POST /drinks` are 401
- [ ] Sign-in works end to end with a real account

The deploy workflow checks most of these for you and fails the job rather than
leaving a half-working deployment.

---

## If it goes wrong

| Symptom | Cause |
|---|---|
| App Service returns 503 | Cold start on F1, or the app failed to boot. `az webapp log tail` will say which |
| `ConfigurationError` in the log | A required app setting is missing. The message names it |
| `Callback URL mismatch` | The deployed origin is not in Auth0's Allowed Callback URLs |
| Sign-in works; every API call is 401 | The frontend and the API are pointed at different tenants or audiences. `/diagnostics` says so explicitly |
| CORS error | `CORS_ORIGINS` does not include the deployed frontend origin |
| The menu resets | Ephemeral storage. `DATABASE_URL` must point at a persistent path |
| Deploy succeeds, site shows the old build | `index.html` was cached. The nginx config sets `no-store` on it; Static Web Apps handles this itself |
