// ---------------------------------------------------------------------------
// Coffee Shop — Azure infrastructure.
//
// Deploys the API to Linux App Service and the Ionic app to Static Web Apps.
// On the F1 and Free tiers respectively, this costs nothing.
//
//   az group create --name coffee-shop-rg --location eastus
//   az deployment group create \
//     --resource-group coffee-shop-rg \
//     --template-file infra/main.bicep \
//     --parameters infra/main.parameters.json \
//     --parameters auth0M2mClientSecret=$SECRET secretKey=$KEY
//
// Secrets are passed on the command line or from Key Vault, never written
// into the parameters file.
// ---------------------------------------------------------------------------

targetScope = 'resourceGroup'

// --- Naming ----------------------------------------------------------------

@description('Short name used as the prefix for every resource.')
@minLength(3)
@maxLength(18)
param appName string = 'coffee-shop'

@description('Location for the App Service plan.')
param location string = resourceGroup().location

@description('Static Web Apps is not available in every region; this is a separate parameter for that reason.')
@allowed([
  'eastus2'
  'centralus'
  'westus2'
  'westeurope'
  'eastasia'
])
param staticWebAppLocation string = 'eastus2'

@description('Suffix that keeps globally-unique names unique.')
param uniqueSuffix string = substring(uniqueString(resourceGroup().id), 0, 6)

// --- Plan ------------------------------------------------------------------

@description('F1 is free and sufficient for a demo: 60 CPU-minutes a day, no always-on. B1 removes both limits for roughly 13 USD a month.')
@allowed([
  'F1'
  'B1'
  'P0v3'
])
param appServicePlanSku string = 'F1'

// --- Auth0 -----------------------------------------------------------------

@description('Auth0 tenant domain, e.g. coffee-shop.us.auth0.com')
param auth0Domain string

@description('Auth0 API identifier, e.g. coffee-shop')
param auth0Audience string = 'coffee-shop'

@description('Auth0 SPA client id. Public, not a secret.')
param auth0ClientId string = ''

@description('Register the user-administration endpoints.')
param managementApiEnabled bool = true

@description('Auth0 machine-to-machine client id.')
param auth0M2mClientId string = ''

@description('Auth0 machine-to-machine client secret.')
@secure()
param auth0M2mClientSecret string = ''

// --- Application -----------------------------------------------------------

@description('Flask session key. At least 32 characters; production start-up refuses anything shorter.')
@secure()
param secretKey string

@description('Extra CORS origins beyond the Static Web App, comma-separated.')
param additionalCorsOrigins string = ''

// ---------------------------------------------------------------------------
// Resources
// ---------------------------------------------------------------------------

var apiName = '${appName}-api-${uniqueSuffix}'
var webName = '${appName}-web-${uniqueSuffix}'
var planName = '${appName}-plan-${uniqueSuffix}'
var logName = '${appName}-logs-${uniqueSuffix}'
var insightsName = '${appName}-insights-${uniqueSuffix}'

// F1 does not support always-on; asking for it on F1 fails the deployment.
var alwaysOn = appServicePlanSku != 'F1'

resource plan 'Microsoft.Web/serverfarms@2023-12-01' = {
  name: planName
  location: location
  sku: {
    name: appServicePlanSku
    // F1 is its own tier; everything else here is Basic or Premium v3.
    tier: appServicePlanSku == 'F1' ? 'Free' : (appServicePlanSku == 'B1' ? 'Basic' : 'PremiumV3')
  }
  kind: 'linux'
  properties: {
    reserved: true // 'reserved' is how Bicep spells "Linux"
  }
}

resource staticWeb 'Microsoft.Web/staticSites@2023-12-01' = {
  name: webName
  location: staticWebAppLocation
  sku: {
    name: 'Free'
    tier: 'Free'
  }
  properties: {
    // The GitHub Actions workflow deploys the build output; Azure does not
    // need repository access of its own.
    allowConfigFileUpdates: true
    stagingEnvironmentPolicy: 'Enabled'
  }
}

