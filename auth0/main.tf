###############################################################################
# Coffee Shop — Auth0 tenant as code.
#
# Creates the API, its ten permissions, the three roles with their grants, the
# single-page application, the machine-to-machine application, and the Action
# that puts role names into the access token.
#
# What this cannot do: create your Auth0 account, or the bootstrap
# machine-to-machine application Terraform itself authenticates with. Those
# are credentials, and they stay on your keyboard. See README.md here for the
# four clicks that come first.
#
# Everything in docs/AUTH0_SETUP.md sections 1, 2, 3, 5 and 8 is replaced by
# `terraform apply`.
###############################################################################

terraform {
  required_version = ">= 1.6"

  required_providers {
    auth0 = {
      source  = "auth0/auth0"
      version = "~> 1.7"
    }
  }
}

provider "auth0" {
  domain        = var.auth0_domain
  client_id     = var.terraform_client_id
  client_secret = var.terraform_client_secret
}

###############################################################################
# The API
#
# Its identifier becomes the `aud` claim. RBAC and token-permissions are both
# switched on here: with RBAC on and permissions off, every request gets a 403
# because the API can identify the caller but has nothing to authorise them
# with.
###############################################################################

resource "auth0_resource_server" "coffee_shop" {
  name             = "Coffee Shop"
  identifier       = var.api_audience
  signing_alg      = "RS256"
  token_lifetime   = var.token_lifetime_seconds
  skip_consent_for_verifiable_first_party_clients = true

  allow_offline_access = false
}

resource "auth0_resource_server_scopes" "coffee_shop" {
  resource_server_identifier = auth0_resource_server.coffee_shop.identifier

  dynamic "scopes" {
    for_each = local.permissions
    content {
      name        = scopes.key
      description = scopes.value
    }
  }
}

resource "auth0_resource_server_settings" "coffee_shop" {
  resource_server_identifier = auth0_resource_server.coffee_shop.identifier

  enforce_policies = true # "Enable RBAC"
  token_dialect    = "access_token_authz" # "Add Permissions in the Access Token"

  depends_on = [auth0_resource_server_scopes.coffee_shop]
}

locals {
  permissions = {
    "get:drinks-detail" = "Read full drink recipes"
    "post:drinks"       = "Add a drink to the menu"
    "patch:drinks"      = "Edit an existing drink"
    "delete:drinks"     = "Remove a drink"
    "get:users"         = "List accounts junior to yours"
    "post:users"        = "Invite a new team member"
    "patch:users"       = "Change a junior account"
    "delete:users"      = "Delete a junior account"
    "get:roles"         = "See which roles you may grant"
    "get:audit"         = "Read the audit trail"
  }

  # The role ladder. Mirrors ROLE_RANKS in
  # backend/src/management/users_api.py — if one moves and the other does not,
  # the tenant and the code disagree about who outranks whom.
  barista_permissions = [
    "get:drinks-detail",
  ]

  manager_permissions = concat(local.barista_permissions, [
    "post:drinks",
    "patch:drinks",
    "delete:drinks",
    "get:users",
    "post:users",
    "patch:users",
    "delete:users",
    "get:roles",
  ])

  administrator_permissions = concat(local.manager_permissions, [
    "get:audit",
  ])
}

###############################################################################
# Roles
###############################################################################

resource "auth0_role" "barista" {
  name        = "Barista"
  description = "Can see the menu and full recipes."
}

resource "auth0_role" "manager" {
  name        = "Manager"
  description = "Full menu control; can administer baristas."
}

resource "auth0_role" "administrator" {
  name        = "Administrator"
  description = "Everything a manager can do, plus managing managers and reading the audit trail."
}

resource "auth0_role_permissions" "barista" {
  role_id = auth0_role.barista.id

  dynamic "permissions" {
    for_each = local.barista_permissions
    content {
      name                       = permissions.value
      resource_server_identifier = auth0_resource_server.coffee_shop.identifier
    }
  }

  depends_on = [auth0_resource_server_scopes.coffee_shop]
}

resource "auth0_role_permissions" "manager" {
  role_id = auth0_role.manager.id

  dynamic "permissions" {
    for_each = local.manager_permissions
    content {
      name                       = permissions.value
      resource_server_identifier = auth0_resource_server.coffee_shop.identifier
    }
  }

  depends_on = [auth0_resource_server_scopes.coffee_shop]
}

resource "auth0_role_permissions" "administrator" {
  role_id = auth0_role.administrator.id

  dynamic "permissions" {
    for_each = local.administrator_permissions
    content {
      name                       = permissions.value
      resource_server_identifier = auth0_resource_server.coffee_shop.identifier
    }
  }

  depends_on = [auth0_resource_server_scopes.coffee_shop]
}

