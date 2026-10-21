# Proven Patterns for Agent Architectures

Pillar 06 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when the orchestration, state, tool
contracts, or hosting model of an agent are unclear, or when a prototype's
design will not survive production load and failure.

## Pillar definition

This pillar covers choosing and shaping the agent's architecture so that it is
the simplest design that meets the task and can be operated, secured, and
changed. It favors established patterns over novel ones and treats every added
agent, tool, or hop as a cost that must be justified.

Sub-topics:

- **Orchestration.** Choosing among a deterministic workflow, a single agent
  with tools, and multi-agent patterns such as sequential, concurrent, handoff,
  or planner with specialists, and defining how control returns when a step
  cannot proceed.
- **Quality controls.** Grounding, validation of tool inputs and outputs,
  routing and fallback for out-of-scope requests, and seams where evaluation can
  observe each step.
- **Interoperability.** Standard tool and agent protocols such as the Model
  Context Protocol (MCP) and agent-to-agent communication, tool and agent
  registries, and governed access to shared tools.
- **State and memory.** Externalized state with defined scopes, such as turn,
  session, task, and long-term memory, each with ownership, size limits, and
  expiry.
- **Front doors and channels.** Reaching users in the channels they already use,
  while keeping the agent logic in a service that can be tested and operated
  independently of the channel.
- **Scale and hosting.** Managed agent services versus custom runtimes,
  concurrency and long-running tasks, and isolated execution for generated code.

## Common signals

These signals are generalized from real-world implementations; they describe
recurring situations, not statistics.

- Teams commonly find that the move from prototype to production fails first on
  state. In-memory conversation state works in a demo and breaks under
  scale-out, restarts, and long-running tasks. Externalizing state and
  separating it by scope (turn, session, task, and long-term) resolves most of
  these failures.
- Teams commonly settle on a planner with a small number of domain specialists,
  plus an explicit path for requests that no specialist should handle. Without
  that return path, specialists guess.
- Teams commonly place the front door where users already work, such as a
  collaboration suite or an existing application, and back it with an agent
  service in the cloud, rather than rebuilding agent logic inside each channel.
- Teams commonly expose shared tools through a governed gateway, for example MCP
  servers published behind an API gateway and listed in a registry, so that
  authentication, quotas, and discovery are handled once.
- Teams commonly version skills and tools independently of the agent, so that a
  tool change can be tested and rolled out without redeploying every agent that
  uses it.

## Entry questions

1. What task does the agent perform, which actions may it take, and what are its
   non-goals?
2. Could a deterministic workflow or a single agent with tools meet the task,
   and if not, what specifically requires orchestration?
3. Which agents, tools, and data stores take part, and how do data and control
   flow between them?
4. What happens when a request is out of scope, a tool fails, or a step returns
   partial results?
5. Does each tool have a contract that defines inputs, outputs, errors,
   timeouts, and idempotency?
6. Where does state live, what is its scope, and how is it sized, expired, and
   recovered after a restart?
7. Which channels do users reach the agent through, and is the agent logic
   independent of those channels?
8. Will the agent run in a low-code builder, a managed agent service, or a
   custom runtime, and why?
9. Does the agent generate or run code, and if so, where is that code isolated?
10. How are tools and agents discovered, versioned, and governed when more than
    one team shares them?

## Approach and play

