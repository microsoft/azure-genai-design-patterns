# ADR 0001: Default to prompt agents, escalate to hosted agents only for a named control

- **Status:** Accepted
- **Date:** 2026-10-20
- **Scope:** Every new agent scenario built on Microsoft Foundry Agent Service, and every change of runtime for an existing scenario
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

Foundry Agent Service offers two runtimes that share one project, one set of model deployments, one set of connections, and one network boundary:

- **Prompt agents.** These are declarative. You supply the model, instructions, and platform-managed tools (file search, Azure AI Search, Code Interpreter, OpenAPI, MCP, agent-to-agent, and function tools). Foundry runs the loop, injects connection credentials, and stores conversations.
- **Hosted agents (preview).** These are your own code, packaged as a container image. Foundry deploys the image, gives it an Entra agent identity, and runs it in isolated per-session sandboxes that scale to zero. You own the loop, the dependencies, the protocol surface, any custom state, and outbound network restrictions.

Teams that have both runtimes available tend to drift to one of two poles:

1. **Hosting by default.** Code feels familiar and flexible, so every agent becomes a container. The result is many images that do little more than wrap a prompt and a tool list. Each one still carries a full supply chain, egress rules, RBAC grants, on-call duty, and a slower first turn.
2. **Prompting by default, with no exit.** Deterministic, multi-step, or library-dependent work is forced into instructions. Steps get skipped or reordered, proprietary logic gets re-described in natural language, and teams cannot add the telemetry they need.

These forces shape the decision:

- **Ownership cost.** A hosted agent's operating cost is mostly people, not compute: image builds, dependency patching, vulnerability response, registry management, egress allowlists, sizing reviews, and incident ownership.
- **Security boundary.** A prompt agent's blast radius is bounded by its tools and connections. A hosted agent can reach whatever its identity and network allow, and outbound traffic is open unless you restrict it.
- **Governance.** In both runtimes, guardrails apply to model calls that go through governed Foundry deployments. Only in a hosted agent can code point the model client somewhere else, so gateway use must be enforced by policy and network controls.
- **Control.** Some requirements cannot be met declaratively: a fixed step order with compensation, an in-process proprietary library, a non-conversational protocol, custom durable state, or custom spans and metrics.
- **First-turn experience.** Hosted sessions must be provisioned or resumed. Prompt agents have no customer-visible container layer.
- **Preview churn.** Hosted agents, some tools, memory, some protocols, and bring-your-own gateway support are in preview. Any decision has to survive platform change.

## Decision

1. **Prompt agent is the default runtime.** Every new scenario starts as a prompt agent unless a named control below is a hard requirement. That prompt agent also becomes the evaluation baseline and, where possible, the fallback.
2. **Escalate to a hosted agent only for a named control.** The ADR for the scenario must name at least one of these as a hard requirement:
   - The agent loop itself must be code: a fixed order, explicit branching, compensation, or approval checkpoints across the whole job.
   - Proprietary, native, or licensed libraries must run inside the loop, not behind a tool.
   - The agent must speak a protocol that a prompt agent does not: arbitrary JSON or webhook payloads (Invocations), real-time streaming, or serving as an agent-to-agent peer.
   - Custom durable state or working files must persist across turns in a store you control.
   - Custom spans, metrics, or attributes are required inside the loop.

   "We prefer code" or "we might need it later" does not qualify.
3. **Prefer isolating the gap behind a tool first.** If only one capability is missing, expose it as an OpenAPI, MCP, function, or agent-to-agent tool and keep the prompt agent. Host the whole agent only when the loop itself must be code.
4. **Mix per scenario, not per estate.** When a user-facing front door must stay platform-governed and the heavy work needs code, use a **mixed** design:
   - The prompt agent owns the conversation and guardrails.
   - The hosted capability sits behind a tool boundary and returns structured results.
   - Exactly one layer plans. The other executes.
5. **Invariants that apply in every runtime:**
   - Untrusted or model-generated code never runs in the agent's process. It runs in a session pool with egress disabled and a separate identity.
   - User identity is never carried in prompts, tool parameters, headers, or baggage. Use delegated tool connections (MCP or Toolbox OAuth), a channel's on-behalf-of support, or a server-side context store keyed by conversation.
   - Model calls go only to governed Foundry deployments or an approved gateway. For hosted agents, enforce this with egress rules.
   - Hosted agents keep the Responses protocol unless a named need requires another, so clients and evaluation datasets stay portable.
   - Every new version gets a CI smoke turn, and the release plan accounts for existing sessions that keep using the previous version.