###############################################################################
# The single-page application
#
# `token_endpoint_auth_method = "none"` is what makes this a public client:
# a browser cannot keep a secret, so it uses PKCE instead of holding one.
###############################################################################

resource "auth0_client" "spa" {
  name                = "Coffee Shop Web"
  description         = "Ionic frontend for the Coffee Shop menu."
  app_type            = "spa"
  oidc_conformant     = true
  is_first_party      = true

  grant_types = [
    "authorization_code",
    "refresh_token",
    "implicit", # only used when useAuthorizationCodePkce is false
  ]

  callbacks           = var.allowed_origins
  allowed_logout_urls = var.allowed_origins
  web_origins         = var.allowed_origins
  allowed_origins     = var.allowed_origins

  jwt_configuration {
    alg = "RS256"
  }

  refresh_token {
    rotation_type   = "rotating"
    expiration_type = "expiring"
    token_lifetime  = 2592000 # 30 days
  }
}

resource "auth0_client_credentials" "spa" {
  client_id                = auth0_client.spa.id
  authentication_method    = "none" # public client: no secret to steal
}

###############################################################################
# The machine-to-machine application
#
# This is what the API uses to administer users. Its grants are exactly the
# nine Management scopes the user endpoints call — anything more would be
# standing authority nobody asked for.
###############################################################################

resource "auth0_client" "management" {
  name            = "Coffee Shop Management"
  description     = "Lets the Coffee Shop API administer users through the Auth0 Management API."
  app_type        = "non_interactive"
  oidc_conformant = true
  is_first_party  = true

  grant_types = ["client_credentials"]

  jwt_configuration {
    alg = "RS256"
  }
}

resource "auth0_client_credentials" "management" {
  client_id             = auth0_client.management.id
  authentication_method = "client_secret_post"
}

data "auth0_resource_server" "management_api" {
  identifier = "https://${var.auth0_domain}/api/v2/"
}

resource "auth0_client_grant" "management" {
  client_id = auth0_client.management.id
  audience  = data.auth0_resource_server.management_api.identifier

  scopes = [
    "read:users",
    "update:users",
    "create:users",
    "delete:users",
    "read:roles",
    "read:role_members",
    "create:role_members",
    "delete:role_members",
    "create:user_tickets",
  ]
}

###############################################################################
# The Action that puts role names into the token
#
# Auth0 includes permissions but not role names. The API can work without
# this — it falls back to a Management API lookup — but that costs a round
# trip on every request that needs to know the caller's rank.
###############################################################################

resource "auth0_action" "add_roles" {
  name    = "Add roles to access token"
  runtime = "node22"
  deploy  = true

  supported_triggers {
    id      = "post-login"
    version = "v3"
  }

  code = <<-JAVASCRIPT
    /**
     * Adds the user's role names to the access token as a namespaced claim.
     *
     * Auth0 silently drops custom claims that are not namespaced with a URI,
     * so the prefix is mandatory rather than stylistic.
     */
    exports.onExecutePostLogin = async (event, api) => {
      const namespace = '${var.roles_claim_namespace}';
      const roles = event.authorization?.roles ?? [];

      api.accessToken.setCustomClaim(`$${namespace}/roles`, roles);
      api.idToken.setCustomClaim(`$${namespace}/roles`, roles);
    };
  JAVASCRIPT
}

resource "auth0_trigger_actions" "post_login" {
  trigger = "post-login"

  actions {
    id           = auth0_action.add_roles.id
    display_name = auth0_action.add_roles.name
  }
}

###############################################################################
# Google sign-in (optional)
#
# Leaving the client id and secret empty uses Auth0's shared development keys,
# which are rate-limited and not for production — fine for a graded demo.
###############################################################################

resource "auth0_connection" "google" {
  count = var.enable_google_login ? 1 : 0

  name     = "google-oauth2"
  strategy = "google-oauth2"

  options {
    client_id     = var.google_client_id
    client_secret = var.google_client_secret

    scopes = ["email", "profile"]
  }
}

resource "auth0_connection_clients" "google" {
  count = var.enable_google_login ? 1 : 0

  connection_id   = auth0_connection.google[0].id
  enabled_clients = [auth0_client.spa.id]
}

###############################################################################
# Username/password connection, wired to the SPA
###############################################################################

resource "auth0_connection" "database" {
  name     = var.database_connection_name
  strategy = "auth0"

  options {
    password_policy        = "good"
    brute_force_protection = true
    disable_signup         = true # staff are invited, not self-registered

    password_complexity_options {
      min_length = 12
    }
  }
}

resource "auth0_connection_clients" "database" {
  connection_id = auth0_connection.database.id

  enabled_clients = [
    auth0_client.spa.id,
    auth0_client.management.id,
  ]
}
