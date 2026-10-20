# ADR 0001: Two-stage private Foundry landing zone

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Enterprises that adopt Microsoft Foundry agents usually need a
network-isolated deployment for production. Two things make starting there
risky:

- **Full isolation is slow to set up and hard to debug.** The capability host
  alone takes 30–35 minutes to provision. A private DNS or peering mistake
  looks like an authentication or tool failure. If teams start private-first,
  they often can't tell whether a failure comes from the network, identity or
  the tool contract.
- **The functional contracts are independent of the network.** Token
  validation, Key Vault references, managed identities, Toolbox namespace
  pinning, OAuth passthrough and evaluation gates behave the same on public
  and private endpoints.

We also saw two recurring failures:

- When the Container Apps environment and the app were deployed in one
  template, the app's host allow-list was evaluated against an environment
  that didn't exist yet, so every request got HTTP 421 on the first deploy.
- Putting an operator or runner subnet into the foundation VNet was overwritten
  by the next foundation deployment. Both foundation subnets are delegated to
  `Microsoft.App/environments`, so the runner couldn't go there anyway.

## Decision

1. **Adopt in two stages.**
   - **Stage 1 — Foundations and functional validation profile.** Use public
     HTTPS endpoints with mandatory Entra bearer validation
     (`disableLocalAuth: true`), Key Vault secret references, managed
     identities, a versioned Toolbox with `<connection-name>___<tool-name>`
     pinning, a Prompt Agent with a Foundry IQ knowledge base, a Hosted Agent
     with OAuth passthrough (`access_as_user`), and a repeatable evaluation
     gate. Stage 1 isn't a production profile.
   - **Stage 2 — Private-by-default enterprise landing zone.** Use the same
     contracts with `publicNetworkAccess: 'Disabled'`, delegated subnets for
     the capability host and the internal MCP environment, private endpoints
     and DNS zones, a private operator runner, split bootstrap, foundation and
     release identities, and one scoped exception: Azure Bot Service
     Activity-only ingress for Microsoft 365 and Teams.
2. **Split the Bicep lifecycle.** `foundation.bicep` owns the shared state (ACR,
   Key Vault, Log Analytics, Application Insights and the Container Apps
   environment). Application revisions are deployed separately and consume
   its outputs, including `defaultDomain`.
3. **Use a separate, peered operator VNet**, with explicit private DNS zone
   links to every platform zone.

## Consequences

**Positive**

- Functional, identity and tool problems are found and fixed before
  networking is added. Stage 2 failures can then be attributed to the network.
- `ALLOWED_HOSTS` is derived from a real `defaultDomain`, so there's no 421 on
  the first deploy.
- Application redeployments and rollbacks can't delete shared resources. The
  release identity needs only Reader + project-scoped Foundry User.
- Operator access is audited and revoked by removing one peering.

**Negative**

- Two profiles have to be kept equivalent. Stage 1 must not grow
  shortcuts, such as keys or anonymous endpoints, that Stage 2 can't keep.
- More deployments to orchestrate in order, and outputs to pass between them.
- Every new private DNS zone has to be linked to the operator VNet too, or
  operator steps fail to resolve it.
- The Microsoft 365 ingress exception has to be owned, approved and reviewed.

## Alternatives considered

| Alternative | Why we didn't choose it |
|-------------|--------------------------|
| Private-first, single stage | Mixes network, identity and contract failures. Slow feedback loop |
| Public-only with IP allow-lists | Doesn't meet enterprise isolation requirements. IP lists are fragile for platform services |
| One template for environment and app | Causes 421s on the first deploy, and app redeployments put shared state at risk |
| Runner subnet in the foundation VNet | Overwritten by the foundation's subnet array, and the subnets are delegated |
| Public runners with temporary firewall openings | Reopens public access on every release, and is hard to audit |
