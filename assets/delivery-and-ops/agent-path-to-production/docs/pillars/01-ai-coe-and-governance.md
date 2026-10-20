# AI CoE & Governance

Pillar 01 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when decision rights, acceptable use,
accountable ownership, or the route from experiment to supported service are
unclear.

## Pillar definition

This pillar covers how an organization decides which agentic workloads to build,
who may approve them, and how they move from experiment to supported service. It
is the organizational counterpart to the technical pillars: a well-engineered
agent still stalls if nobody can approve its use boundary, accept its residual
risk, or fund its operation.

Sub-topics:

- **Operating model.** Where a center of excellence (CoE) or equivalent function
  sits, which decisions it holds centrally, and which it delegates to product
  teams. Models range from a central team that builds most agents to a platform
  team that enables team-owned agents under shared guardrails.
- **Intake and graduation.** A lightweight route for proposing a use case,
  classifying its risk, and promoting it from experiment to pilot to production
  with explicit criteria at each step.
- **Responsible AI policy.** Acceptable-use rules, impact assessment,
  human-oversight requirements, transparency to users, and an exception process
  that names who can grant an exception and for how long.
- **Agent inventory and lifecycle.** A register of agents with their owners,
  purpose, data and tool access, environment, and status, including how agents
  are retired.
- **Cost accountability.** Attribution of model, tool, and platform consumption
  to an owner, budgets and alerts, and a periodic review of value against cost.
- **Skills and enablement.** Role-based learning for builders, reviewers, and
  operators, plus reusable guidance that lowers the cost of doing the right
  thing.

## Field signals

These signals are generalized from delivery reviews; they describe recurring
situations, not statistics.

- Teams commonly move from many one-off agents toward a shared platform on which
  product teams own their agents. The hardest part is usually not the platform
  itself but agreeing which guardrails the platform enforces and which ones the
  owning team must evidence.
- Teams commonly ask for an agent inventory or control plane early, often before
  a second production agent exists. Without one, nobody can say which agents can
  reach which data, who owns them, or which ones should be retired.
- Teams commonly discover late that delivery needs several disciplines at once:
  application engineering, platform, identity, data, and security. Engagements
  run more smoothly when that cross-discipline team and its decision owners are
  named at intake rather than recruited after a blocker appears.
- Teams that expose agents to external customers or tenants commonly conflate
  entitlement (whether a tenant has been granted a capability) with
  authorization (whether a specific caller may perform a specific action). They
  also tend to need per-tenant metering well before they need a pricing model.

## Entry questions

1. What user or business outcome does the workload serve, and who is accountable
   for it?
2. Who may approve the use case, its data access, its tool actions, exceptions,
   and production release?
3. Has the use case been classified for risk, and which responsible AI
   requirements follow from that classification?
4. Which actions require human oversight, and who is authorized to provide it?
5. Is there an inventory entry with owner, purpose, data, tools, environment,
   and status?
6. What criteria move the workload from experiment to pilot and from pilot to
   production, and who confirms them?
7. How is consumption attributed and budgeted, and who reviews value against
   cost?
8. If the workload serves external tenants, how are entitlement, authorization,
   and metering kept separate?
9. Who operates the workload after release, and do they have the skills and
   access to do so?
10. How is the workload retired or disabled, and who decides?

## Approach and play