6. **Record the decision.** Each scenario's ADR attaches the [runtime selector](../../samples/runtime_selector.py) output, the relevant rows of the [decision matrix](../architecture.md#7-decision-matrix), and the named control (if hosted). Revisit the decision when the named control disappears or a platform capability removes the need for it.

## Consequences

### Positive

- Most agents carry the smallest operational footprint: a definition, connections, and evaluation gates, with no image or container supply chain.
- Every hosted agent has a written reason to exist, which makes architecture and security reviews faster and keeps the hosted estate small enough to patch and monitor.
- Guardrails, credential injection, and conversation storage stay platform-managed on user-facing paths, including in mixed designs.
- Evaluation datasets carry over across runtimes because hosted agents default to the Responses protocol. Parity evaluation during migration needs no new harness.
- Migrating in either direction is routine: prompt → hosted when a control becomes mandatory, hosted → prompt when platform tools catch up.
- The invariants (session pool for untrusted code, no identity in headers or prompts, enforced gateway) hold regardless of runtime, so reviewers check one list.

### Trade-offs and operational obligations

- Teams must write a short justification for every hosted agent. Some will see this as friction.
- Mixed designs run two runtimes and a contract between them. That contract needs a schema, versioning, trace propagation, and an owner.
- The tool-first rule can push a team toward a prompt agent with several tools, when a single hosted agent would be simpler. Reviewers must watch for tool sprawl and reopen the decision when it appears.
- Hosted agents need extra platform investment before their first production release: self-hosted CI runners inside the network, an egress allowlist, registry pull rights for the project identity, container log access, and explicit data-plane RBAC.
- Preview capabilities can change. The decision has to be revisited when hosted agent, protocol, memory, or gateway behavior changes. Check the current Microsoft Learn documentation at each review.
- The selector is a rubric, not an oracle. Architects must still read the rationale and warnings and apply judgment.

## Alternatives considered

| Alternative | Why it was rejected | When to reconsider |
|---|---|---|
| **Always use hosted agents** | Maximizes control, but every agent pays the full operational cost: image supply chain, egress rules, RBAC, sizing, and on-call. Simple grounded assistants gain nothing, and the hosted fleet becomes hard to patch and audit. | If the platform makes hosted operations nearly free (managed images, default egress lockdown, built-in gateway enforcement) *and* most of your scenarios need code-level control. |
| **Always use prompt agents** | Forces deterministic workflows, proprietary libraries, and custom protocols into instructions or awkward tool chains. Leads to skipped steps and to logic that cannot be tested or observed. | If platform workflow and tool capabilities cover your deterministic and protocol needs well enough that no named control remains. |
| **Decide once for the whole estate** | Different scenarios in one product have different needs. A single estate-wide choice guarantees bad fits in one direction or the other. | Only for a very small, homogeneous portfolio where every scenario genuinely has the same profile. |
| **Self-host agents on your own container platform outside Foundry** (for example, Azure Container Apps or AKS) | You give up per-session sandboxing, platform-issued agent identity, built-in agent endpoints and protocols, Foundry tracing, and the evaluation tooling, and you rebuild them yourself. | When you need runtime features hosted agents do not offer (for example, a specific scaling model, a region, or a network topology) and you accept owning the agent platform. See [Production Hosted Agents on Foundry](../../../production-hosted-agents-on-foundry/README.md). |
| **Mixed by default** | Adds a second runtime and an internal contract to scenarios that need neither. Duplicate planning across layers causes inconsistent behavior. | When most scenarios in a portfolio combine a governed conversational front door with heavy code-based processing. |

## Follow-ups

- Apply the [production checklist](../architecture.md#19-production-checklist) to every hosted or mixed scenario.
- Use [Production Hosted Agents on Foundry](../../../production-hosted-agents-on-foundry/README.md) for hosted build and operations, [Code Execution Sandboxes with Session Pools](../../../code-execution-sandbox-session-pools/README.md) for untrusted code, and [Secured Foundry Landing Zone](../../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md) for network isolation.
