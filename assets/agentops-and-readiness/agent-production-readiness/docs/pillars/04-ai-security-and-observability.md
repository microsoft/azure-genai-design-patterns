# AI Security & Observability

Pillar 04 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when tool permissions, data exposure, or
the traceability of agent actions are unclear.

## Pillar definition

This pillar covers the controls that keep an agent within its authorized
boundary and the evidence that shows it stayed there. Agents combine untrusted
input with the ability to act, so security work centers on authority (whom the
agent acts as, and what it may do), content (what can influence it), and
accountability (what can be proven afterward). Observability in this pillar
means security-relevant evidence; operational telemetry is covered in [Agent
Ops](03-agent-ops.md).

Sub-topics:

- **Agent and tool identity.** Distinct identities for agents and tools, managed
  credentials, and the distinction between application authority and delegated
  user authority.
- **Authorization.** Per-operation authorization enforced by the downstream
  system of record, least-privilege tool scopes, and human approval for
  high-impact actions.
- **Prompt-injection and jailbreak defenses.** Treating user input, retrieved
  content, and tool responses as untrusted; filtering inputs and outputs; and
  limiting what a manipulated instruction can reach.
- **Sensitive data protection.** Data classification, minimization in prompts
  and logs, output controls, and retention.
- **Responsible AI controls.** Content safety filters, transparency to users,
  and safeguards matched to the workload's risk classification.
- **Red teaming.** Adversarial testing of the agent and its tools, including
  automated probing, before release and after material changes.
- **Audit trails and detection.** Attributable records of tool calls and
  approvals, and security monitoring that detects misuse rather than only
  collecting logs.

## Common signals

These signals are generalized from real-world implementations; they describe
recurring situations, not statistics.

- Teams commonly get blocked on identity propagation: the agent can reach the
  tool, but the system of record cannot tell which user a request is for. The
  durable fix is to authorize at the backend using an identity it trusts, not to
  instruct the model to respect permissions.
- Teams commonly converge on a few credential patterns: a managed identity for
  application-level access, a delegated user token from a supported token
  exchange for on-behalf-of access, and a narrowly scoped credential in a secret
  store when neither fits. Forwarding the client's token unchanged to downstream
  services is a recurring anti-pattern.
- Teams commonly treat authentication to a tool server as if it authorized every
  tool behind it. Authenticating the connection is necessary, but each tool
  operation still needs its own authorization decision.
- Teams commonly test that access works but not that it stops working. Token
  refresh, revocation, expired delegation, and isolation between users or
  tenants all need explicit negative tests.
- Teams commonly equate log collection with security coverage. Coverage means
  someone has defined which events indicate misuse, built detections for them,
  and knows how to respond.

## Entry questions

1. Which identity does the agent use for each tool call, and when does it act
   with delegated user authority?
2. Where is each tool operation authorized, and does the system of record
   enforce it independently of the model?
3. Which content can reach the model (user input, retrieved documents, and tool
   responses), and which of it is untrusted?
4. What could a successful prompt injection cause the agent to read, change, or
   send?
5. Which actions require human approval, and is approval bound to the specific
   action and its parameters?
6. Which sensitive data appears in prompts, responses, traces, and evaluation
   sets, and how is it minimized and retained?
7. Have token refresh, revocation, expired authority, and cross-user or
   cross-tenant isolation been tested?
8. What adversarial testing has been run, and when will it be repeated?
9. Can every tool call and approval be attributed to a user, an agent, and a
   version?
10. Which detections and response procedures exist for agent misuse?

## Approach and play

