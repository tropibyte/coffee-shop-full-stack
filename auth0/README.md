# Auth0 tenant as code

An alternative to clicking through [docs/AUTH0_SETUP.md](../docs/AUTH0_SETUP.md).
`terraform apply` creates the API, its ten permissions, the three roles with
their grants, both applications, the Action, and the connections.

**It does not replace the whole guide.** Four things stay manual, and should:

| Still by hand | Why |
|---|---|
| Creating the Auth0 account | It is an account. You type the password. |
| The bootstrap M2M application | Terraform needs a credential before it can create credentials. |
| The three test users | Creating a user means setting a password. Terraform would have to hold it, and the state file would then contain it. |
| Enabling MFA | Tenant-wide security policy, three clicks, and worth doing deliberately. |

---

## Once: the bootstrap application

Terraform authenticates to Auth0 as a machine-to-machine application. Create
it in the dashboard:

1. **Applications → Applications → + Create Application**
2. Name: `Terraform`, type: **Machine to Machine Applications**
3. Authorise it for the **Auth0 Management API**
4. Grant these scopes:

   ```
   read:clients          create:clients          update:clients          delete:clients
   read:client_keys      create:client_keys
   read:resource_servers create:resource_servers update:resource_servers delete:resource_servers
   read:roles            create:roles            update:roles            delete:roles
   read:connections      create:connections      update:connections      delete:connections
   read:actions          create:actions          update:actions          delete:actions
   read:client_grants    create:client_grants    update:client_grants    delete:client_grants
   ```

5. Copy the Client ID and Client Secret.

This application can create and destroy anything in the tenant. Treat its
secret accordingly, and delete the application when you are finished with it.

---

## Apply

```bash
cd auth0
cp terraform.tfvars.example terraform.tfvars
```

Edit `terraform.tfvars` with your domain and the bootstrap client id. Then put
the secret in the environment rather than in the file:

```bash
# macOS / Linux — the leading space keeps it out of shell history in most shells
 export TF_VAR_terraform_client_secret='...'

# PowerShell
$env:TF_VAR_terraform_client_secret = '...'
```

```bash
terraform init
terraform plan          # read this before applying
terraform apply
```

Then collect the configuration:

```bash
terraform output backend_env
terraform output frontend_environment_ts
terraform output -raw management_client_secret
```

Paste the first into `backend/.env`, the second into
`frontend/src/environments/environment.ts`, and the third into `.env` as
`AUTH0_M2M_CLIENT_SECRET`.

---

## Then, by hand

```
terraform output next_steps
```

1. Create `barista@coffeeshop.test`, `manager@coffeeshop.test` and
   `admin@coffeeshop.test` under **User Management → Users**.
2. Assign each the matching role on its **Roles** tab.
3. Enable MFA under **Security → Multi-factor Auth**.

---

## Handling the state file

`terraform.tfstate` contains the machine-to-machine client secret in
plaintext. It is gitignored, and that is the minimum rather than the answer.

For anything beyond a personal demo, use a remote backend with encryption at
rest:

```hcl
terraform {
  backend "azurerm" {
    resource_group_name  = "coffee-shop-rg"
    storage_account_name = "coffeeshoptfstate"
    container_name       = "tfstate"
    key                  = "auth0.tfstate"
  }
}
```

Azure Storage encrypts at rest by default and supports state locking through
blob leases.

---

## Changing things

| To change | Edit |
|---|---|
| A permission | `local.permissions` in `main.tf` |
| What a role grants | `local.*_permissions` in `main.tf` |
| Allowed origins | `allowed_origins` in `terraform.tfvars` |
| Token lifetime | `token_lifetime_seconds` |
| The Action's code | The `code` block on `auth0_action.add_roles` |

`local.*_permissions` in `main.tf` and `ROLE_RANKS` in
`backend/src/management/users_api.py` describe the same ladder from two ends.
If one moves and the other does not, the tenant and the code disagree about
who outranks whom — and the code is what enforces it. Change both.

---

## Tearing it down

```bash
terraform destroy
```

Removes everything Terraform created. It does **not** remove users, MFA
enrolments, or the bootstrap application, because it never created them.

---

## A caveat worth stating

This configuration is written against **provider `auth0/auth0 ~> 1.7`** and is
validated by `terraform validate` and `terraform plan`. It has not been
applied against a live tenant as part of this project, because doing so would
have meant creating an Auth0 account — which is exactly the step that stays
with you.

The Auth0 provider has reorganised resources between major versions before
(`auth0_resource_server` scopes moving to `auth0_resource_server_scopes`, and
client secrets moving to `auth0_client_credentials`, are both recent). If
`terraform plan` complains about an unknown attribute, check the provider's
[upgrade guide](https://registry.terraform.io/providers/auth0/auth0/latest/docs)
— the shape will have moved, not the intent.

If you would rather not debug a provider version, the click-by-click route in
[docs/AUTH0_SETUP.md](../docs/AUTH0_SETUP.md) takes about 25 minutes and has
no such dependency.
