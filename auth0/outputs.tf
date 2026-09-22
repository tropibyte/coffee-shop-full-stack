###############################################################################
# Outputs: the values that go into the two configuration files.
###############################################################################

output "backend_env" {
  description = "Paste into backend/.env"
  value       = <<-TEXT
    AUTH0_DOMAIN=${var.auth0_domain}
    AUTH0_API_AUDIENCE=${auth0_resource_server.coffee_shop.identifier}
    AUTH0_CLIENT_ID=${auth0_client.spa.client_id}
    MANAGEMENT_API_ENABLED=true
    AUTH0_M2M_CLIENT_ID=${auth0_client.management.client_id}
    AUTH0_M2M_CLIENT_SECRET=<run: terraform output -raw management_client_secret>
  TEXT
}

output "frontend_environment_ts" {
  description = "Paste into frontend/src/environments/environment.ts"
  value       = <<-TEXT
    export const environment = {
      production: false,
      apiServerUrl: 'http://127.0.0.1:5000',
      auth0: {
        url: '${replace(var.auth0_domain, ".auth0.com", "")}',
        audience: '${auth0_resource_server.coffee_shop.identifier}',
        clientId: '${auth0_client.spa.client_id}',
        callbackURL: '${var.allowed_origins[0]}',
        useAuthorizationCodePkce: true,
        tokenStorage: 'session' as 'session' | 'local',
      },
    };
  TEXT
}

output "spa_client_id" {
  description = "Single-page application client id. Public."
  value       = auth0_client.spa.client_id
}

output "management_client_id" {
  description = "Machine-to-machine client id."
  value       = auth0_client.management.client_id
}

output "management_client_secret" {
  description = "Machine-to-machine client secret. Read with: terraform output -raw management_client_secret"
  value       = auth0_client_credentials.management.client_secret
  sensitive   = true
}

output "role_ids" {
  description = "The three role ids."
  value = {
    barista       = auth0_role.barista.id
    manager       = auth0_role.manager.id
    administrator = auth0_role.administrator.id
  }
}

output "next_steps" {
  description = "What still has to be done by hand."
  value = [
    "Create the three test users (Auth0 will not accept a password from Terraform, and should not).",
    "Assign each of them a role: User Management > Users > (user) > Roles.",
    "Turn on multi-factor authentication: Security > Multi-factor Auth.",
    "Copy the two outputs above into backend/.env and frontend/src/environments/environment.ts.",
  ]
}