var frontendOrigin = 'https://${staticWeb.properties.defaultHostname}'
var corsOrigins = empty(additionalCorsOrigins)
  ? frontendOrigin
  : '${frontendOrigin},${additionalCorsOrigins}'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: insightsName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
  }
}

resource api 'Microsoft.Web/sites@2023-12-01' = {
  name: apiName
  location: location
  // A system-assigned identity so the app can reach Key Vault or storage
  // later without a connection string in configuration.
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.12'
      alwaysOn: alwaysOn
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      healthCheckPath: '/health/ready'

      // Oryx installs requirements.txt during deployment; gunicorn then
      // serves the app from wsgi.py.
      appCommandLine: 'gunicorn --bind=0.0.0.0:8000 --workers 2 --threads 2 --timeout 120 --access-logfile - --error-logfile - wsgi:app'

      appSettings: [
        {
          name: 'SCM_DO_BUILD_DURING_DEPLOYMENT'
          value: 'true'
        }
        {
          name: 'ENABLE_ORYX_BUILD'
          value: 'true'
        }
        {
          name: 'WEBSITES_PORT'
          value: '8000'
        }
        {
          name: 'FLASK_CONFIG'
          value: 'production'
        }
        {
          name: 'AUTH0_DOMAIN'
          value: auth0Domain
        }
        {
          name: 'AUTH0_API_AUDIENCE'
          value: auth0Audience
        }
        {
          name: 'AUTH0_CLIENT_ID'
          value: auth0ClientId
        }
        {
          name: 'MANAGEMENT_API_ENABLED'
          value: string(managementApiEnabled)
        }
        {
          name: 'AUTH0_M2M_CLIENT_ID'
          value: auth0M2mClientId
        }
        {
          name: 'AUTH0_M2M_CLIENT_SECRET'
          value: auth0M2mClientSecret
        }
        {
          name: 'SECRET_KEY'
          value: secretKey
        }
        {
          // Only /home survives a restart or a scale operation on App
          // Service. Anywhere else and the menu resets without warning.
          name: 'DATABASE_URL'
          value: 'sqlite:////home/data/database.db'
        }
        {
          name: 'DB_SEED_IF_EMPTY'
          value: 'true'
        }
        {
          name: 'CORS_ORIGINS'
          value: corsOrigins
        }
        {
          name: 'FRONTEND_URL'
          value: frontendOrigin
        }
        {
          name: 'LOG_FORMAT'
          value: 'json'
        }
        {
          name: 'LOG_LEVEL'
          value: 'INFO'
        }
        {
          name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
          value: insights.properties.ConnectionString
        }
      ]
    }
  }
}

// The API's own JSON log lines, so a request id from a browser can be traced
// to the server side.
resource apiLogs 'Microsoft.Web/sites/config@2023-12-01' = {
  parent: api
  name: 'logs'
  properties: {
    applicationLogs: {
      fileSystem: {
        level: 'Information'
      }
    }
    httpLogs: {
      fileSystem: {
        retentionInMb: 35
        retentionInDays: 7
        enabled: true
      }
    }
    detailedErrorMessages: {
      enabled: false // never return a stack trace to a caller
    }
    failedRequestsTracing: {
      enabled: true
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

@description('The API base URL. Put this in frontend/src/environments/environment.prod.ts.')
output apiUrl string = 'https://${api.properties.defaultHostName}'

@description('The frontend URL. Add it to the Auth0 application Callback, Logout and Web Origin lists.')
output frontendUrl string = frontendOrigin

@description('Name of the API app, for `az webapp` commands.')
output apiAppName string = api.name

@description('Name of the static site, for the deployment workflow.')
output staticWebAppName string = staticWeb.name

@description('Everything that still has to be done by hand once this deploys.')
output nextSteps array = [
  'Add ${frontendOrigin} to the Auth0 application Allowed Callback URLs.'
  'Add ${frontendOrigin} to Allowed Logout URLs.'
  'Add ${frontendOrigin} to Allowed Web Origins.'
  'Set apiServerUrl in environment.prod.ts to https://${api.properties.defaultHostName}.'
  'Set callbackURL in environment.prod.ts to ${frontendOrigin}.'
  'Rebuild and redeploy the frontend so those values are compiled in.'
]