Security is the primary owner of [Play C: Secure agent-to-tool and downstream
integration](../readiness-review.md#play-c-secure-agent-to-tool-and-downstream-integration).
The steps below expand Play C and add detection and response.

1. **Map the trust boundary** (Play C step 1). Enumerate tools, authorized
   callers, allowed operations, affected data, and the impact of a wrong or
   repeated action. Mark every point where untrusted content enters.
2. **Choose identity patterns** (Play C step 2). For each tool, decide between
   application authority and delegated user authority, and use a supported
   mechanism for that service. Consider a dedicated agent identity, such as
   Microsoft Entra Agent ID, so agent actions are distinguishable from users and
   other services.
3. **Enforce authorization downstream** (Play C step 3). Scope credentials to
   the minimum operations, authorize each operation at the tool or system of
   record, and authorize each tool individually behind a shared tool server or
   gateway.
4. **Defend against injected instructions** (Play C step 4). Separate
   instructions from data, apply input and output filtering such as Prompt
   Shields, and limit tool reach so a manipulated instruction has little to
   exploit. Run adversarial tests, including automated red teaming.
5. **Bind human approval to actions** (Play C step 5). Present the exact action
   and parameters for approval, and execute only what was approved.
6. **Capture audit evidence and test negatives** (Play C step 6). Record the
   user, agent, version, tool, operation, and approval for each action, without
   secrets or unnecessary content. Test denial, refresh, revocation, expiry,
   isolation, and partial failure.
7. **Detect and respond.** Define misuse signals, such as repeated denied
   operations, unusual tool sequences, or resource exhaustion and runaway cost.
   Build detections, and add agent-specific steps to the incident procedure,
   including how to disable a tool or the whole agent.

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Threat review | A trust-boundary diagram and threat review cover tools, untrusted content sources, and the impact of each action. | Required (Authorization and data) |
| Identity and authorization design | Each tool call has a documented identity pattern and is authorized per operation by the downstream system. | Required (Authorization and data) |
| Negative authorization tests | Tests show denial for unauthorized users and operations, expired or revoked authority, and cross-user or cross-tenant access. | Required (Authorization and data) |
| Human approval binding | High-impact actions run only with an approval bound to the specific action and parameters. | Required (Authorization and data) |
| Data handling | Classification, minimization, retention, and access for prompts, traces, and evaluation data are approved. | Required (Authorization and data) |
| Injection and misuse tests | Adversarial tests, including instructions injected through retrieved content and tool responses, are run and results recorded. | Required (Quality and safety) |
| Audit trail | Tool calls and approvals are attributable to user, agent, and version; logs exclude secrets and minimize sensitive content. | Required (Operations) |
| Detection and response | Misuse detections and an agent-specific incident and disablement procedure exist, each with an owner. | Recommended (Operations) |

## Common pitfalls

- **Authorization in the prompt.** Instructions that tell the model what a user
  may do are not a control.
- **An over-privileged service identity.** One broad identity for all tools
  turns any successful injection into broad access.
- **Token passthrough.** Forwarding the client token to downstream services
  breaks audience boundaries and weakens auditing.
- **Gateway authentication as authorization.** Authenticating to a tool server
  does not authorize every tool behind it.
- **Trusting tool responses.** Content returned by a tool can carry instructions
  as easily as a retrieved document can.
- **Session-wide approval.** Approving a session rather than an action lets
  later actions inherit consent they never received.
- **Logs as coverage.** Collected logs with no detections and no response owner
  do not protect the workload.

## Collaboration boundaries

- **[AI CoE & Governance](01-ai-coe-and-governance.md):** provides the action
  boundary and risk classification, and receives exception and residual-risk
  records for acceptance.
- **[AI Landing Zones](02-ai-landing-zones.md):** provides the managed
  identities, network isolation, secret storage, and policy that security
  controls depend on.
- **[Agent Ops](03-agent-ops.md):** runs misuse and red-team cases as part of
  the release gate and carries audit telemetry. Agree together which fields are
  logged and which are excluded.
- **[Solution Optimization](05-solution-optimization.md):** brings caching,
  pre-computation, and model-routing changes for review so they do not bypass
  authorization or leak data between users.
- **[Proven Patterns for Agent
  Architectures](06-proven-patterns-for-agent-architectures.md):** places tool
  gateways, approval steps, and state so that controls can be enforced, and
  supplies the tool contracts to review.
- **Roles:** security architecture, identity, privacy or data protection,
  application engineering, security operations, and the workload owner, who
  accepts residual risk.

## References

- [Enterprise Agent Security and Tool Governance](../../../../security-and-governance/agent-security-and-governance/README.md)
- [Secure AI](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai/secure)

- [Govern and secure AI agents across the organization](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/governance-security-across-organization)
- [What are agent identities?](https://learn.microsoft.com/en-us/entra/agent-id/what-are-agent-identities)
- [Prompt Shields in Azure AI Content Safety](https://learn.microsoft.com/en-us/azure/ai-services/content-safety/concepts/jailbreak-detection)
- [AI Red Teaming Agent](https://learn.microsoft.com/en-us/azure/foundry/concepts/ai-red-teaming-agent)
- [Responsible AI in Azure workloads](https://learn.microsoft.com/en-us/azure/well-architected/ai/responsible-ai)