Governance work rarely needs its own technical play. It frames the engagement in
[section 1 of the runbook](../runbook.md#1-identify-and-frame) and produces the
decision in [section 4](../runbook.md#4-release-and-operate). Keep it
proportionate: the aim is the minimum decision path that lets the workload
proceed safely, not a new governance program.

1. **Name the owners.** Record the engagement coordinator, workload owner,
   operating owner, and decision owner as part of [Identify and
   frame](../runbook.md#1-identify-and-frame). Confirm that each person has
   accepted the role.
2. **Map the minimum decision path.** List the decisions this workload needs
   (use case, data access, tool actions, exceptions, and release) and the person
   or forum that makes each one. Reuse existing forums before creating new ones.
3. **Classify and bound use.** Apply the organization's risk classification and
   acceptable-use policy. Record permitted users, data, actions, and non-goals.
   The action boundary feeds the human-approval step in [Play
   C](../runbook.md#play-c-secure-agent-to-tool-and-downstream-integration).
4. **Register the agent.** Create or update the inventory entry with owner,
   purpose, data and tool access, environment, and status. Link it from the
   readiness record.
5. **Agree graduation criteria.** Translate the release gates in [section
   4](../runbook.md#4-release-and-operate) into workload-specific criteria
   before testing, so evidence is judged against an agreed standard rather than
   a demonstration.
6. **Attribute cost.** Tag or meter consumption to the owning team and set a
   budget and alert. Hand optimization questions to [Solution
   Optimization](05-solution-optimization.md).
7. **Record the decision.** Capture proceed, rework, or stop, with the accepted
   residual risks and follow-up, in the [decision section of the readiness
   record](../readiness-record.md#decision).
8. **Feed the CoE.** Share generic lessons, reusable guardrails, and gaps in the
   operating model through [Harvest reusable
   learning](../runbook.md#5-harvest-reusable-learning).

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Accountable owners | Workload owner, operating owner, and decision owner are named and have accepted their roles. | Required (Outcome and scope) |
| Use boundary | Permitted users, data, actions, and non-goals are recorded and approved under the acceptable-use policy. | Required (Outcome and scope) |
| Risk classification and responsible AI review | The use case is classified, the resulting requirements are listed, and evidence for each is linked. | Required (Residual risk) |
| Decision and exception path | Each needed decision has a named owner or forum; each exception has an approver, scope, and expiry. | Required (Residual risk) |
| Graduation criteria | Workload-specific criteria for pilot and production were agreed before testing began. | Recommended (Outcome and scope) |
| Inventory entry | The agent is registered with owner, purpose, data and tool access, environment, and status. | Recommended (Operations) |
| Cost attribution | Consumption is attributed to the owning team, with a budget and an alert. | Recommended (Operations) |
| Operator readiness | The operating team has the access, procedures, and skills to support the workload. | Recommended (Operations) |

## Common pitfalls

- **Governance as a final gate.** Policy questions raised only at release cause
  rework. Classify and bound use at intake.
- **One forum for every decision.** Routing low-risk changes through the same
  review as high-risk ones slows everything. Scale review effort to the risk
  classification.
- **Policy without enforcement.** An acceptable-use rule that no platform
  control or test checks is an aspiration. Pair each material rule with a
  control or an evidence item.
- **No owner after the pilot.** A pilot team that disbands leaves an unowned
  production service. Name the operating owner before release.
- **An inventory nobody updates.** Tie registration to deployment or access
  grants so the inventory reflects what is actually running.
- **Training completion as readiness.** Skills matter, but completed courses are
  not evidence that a workload is safe to operate.
- **Entitlement as authorization.** A tenant's license does not authorize every
  action for every user in that tenant.

## Collaboration boundaries

- **[AI Landing Zones](02-ai-landing-zones.md):** governance sets the rules; the
  platform team enforces those that can be automated through policy, RBAC, and
  environment separation. Hand over the risk classification, data-residency
  needs, and use boundary.
- **[Agent Ops](03-agent-ops.md):** produces the evaluation and release evidence
  that graduation criteria depend on. Agree the criteria together before
  testing.
- **[AI Security & Observability](04-ai-security-and-observability.md):** turns
  the action boundary into tested authorization and human-approval controls, and
  supplies audit evidence for exceptions.
- **[Solution Optimization](05-solution-optimization.md):** runs cost and
  performance experiments once consumption is attributed and budgets exist.
- **[Proven Patterns for Agent
  Architectures](06-proven-patterns-for-agent-architectures.md):** confirms
  whether the use case needs an agent at all, which is often the cheapest
  governance decision available.
- **Roles:** workload owner, responsible AI or risk reviewer, security, privacy
  or legal where required, finance or FinOps, and the operating team. The
  coordinator keeps the decision log; the accountable owner accepts residual
  risk.

## References

- [Establish an AI Center of Excellence](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/center-of-excellence)
- [Govern AI](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/govern)
- [Responsible AI policies](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/responsible-ai-policies)
- [Govern and secure AI agents across the organization](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/governance-security-across-organization)
- [Business plan for AI agents](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/business-strategy-plan)