Architecture is the primary owner of [Play A: Choose an operable agent
architecture](../readiness-review.md#play-a-choose-an-operable-agent-architecture). The
steps below expand Play A with a build-option decision and a handover to
hardening.

1. **Frame the task** (Play A step 1). Record the task, allowed actions, success
   criteria, and non-goals.
2. **Justify the complexity** (Play A step 2). Compare a deterministic workflow
   or a single agent with tools against the proposed orchestration. Record the
   decision and the alternatives in an architecture decision record.
3. **Choose the build option.** Use a low-code builder when the task fits its
   connectors and controls. Default to a managed agent service, such as
   Microsoft Foundry Agent Service, for hosted agents with built-in tools,
   identity, and tracing. Choose a custom runtime only when a specific
   requirement rules out the managed options. Run any generated code in an
   isolated sandbox.
4. **Draw the flows** (Play A step 3). Show data and control flows, trust
   boundaries, the channel front door, the tool gateway, and where state is
   stored.
5. **Define contracts and state** (Play A step 4). Specify each tool's inputs,
   outputs, errors, timeouts, retries, and idempotency. Define state scopes,
   ownership, limits, and expiry, and how duplicate or partial actions are
   handled.
6. **Demonstrate success and failure** (Play A step 5). Run one synthetic task
   end to end and at least one failure path, such as a tool timeout or an
   out-of-scope request.
7. **Hand over for hardening.** Pass the design, contracts, and demonstration to
   [Agent Ops](03-agent-ops.md) for evaluation and release gating, and to [AI
   Security & Observability](04-ai-security-and-observability.md) for the tool
   authorization review. Attach the evidence to the [readiness
   record](../readiness-record.md#evidence-and-acceptance).

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Architecture decision | A recorded decision compares simpler options with the chosen orchestration and build option. | Required (Architecture and platform) |
| Data and control flow | A reviewed diagram shows agents, tools, data stores, channels, trust boundaries, and state. | Required (Architecture and platform) |
| Tool contracts | Each tool defines inputs, outputs, errors, timeouts, retries, and idempotency. | Required (Authorization and data) |
| State design | State is externalized with defined scopes, ownership, size limits, expiry, and restart behavior. | Required (Architecture and platform) |
| End-to-end demonstration | One synthetic task succeeds end to end, and at least one failure path behaves as designed. | Required (Quality and safety) |
| Execution isolation | Where the agent generates or runs code, execution is sandboxed with no ambient access to production resources. | Required (Authorization and data) |
| Routing and fallback | Out-of-scope and low-confidence requests follow a defined path instead of being answered by a guessing specialist. | Recommended (Quality and safety) |
| Tool and agent registry | Shared tools and agents are discoverable, versioned, and owned. | Recommended (Operations) |

## Common pitfalls

- **Multi-agent by default.** Every added agent adds latency, cost, and failure
  modes. Start with the simplest design that meets the task.
- **In-memory state.** State held in process memory is lost on restart and
  diverges across instances.
- **Specialists that guess.** Without a return path for out-of-scope requests, a
  specialist answers questions it was never designed for.
- **Tools without contracts.** Unspecified errors, timeouts, and retries turn
  transient failures into duplicate or partial actions.
- **Rebuilding logic in each channel.** Agent logic embedded in a channel cannot
  be tested, versioned, or reused independently.
- **Unsandboxed code execution.** Generated code running with the agent's
  identity and network access is an unbounded tool.
- **Treating the demonstration as the release.** A successful demonstration
  proves the design is feasible; evaluation, security review, and operations
  still decide readiness.

## Collaboration boundaries

- **[AI CoE & Governance](01-ai-coe-and-governance.md):** confirms the use case,
  action boundary, and risk classification that constrain the design, and
  maintains shared standards such as approved build options.
- **[AI Landing Zones](02-ai-landing-zones.md):** provides the hosting,
  networking, and identity foundation for the chosen runtime and channels.
- **[Agent Ops](03-agent-ops.md):** turns the demonstration into an evaluation
  set and release gate, and relies on the observable seams designed here.
- **[AI Security & Observability](04-ai-security-and-observability.md):**
  reviews tool contracts, identity patterns, approval steps, and execution
  isolation.
- **[Solution Optimization](05-solution-optimization.md):** measures the design
  under load and proposes structural changes, such as pre-computed paths or
  business tools.
- **Roles:** solution architecture, application and agent engineering, the tool
  or system-of-record owners, the channel owner, and the product or workload
  owner, who confirms the task and non-goals.

## References

- [Microsoft 365 Integration with Azure Custom Engine Agent](../../../../m365-and-copilot/m365-azure-custom-agent/README.md)
- [Microsoft 365 Copilot Integration with MCP via Plug-ins](../../../../m365-and-copilot/m365-mcp-plugin-integration/README.md)
- [AI agent orchestration patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)

- [Choosing between building a single-agent system or multi-agent system](https://learn.microsoft.com/en-us/azure/cloud-adoption-framework/ai-agents/single-agent-multiple-agents)
- [Application design for AI workloads on Azure](https://learn.microsoft.com/en-us/azure/well-architected/ai/application-design)
- [What is Microsoft Foundry Agent Service?](https://learn.microsoft.com/en-us/azure/foundry/agents/overview)
- [Connect agents to MCP server endpoints](https://learn.microsoft.com/en-us/azure/foundry/agents/how-to/tools/model-context-protocol)
