# 0001: Enforce tool governance through four platform and source gates

- **Status:** Accepted
- **Date:** 2026-10-20

## Context

Enterprise agents increasingly call tools that read sensitive data and change
state in business systems. The language model decides which tool to call and with
which arguments, and that decision can be manipulated by direct jailbreaks,
indirect prompt injection in tool output (logs, tickets, web pages), or simple
reasoning errors. Several teams share responsibility: agent developers ship code,
security owns policy, and source-system owners own the data.

We need controls that hold even when the model is fooled, that security can
change without redeploying the agent, and that produce evidence of who did what.

## Decision

Authorization and safety for agent tool calls are enforced by four independent,
serial gates outside the model, and never by prompt instructions or checks inside
agent code:

1. **Tool catalog allow-list** in a versioned Foundry Toolbox (`allowed_tools`).
2. **Connection identity**: Entra Agent ID for the agent's own calls, OAuth
   passthrough for a signed-in user; no static keys.
3. **Human approval** for state-changing tools, declared on the Toolbox
   (`require_approval`) and enforced by the runtime (Agent Framework
   `approval_mode`), failing closed.
4. **Source-side entitlement** in the downstream MCP server or API, from
   validated token claims (`roles` / `scp`), deny by default, with a structured
   audit record per decision.

These are complemented by guardrails (Prompt Shields, egress allow-list), an
evaluation release gate, platform traces and a two-lever kill switch.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| **Prompt-only rules** ("never touch org-b", "ask before writing") | The model is the component being attacked. Instructions are advisory and can be overridden by injected text. Provides no evidence. |
| **Authorization checks in agent code** | Better than prompts, but couples policy to deployments, is bypassed by any other client of the same tools, and requires the agent to hold credentials. Security cannot change policy without a release. |
| **Gateway-only enforcement** (one API gateway policy) | Valuable as an extra layer, but a gateway sees requests, not business entitlements; it cannot know that a device belongs to `org-b` without duplicating source logic. |
| **Human approval as the authorization control** | Approval answers "should this happen now?", not "is this principal allowed?". Approvers can be tired or misled, and clients may not honor approval metadata. |
| **Single shared service identity with an API key** | No attribution per agent or per user, no least privilege, and secrets in configuration. Cannot be disabled for one agent. |

## Consequences

**Positive**

- A manipulated model can, at worst, propose calls that are then refused, held
  for approval or denied with an audit record.
- Security and source owners change tool surface, approval policy and
  entitlements as configuration, without agent redeploys.
- Each gate answers one of the four questions with its own evidence, giving a
  complete audit trail.
- The identity design is the same object Microsoft Agent 365 and Entra ID
  Governance manage, so fleet governance needs no rework.

**Negative**

- More components to configure, test and monitor; several are in preview.
- Each source system must validate tokens and implement entitlement logic.
- Role changes and identity disablement are eventual (token lifetime), so
  runbooks need an immediate source-side revocation path.
- Approval depends on client cooperation, so it must never be the only control.

## References

- [Architecture walkthrough](../architecture.md)
- [Agent 365 governance mapping](../agent365-governance-mapping.md)
