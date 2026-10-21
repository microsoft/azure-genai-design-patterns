# 0002: Govern code execution through a fifth gate, the execution boundary

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

[ADR 0001](0001-four-gate-tool-governance.md) governs which tools an agent may
call, with which identity, with whose approval and against which entitlements.
It assumes each tool has a fixed, reviewed implementation.

Analytical and coding agents break that assumption. The model writes Python,
SQL or shell at run time, users upload notebooks, and tenants publish skills
and container images that the agent loads. The code that runs is chosen by the
model, so anyone who can steer the model (through a document, a tool result or
a web page) can choose code that the platform then executes. A single prompt
injection becomes arbitrary code execution.

Field workshops showed three recurring failures:

- A component called a sandbox ran generated code inside the agent's own
  process. A probe found secret-like environment variables, an advertised
  managed identity endpoint, a reachable metadata address and open internet
  access. Its hostname contained the word "sandbox".
- Skills, prompts and runtime images reached production through a different
  path from application code, without review, evaluation or a rollback plan.
- A model reported results from an execution that had not happened.

## Decision

Code execution is governed by a fifth gate, the **execution boundary**, which
assumes Gates 1 to 4 have already been bypassed and hostile code is running:

1. **Out of process only.** Generated, uploaded or tenant-published code never
   runs in the agent's process. It runs in a hypervisor-isolated session pool
   or a runtime with equivalent, demonstrated isolation.
2. **The sandbox starts with nothing.** No managed identity, no secrets, no
   credentials in the environment, no outbound network and no persistent
   storage beyond the session. Each added capability needs a written reason.
3. **A broker holds the identity.** The broker, not the model or the client,
   derives the session key from tenant, user and conversation. It pre-screens
   code, enforces budgets, guards output and writes one audit record per
   operation.
4. **Pinned runtimes.** Custom images are referenced by digest, built from a
   lock file and scanned. The digest and lock hash are recorded on every
   execution.
5. **Proposals, not side effects.** Code returns files and values. Changes to
   a system of record go through a governed tool, Gate 3 approval and Gate 4
   entitlements.
6. **Read back and probe.** After every deployment, the pipeline reads back
   pool, agent and skill settings and runs the isolation probe through the
   agent's own execution path. A difference from the intended state fails the
   release.
7. **Skills, prompts, images and models are production changes.** They follow
   the same review, evaluation, staged rollout and rollback as application
   code.
8. **Prove that code ran.** An answer is treated as computed only when a
   matching execution record exists.

## Alternatives considered

| Option | Why rejected |
|--------|--------------|
| **In-process execution with a restricted interpreter or denied imports** | Language-level restrictions are routinely escaped, and the code still shares the agent's identity, secrets, network and memory. A failure exposes everything. |
| **Pre-screening code with patterns or a second model, then running it in process** | Screening is a filter, not a boundary. It is easy to evade, and the cost of a miss is the whole agent. Kept as noise reduction in front of isolation. |
| **A container with the agent's managed identity, for convenience** | Turns any injected script into a credential for every resource the agent can reach. Data the code needs is uploaded by the broker instead. |
| **Egress open with logging** | Logging records exfiltration after it happens. Egress stays off unless a written requirement adds an allow-listed destination. |
| **Trusting configuration and service names** | Provisioning can succeed while a setting is ignored, and names prove nothing. Only read-back and a probe inside the boundary show the real state. |

## Consequences

**Positive**

- A manipulated model can, at worst, run code in a session that holds only the
  data uploaded for that conversation and cannot send it anywhere.
- Every execution is attributable to a tenant, user, session, code hash and
  runtime digest, which supports incident response and release evidence.
- Runtime drift and capability changes are caught by read-back, the probe and
  KQL query 10 rather than by users.

**Negative**

- A broker, session pools and their capacity must be built, monitored and
  paid for; cold starts and pool saturation affect latency.
- Workloads that genuinely need outbound access or custom packages need an
  exception process, an allow-list and a custom image pipeline.
- Several session-pool capabilities are in preview, so read-back and the probe
  must be repeated as the platform changes.
- Proposals that need approval add a human step to some workflows.

## References

- [Security and governance for agents that execute code](../code-execution-governance.md)
- [Threat model and control matrix](../threat-model-and-control-matrix.md)
- [Isolation probe](../../samples/execution_isolation_probe.py)
- [Code Execution Sandboxes: ADR 0001, never execute generated code in process](../../../../agent-runtime/code-execution-sandbox-session-pools/docs/decisions/0001-never-execute-generated-code-in-process.md)
