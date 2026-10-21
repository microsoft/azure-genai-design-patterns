# Infrastructure: Multi-Tenant MCP Gateway (illustrative)

> **Illustrative only. Not production-hardened.** The Bicep template and the API Management policies in this folder show the *shape* of the deployment in [../docs/architecture.md](../docs/architecture.md). They have not been deployed end to end as published, and MCP server support in API Management uses a preview API version here. Check current documentation for every resource before you use any of it.

The template deploys one governed front door for MCP tools: Application Gateway WAF_v2 at the edge, API Management injected into a VNet in internal mode, an MCP server in API Management that runs the per-tenant policy pipeline, Key Vault and Event Hubs behind private endpoints, and Azure API Center as the design-time registry. It deliberately bakes in **no sizing, limit, timeout, or retention values**. Each of those is a required parameter that you set from your own load tests, SLOs, and data-retention policy.

## Contents

| File | Purpose |
|---|---|
| [main.bicep](main.bicep) | Resource-group-scoped skeleton: network, edge, API Management, MCP server, policies, observability, API Center. |
| [policies/global.xml](policies/global.xml) | Service-wide policy. Deletes client-supplied tenant hints, starts a W3C trace context when the client sent none, returns the request ID, and removes backend implementation headers. |
| [policies/mcp-tenant-pipeline.xml](policies/mcp-tenant-pipeline.xml) | MCP server-scope policy. The per-call pipeline: token, tenant, directory, entitlement, tool re-check, limits, credential, routing, audit. |
| [policies/fragment-tenant-directory.xml](policies/fragment-tenant-directory.xml) | Policy fragment. Resolves the tenant record from a tenant key taken from the validated token, with a cache in front of the tenant directory API. |
| [policies/fragment-mcp-audit.xml](policies/fragment-mcp-audit.xml) | Policy fragment. Emits the per-tenant metric and the audit event for every outcome, including denials. |

The policy XML files begin with an explanatory XML comment. `main.bicep` loads them with `loadTextContent`, so the comment is part of the deployed policy value. If your deployment pipeline or API Management version rejects content before the root `<policies>` or `<fragment>` element, strip the leading comment in the pipeline.

## Resource inventory

