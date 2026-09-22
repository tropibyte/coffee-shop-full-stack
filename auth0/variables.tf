###############################################################################
# Inputs.
#
# The two credentials are marked sensitive so Terraform never prints them,
# and they are supplied through environment variables rather than a file --
# see README.md.
###############################################################################

variable "auth0_domain" {
  description = "Auth0 tenant domain, e.g. coffee-shop.us.auth0.com"
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9-]+\.[a-z0-9.-]*auth0\.com$", var.auth0_domain))
    error_message = "Use the full tenant domain, including the .auth0.com suffix."
  }
}

variable "terraform_client_id" {
  description = <<-TEXT
    Client id of the bootstrap machine-to-machine application Terraform
    authenticates with. Create it by hand once; see README.md.
  TEXT
  type        = string
}

variable "terraform_client_secret" {
  description = "Its client secret. Supply through TF_VAR_terraform_client_secret."
  type        = string
  sensitive   = true
}

variable "api_audience" {
  description = "The API identifier, which becomes the aud claim."
  type        = string
  default     = "coffee-shop"
}

variable "token_lifetime_seconds" {
  description = <<-TEXT
    How long an access token stays valid. 86400 (24 hours) gives a reviewer
    room to work through the Postman collection without the tokens lapsing
    mid-review. Shorten it for anything real.
  TEXT
  type        = number
  default     = 86400

  validation {
    condition     = var.token_lifetime_seconds >= 300 && var.token_lifetime_seconds <= 2592000
    error_message = "Pick something between 5 minutes and 30 days."
  }
}

variable "allowed_origins" {
  description = <<-TEXT
    Every origin the frontend is served from. Used for Allowed Callback URLs,
    Allowed Logout URLs and Allowed Web Origins. localhost and 127.0.0.1 are
    different origins to Auth0, so list both.
  TEXT
  type        = list(string)
  default = [
    "http://localhost:8100",
    "http://127.0.0.1:8100",
  ]
}

variable "roles_claim_namespace" {
  description = <<-TEXT
    Namespace for the custom roles claim. Must match ROLES_CLAIM in
    backend/src/management/users_api.py and in frontend/src/app/core/oauth.ts.
  TEXT
  type        = string
  default     = "https://coffee-shop.api"
}

variable "database_connection_name" {
  description = "Username/password connection name. Must match AUTH0_CONNECTION in the backend."
  type        = string
  default     = "Username-Password-Authentication"
}

variable "enable_google_login" {
  description = "Create and enable the Google social connection."
  type        = bool
  default     = true
}

variable "google_client_id" {
  description = "Google OAuth client id. Empty uses Auth0's shared development keys."
  type        = string
  default     = ""
}

variable "google_client_secret" {
  description = "Google OAuth client secret. Empty uses Auth0's shared development keys."
  type        = string
  default     = ""
  sensitive   = true
}
