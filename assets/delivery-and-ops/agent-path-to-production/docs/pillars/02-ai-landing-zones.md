# AI Landing Zones

Pillar 02 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when identity, networking, environment
separation, or platform controls prevent safe deployment.

## Pillar definition

This pillar covers the platform foundation that agentic workloads deploy into:
the subscriptions, networks, identities, policies, and shared services that let
a team deploy repeatedly and safely without negotiating each control from
scratch. An AI landing zone extends an organization's existing landing zone with
the services and controls that models, agents, and their tools need.

Sub-topics:

- **Network topology.** Hub-and-spoke or an equivalent topology, egress control
  through a network firewall, ingress for user channels, and segmentation
  between the agent runtime, data stores, and tools.
- **Private connectivity.** Private endpoints for model, data, and agent
  services, private DNS resolution, and controlled outbound access for tools
  that must reach the internet.
- **Identity and access.** Managed identities for services, role-based access
  control (RBAC) scoped to least privilege, and separation of build, deploy, and
  operate permissions.
- **Policy and guardrails.** Azure Policy or an equivalent to enforce allowed
  regions, SKUs, network isolation, diagnostic settings, and tagging, with a
  documented exemption process.
- **Environments and infrastructure as code.** Separate development, test, and
  production environments, deployed from version-controlled templates so that
  the reviewed design is what actually runs.
- **Capacity and resilience.** Model availability and quota by region,
  throughput planning, failover options, and the dependencies that a regional
  outage would affect.
- **Platform operations.** Ownership of shared services, upgrade cadence, cost
  tagging, and a route for workload teams to request platform changes.

## Field signals

These signals are generalized from delivery reviews; they describe recurring
situations, not statistics.

- Teams commonly treat private networking and DNS as the entry gate for
  production. Pilots built on public endpoints stall when the workload must move
  inside the corporate network, and private DNS resolution is a frequent point
  of failure.
- Teams commonly find that network inspection order and address translation
  break IP-based controls. When traffic passes through a third-party firewall or
  proxy, the source address a service sees is not the caller's, so access rules
  keyed to IP ranges either fail or become too broad. Keying access to identity
  is more robust.
- Teams commonly underestimate ingress design when users reach the agent through
  a collaboration suite or another managed channel. The channel's service
  endpoints, authentication flow, and callback paths must be reachable without
  exposing the agent runtime to the internet.
- Teams commonly validate model availability, quota, and regional placement
  late. Checking them early avoids redesigning around a model or region that
  cannot meet the workload's data-residency or throughput requirements.

## Entry questions

1. Which landing zone will host the workload, and does it already provide
   network, identity, and policy foundations?
2. Which services must be reachable privately, and how is private DNS resolved
   from every caller, including build agents and user channels?
3. How does outbound traffic reach tools and external APIs, and what inspects or
   translates that traffic on the way?
4. Which identity does each component use, and are those managed identities with
   least-privilege role assignments?
5. Which policies apply, which would the current design violate, and who grants
   exemptions?
6. Are development, test, and production separated, and is each deployed from
   the same version-controlled templates?
7. Are the required models available with sufficient quota in the target
   regions, and what happens if a region is unavailable?
8. Who owns the shared platform services, and how does a workload team request a
   change?

## Approach and play

Landing zone work supports [Play
A](../runbook.md#play-a-choose-an-operable-agent-architecture) (step 3, data and
control flows) and [Play
C](../runbook.md#play-c-secure-agent-to-tool-and-downstream-integration) (steps
2 and 3, identity and constrained credentials). It rarely needs a separate play;
it needs early evidence that the required paths and controls work in a
representative environment.