| Resource | Why it is here | Notes |
|---|---|---|
| Virtual network with three subnets | Dedicated subnets for Application Gateway and API Management, plus one for private endpoints. | Address prefixes are parameters. Use your landing zone's IP plan. |
| Network security groups | Required inbound rules for Application Gateway v2 and for API Management classic internal VNet injection. | Add outbound service-tag rules if you force-tunnel or restrict egress. Check current docs. |
| Private DNS zones (optional) | Name resolution for the Key Vault and Event Hubs private endpoints. | Set `createPrivateDnsZones` to `false` when a hub already provides them. |
| Log Analytics workspace | Gateway, WAF, and diagnostic logs. | Retention is not set here. Apply your own policy. |
| Application Insights (workspace-based) | Request telemetry, W3C correlation, and per-tenant custom metrics from `emit-metric`. | Enable custom metrics with dimensions, or tenant dimensions are dropped. |
| Event Hubs namespace and `mcp-audit` hub | Audit stream from the `log-to-eventhub` policy toward your SIEM. | Local (SAS) auth disabled; API Management sends with its managed identity. Partition count and retention are left to you. |
| Key Vault | TLS certificate for the edge listener, secret-backed named values, and any per-tenant backend secrets. | RBAC mode, soft delete, purge protection, public network access disabled. |
| User-assigned identity for Application Gateway | Reads the TLS certificate from Key Vault. | Granted Key Vault Secrets User on the vault. |
| API Management (Developer or Premium, internal VNet) | The MCP gateway. System-assigned identity for Key Vault, Event Hubs, the tenant directory, and backends. | Older TLS protocols and ciphers disabled. Custom domain left to you. |
| Named values | Every policy setting, so policies stay identical across environments. | See [Named values](#named-values). |
| Loggers and diagnostics | `appinsights` and `audit-eventhub` loggers; service-wide diagnostics with body logging off. | Body logging stays off because it buffers streams and captures tool arguments. |
| Backends `cell-<key>` | One registered backend per cell or stamp. Policies select by ID and never build URLs from tenant data. | Add circuit-breaker rules from your own SLOs. |
| Policy fragments and policies | The pipeline in [../docs/architecture.md#6-per-call-policy-pipeline](../docs/architecture.md#6-per-call-policy-pipeline). | Loaded from `policies/`. |
| MCP server `contoso-erp-orders-mcp` | Exposes the orders tools as an MCP server that passes through to the MCP adapter in each cell. | Preview API version, suppressed `BCP081` warning. Verify the property shape. |
| Product | Commercial bundle that mirrors the product key in the tenant directory. | `subscriptionRequired: false`. Access comes from the token and the tenant directory, not a subscription key. |
| WAF policy | OWASP-style managed rule set with request body inspection on. | Starts in the mode you pass. Tune exclusions narrowly, from logs. |
| Application Gateway WAF_v2 | Public TLS front door; forwards to the API Management private IP. | Request buffering on for WAF, response buffering off for streaming. Strips client tenant headers. |
| API Center service and environment | Registry and discovery surface for MCP servers, versions, and owners. | Register servers through the portal, CLI, or API Management sync. |

## Prerequisites

You need these before the template is useful. None are created here.

1. **An authorization server for MCP clients.** It issues tokens whose audience is `mcpResourceUri`, carries the `mcp.tools` scope, and includes a `tenant_id` claim. Clients discover it through protected resource metadata at `mcpPrmUrl`. See [../../mcp-oauth-front-door/README.md](../../mcp-oauth-front-door/README.md).
2. **A tenant directory API**, reachable privately from the API Management subnet. It accepts tokens from the API Management managed identity for `tenantDirectoryAppIdUri` and returns the tenant record (status, cell, products, tools, tier). See [../docs/architecture.md#4-reference-architecture](../docs/architecture.md#4-reference-architecture).
3. **An MCP adapter per cell** (for example on Azure Container Apps with internal ingress). It accepts tokens from the API Management managed identity for `backendAppIdUri` and trusts the `X-Contoso-Gateway-Tenant` header **only** on calls authenticated as that identity. See [../../backend-identity-propagation/README.md](../../backend-identity-propagation/README.md).
4. **A TLS certificate in Key Vault** for `mcpHostName`. Pass its versionless secret ID as `tlsCertificateKeyVaultUri`. If the certificate lives in a vault other than the one this template creates, grant the Application Gateway identity access there and make sure the vault resolves privately from the Application Gateway subnet.
5. **The role definition ID of the built-in Azure Event Hubs Data Sender role**, passed as `eventHubsDataSenderRoleId`. Look it up rather than hard-coding it:

   ```powershell
   az role definition list --name "Azure Event Hubs Data Sender" --query "[0].name" -o tsv
   ```

6. **A landing zone** that provides DNS, egress control, and hub connectivity. See [../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md).

## Parameters

Parameters marked **required** have no default on purpose. Sizing, limits, and time values must come from your own measurements and policies.

| Parameter | Required | Meaning |
|---|---|---|
| `namePrefix` | required | Short lowercase prefix for resource names. |
| `location`, `apiCenterLocation` | defaulted | Regions. API Center is available in a subset of regions. |
| `mcpHostName` | defaulted | Public host name MCP clients call (example `mcp.contoso.com`). |
| `tlsCertificateKeyVaultUri` | required | Versionless Key Vault secret ID of the edge TLS certificate. |
| `apimSkuName` | defaulted | `Developer` (non-production) or `Premium` for classic internal VNet injection. |
| `apimSkuCapacity` | required | API Management units, from load tests. |
| `publisherEmail`, `publisherName` | defaulted | API Management publisher details. |
| `appGwMinCapacity`, `appGwMaxCapacity` | required | Application Gateway autoscale bounds, from load tests. |
| `wafMode` | defaulted | `Prevention` by default. Use `Detection` in non-production while you tune. |
| `vnetAddressPrefix`, `appGwSubnetPrefix`, `apimSubnetPrefix`, `peSubnetPrefix` | defaulted | Example address plan. Replace with your IP plan. |
| `createPrivateDnsZones` | defaulted | Create and link private DNS zones for Key Vault and Event Hubs. |
| `idpOpenIdConfigUrl`, `idpIssuer` | defaulted | Authorization server discovery URL and issuer (examples on `login.contoso.com`). |
| `mcpResourceUri` | defaulted | Resource identifier and token audience. Must equal the URL clients call. |
| `mcpPrmUrl` | defaulted | Protected resource metadata URL returned in the 401 challenge. |
| `mcpProductKey` | defaulted | Product key for this MCP server (`orders`). Must match the tenant directory. |
| `mcpServerPath` | defaulted | API Management base path for the MCP server. |
| `tenantDirectoryUrl`, `tenantDirectoryAppIdUri` | required | Tenant directory API location and the App ID URI it accepts. |
| `tenantDirectoryTimeout`, `tenantCacheSeconds` | required | Lookup timeout and tenant record cache duration. Shorter cache means faster offboarding. |
| `tenantRateCalls`, `tenantRatePeriod`, `tenantQuotaCalls`, `tenantQuotaPeriod` | required | Default-tier per-tenant rate limit and quota. |
| `backendAppIdUri` | required | App ID URI the MCP adapters accept tokens for. |
| `cellBackendUrls` | defaulted | Map of cell key to MCP adapter URL. Each key becomes backend `cell-<key>`. |
| `eventHubsDataSenderRoleId` | required | Built-in role definition ID, looked up as shown in the prerequisites. |
| `tags` | defaulted | Resource tags. |

A minimal parameter file looks like this. Every angle-bracket value is yours to supply:

```bicep
using './main.bicep'

param namePrefix = 'erpmcp'
param tlsCertificateKeyVaultUri = 'https://<vault-name>.vault.azure.net/secrets/<certificate-name>'
param apimSkuCapacity = <units-from-load-tests>
param appGwMinCapacity = <min-from-load-tests>
param appGwMaxCapacity = <max-from-load-tests>
param tenantDirectoryUrl = 'https://tenant-directory.erp.contoso.internal'
param tenantDirectoryAppIdUri = 'api://contoso-erp-tenant-directory'
param tenantDirectoryTimeout = '<seconds>'
param tenantCacheSeconds = '<seconds>'
param tenantRateCalls = '<calls>'
param tenantRatePeriod = '<seconds>'
param tenantQuotaCalls = '<calls>'
param tenantQuotaPeriod = '<seconds>'
param backendAppIdUri = 'api://contoso-erp-mcp-adapter'
param eventHubsDataSenderRoleId = '<role-definition-id>'
```

## Named values

Policies reference settings only through named values, so the same XML runs in every environment and limit changes never need a policy redeploy.

| Named value | Set from | Used by |
|---|---|---|
| `idp-openid-config-url` | `idpOpenIdConfigUrl` | `validate-jwt` in the pipeline |
| `idp-issuer` | `idpIssuer` | `validate-jwt` |
| `mcp-resource-uri` | `mcpResourceUri` | `validate-jwt` audience |
| `mcp-prm-url` | `mcpPrmUrl` | 401 `WWW-Authenticate` challenge |
| `mcp-product-key` | `mcpProductKey` | Product entitlement check |
| `tenant-directory-url` | `tenantDirectoryUrl` | Tenant directory fragment |
| `tenant-directory-app-id-uri` | `tenantDirectoryAppIdUri` | Managed identity token for the directory call |
| `tenant-directory-timeout` | `tenantDirectoryTimeout` | Directory `send-request` timeout |
| `tenant-cache-seconds` | `tenantCacheSeconds` | `cache-store-value` duration |
| `tenant-rate-calls`, `tenant-rate-period` | parameters of the same names | `rate-limit-by-key` |
| `tenant-quota-calls`, `tenant-quota-period` | parameters of the same names | `quota-by-key` |
| `backend-app-id-uri` | `backendAppIdUri` | `authentication-managed-identity` toward the cell |
| `appinsights-connection-string` | Key Vault secret (created by the template) | `appinsights` logger |

The default-tier limits are a starting point. To give tiers different limits, add tier-specific named values (for example `tenant-rate-calls-premium`) and select them in the pipeline by the tier in the tenant record. Never let a client-supplied value pick the tier.

Secret values belong in Key Vault-backed named values, never in plain ones. If a backend needs a per-tenant secret, create **one Key Vault-backed named value per tenant secret** and select it by the tenant record, as described in [../docs/architecture.md#8-backend-credentials-and-identity](../docs/architecture.md#8-backend-credentials-and-identity).

## Policy execution order

API Management evaluates policies from the widest scope to the narrowest, and `<base />` places the outer scope's policy:

1. **Global** ([policies/global.xml](policies/global.xml)): delete `X-Tenant-Id`, `X-Contoso-Gateway-Tenant`, and `X-Contoso-User-Assertion` from the incoming request, start a `traceparent` if the client sent none, and return `X-Request-Id` on every response for support correlation.
2. **MCP server** ([policies/mcp-tenant-pipeline.xml](policies/mcp-tenant-pipeline.xml)): the eleven stages from [../docs/architecture.md#6-per-call-policy-pipeline](../docs/architecture.md#6-per-call-policy-pipeline), calling the `tenant-directory` and `mcp-audit` fragments.
3. **Backend**: `forward-request` with response buffering off, so streamed MCP responses pass through as they are produced.
4. **On-error**: map token failures to 401 with the protected-resource challenge, rate-limit and quota failures to 429 with `Retry-After` and a JSON-RPC error, parse failures to 400, and anything else to a generic JSON-RPC internal error with the request ID. Every branch emits the audit event.

Do not read `context.Response.Body` anywhere on the MCP path. It forces buffering and breaks streaming.

## Bicep excerpts

**MCP server (preview resource shape).** The MCP server is an API of type `mcp`. This template passes through to an existing MCP server (the adapter in each cell); the pipeline policy then overrides the backend per tenant with `set-backend-service`.

```bicep
#disable-next-line BCP081
resource mcpServer 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'contoso-erp-orders-mcp'
  properties: {
    displayName: 'Contoso ERP orders (MCP)'
    type: 'mcp'
    path: mcpServerPath
    protocols: [ 'https' ]
    subscriptionRequired: false
    serviceUrl: cellBackendUrls.a
    mcpProperties: {
      transportType: 'streamable'
      endpoints: [ { name: 'message', uriTemplate: '/mcp' } ]
    }
  }
}
```

**Key Vault-backed named value.** API Management reads the secret with its managed identity over the private endpoint, so the role assignment and private DNS must exist first.

```bicep
resource nvAppInsights 'Microsoft.ApiManagement/service/namedValues@2024-05-01' = {
  parent: apim
  name: 'appinsights-connection-string'
  properties: {
    displayName: 'appinsights-connection-string'
    secret: true
    keyVault: { secretIdentifier: kvSecretAppInsights.properties.secretUri }
  }
  dependsOn: [ kvSecretsUserApim, kvPeDns ]
}
```

**Audit logger with managed identity.** No connection string or shared access key is stored.

```bicep
resource auditLogger 'Microsoft.ApiManagement/service/loggers@2024-05-01' = {
  parent: apim
  name: 'audit-eventhub'
  properties: {
    loggerType: 'azureEventHub'
    credentials: {
      endpointAddress: '${ehNamespace.name}.servicebus.windows.net'
      identityClientId: 'SystemAssigned'
      name: auditHub.name
    }
  }
}
```

**Edge buffering and header stripping.** WAF needs request buffering. Response buffering stays off so server-sent events flow. An empty header value in a rewrite rule removes the header.

```bicep
globalConfiguration: {
  enableRequestBuffering: true
  enableResponseBuffering: false
}
rewriteRuleSets: [
  {
    name: 'strip-tenant-hints'
    properties: {
      rewriteRules: [
        {
          name: 'remove-client-tenant-headers'
          ruleSequence: 100
          actionSet: {
            requestHeaderConfigurations: [
              { headerName: 'X-Tenant-Id', headerValue: '' }
              { headerName: 'X-Contoso-Gateway-Tenant', headerValue: '' }
            ]
          }
        }
      ]
    }
  }
]
```

## Deploy

Run a what-if first, then deploy. Placeholders are in angle brackets.

```powershell
az bicep build --file assets/ai-gateway/multi-tenant-mcp-gateway/infra/main.bicep --stdout | Out-Null

az deployment group what-if `
  --resource-group <resource-group> `
  --template-file assets/ai-gateway/multi-tenant-mcp-gateway/infra/main.bicep `
  --parameters <your-parameters>.bicepparam

az deployment group create `
  --resource-group <resource-group> `
  --template-file assets/ai-gateway/multi-tenant-mcp-gateway/infra/main.bicep `
  --parameters <your-parameters>.bicepparam
```

Expect the build to report only the suppressed preview-type warnings for the MCP server resources. API Management with VNet injection takes a long time to provision. Plan the deployment window accordingly.

## After deployment

1. **Point DNS** for `mcpHostName` at the Application Gateway public IP (`appGatewayPublicIp` output), or at the upstream firewall or NVA that fronts it.
2. **Verify the client URL.** Open the MCP server in the portal and copy the server URL it shows. Set `mcpResourceUri` and the resource in your protected resource metadata to exactly that URL, as seen through the public host name. If the base path and the endpoint template compose into a doubled segment (for example `/mcp/mcp`), change `mcpServerPath` or add a URL rewrite at the edge, then redeploy.
3. **Publish protected resource metadata** at `mcpPrmUrl`. See [../../mcp-oauth-front-door/README.md](../../mcp-oauth-front-door/README.md).
4. **Enable custom metrics with dimensions** in Application Insights so the `tenant` dimension on `mcp-requests` is kept.
5. **Connect the audit hub to your SIEM**, for example Microsoft Sentinel, with a consumer group per reader.
6. **Register the MCP server** and its owner, version, and lifecycle stage in API Center. See [../docs/architecture.md#10-catalog-and-governance](../docs/architecture.md#10-catalog-and-governance).
7. **Run the negative tests** in [../docs/evaluation-and-troubleshooting.md](../docs/evaluation-and-troubleshooting.md) before onboarding the first tenant: spoofed header, list-skipping `tools/call`, unknown tenant, suspended tenant, rate limit, and missing token.

## Known gaps

These are deliberate simplifications or unverified points. Treat each as a task before production use.

| Gap | What to do |
|---|---|
| MCP server resource uses a preview API version and an unverified property shape. | Compare `type`, `mcpProperties`, and endpoint fields with the current ARM reference. Or create the MCP server in the portal and export it. |
| Binding a product to an MCP server through `products/apis`. | Confirm it is supported for MCP-type APIs in your tier. If not, keep the product as a commercial construct only. Entitlement is enforced by the tenant directory either way. |
| Client URL composition (`mcpServerPath` plus the endpoint template). | Verify in the portal, then align `mcpResourceUri` and protected resource metadata. |
| Application Gateway probe interval, timeout, and threshold are omitted. | Set them from your health objectives. The deployment may require them. |
| Backend `requestTimeout` and WAF body size limits are omitted. | Set them deliberately for long-lived streamable HTTP and server-sent events and for your largest JSON-RPC payload. |
| No circuit breaker on cell backends. | Add circuit-breaker rules from your SLOs so one unhealthy cell fails fast. |
| Leading XML comments in policy files. | Strip them in the pipeline if your deployment rejects them. |
| Private DNS for Key Vault and Event Hubs must resolve from the API Management and Application Gateway subnets. | Use the zones created here, or link the hub's zones and set `createPrivateDnsZones` to `false`. |
| Covers classic internal VNet injection only. | v2 tiers use different networking (VNet integration, private endpoints). Rework the network section if you choose a v2 tier. Check current docs for MCP support per tier. |
| No custom domain on API Management. | Application Gateway overrides the Host header to the default gateway host. Add a custom domain if you need one end to end. |
| MCP servers are not supported in API Management workspaces at the time of writing. | Keep MCP servers at the service level. Check current docs. |

For the full production checklist see [../docs/architecture.md#17-production-checklist](../docs/architecture.md#17-production-checklist). For identities and roles see [../docs/authentication-and-governance.md](../docs/authentication-and-governance.md).

## Related

- [../README.md](../README.md): asset overview.
- [../samples/README.md](../samples/README.md): a local simulation of the same pipeline, with self-tests.
- [../../mcp-oauth-front-door/README.md](../../mcp-oauth-front-door/README.md): how MCP clients obtain tokens.
- [../../backend-identity-propagation/README.md](../../backend-identity-propagation/README.md): how user identity reaches the backend.