1. **Confirm the entry gate.** Identify the target landing zone and the platform
   owner during [Triage and route](../runbook.md#2-triage-and-route). Record
   which foundations exist and which are gaps.
2. **Draw the deployment view.** Extend the Play A flow diagram with
   subscriptions, networks, private endpoints, DNS zones, egress paths, ingress
   for user channels, and identities.
3. **Prove the paths.** From each caller (agent runtime, build agent, user
   channel, and operator), test that required private endpoints resolve and
   connect, and that paths that should be blocked are blocked.
4. **Assign identities.** Use managed identities for service-to-service calls
   and grant least-privilege roles. Where a firewall or proxy translates
   addresses, base access decisions on identity rather than source IP.
5. **Test against policy early.** Deploy the design into a policy-enforced test
   environment to surface violations before production. Record each exemption
   with an approver, scope, and expiry.
6. **Codify the environment.** Deploy from version-controlled templates through
   a pipeline, so the reviewed design and the running environment do not drift.
   Hand the pipeline to [Agent Ops](03-agent-ops.md) for release gating.
7. **Check capacity and resilience.** Confirm model availability and quota in
   the target regions, plan throughput, and document expected behavior and the
   recovery path during a regional or dependency outage.
8. **Agree platform ownership.** Record who operates each shared service and how
   changes are requested, and attach the evidence to the [readiness
   record](../readiness-record.md#evidence-and-acceptance).

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Deployment view | A reviewed diagram shows networks, private endpoints, DNS, ingress, egress, identities, and environment boundaries. | Required (Architecture and platform) |
| Connectivity tests | Each required path is tested from each caller, and paths that should be blocked are confirmed blocked. | Required (Architecture and platform) |
| Identity and RBAC | Services use managed identities with least-privilege roles; no long-lived secrets are embedded in code or configuration. | Required (Authorization and data) |
| Policy compliance | The design deploys in a policy-enforced environment; exemptions have an approver, scope, and expiry. | Required (Architecture and platform) |
| Infrastructure as code | Environments are deployed from version-controlled templates through a pipeline. | Recommended (Change and recovery) |
| Capacity and regional plan | Model availability and quota are confirmed for target regions, with documented outage behavior and recovery. | Recommended (Architecture and platform) |
| Platform ownership | Shared-service owners and the change request route are recorded. | Recommended (Operations) |

## Common pitfalls

- **Building the pilot outside the landing zone.** A pilot on public endpoints
  proves the model, not the deployment. Move into a representative environment
  before claiming readiness.
- **DNS as an afterthought.** Private endpoints without correct DNS resolution
  from every caller fail in ways that look like application bugs.
- **IP allow lists behind translation.** Allow lists keyed to source addresses
  become meaningless or overly broad once traffic is translated. Prefer
  identity-based access.
- **Open-ended exemptions.** A permanent policy exemption granted to unblock a
  pilot quietly becomes production configuration. Scope each exemption and give
  it an expiry.
- **A single environment.** Testing in production, or deploying production by
  hand, removes the ability to gate and roll back changes.
- **Late quota checks.** Discovering at release that a model is unavailable or
  under-provisioned in the required region forces a redesign.

## Collaboration boundaries

- **[AI CoE & Governance](01-ai-coe-and-governance.md):** supplies the risk
  classification and data-residency requirements that drive region, isolation,
  and policy choices.
- **[Agent Ops](03-agent-ops.md):** consumes the deployment pipeline,
  environments, and monitoring workspace, and owns the release gates that run in
  them.
- **[AI Security & Observability](04-ai-security-and-observability.md):** relies
  on the identity model, secret storage, and network boundaries to enforce tool
  authorization, and owns the threat review of exposed paths.
- **[Solution Optimization](05-solution-optimization.md):** needs throughput,
  quota, and regional data to size capacity and compare cost options.
- **[Proven Patterns for Agent
  Architectures](06-proven-patterns-for-agent-architectures.md):** chooses the
  runtime and hosting model the platform must support, such as a managed agent
  service or a custom container runtime.
- **Roles:** platform and infrastructure, network, identity, application
  engineering, and the platform owner. Platform specialists lead platform
  concerns; the workload team owns the application deployed on the platform.

## References

- [Secured Microsoft Foundry Landing Zone for Enterprise Agents](../../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
- [AI Ready](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/ready)

- [What is an Azure landing zone?](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ready/landing-zone/)
- [Baseline Microsoft Foundry chat reference architecture](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/architecture/baseline-microsoft-foundry-chat)
- [How to configure network isolation for Microsoft Foundry](https://learn.microsoft.com/en-us/azure/foundry/how-to/configure-private-link)
- [Application platform for AI workloads on Azure](https://learn.microsoft.com/en-us/azure/well-architected/ai/application-platform)
