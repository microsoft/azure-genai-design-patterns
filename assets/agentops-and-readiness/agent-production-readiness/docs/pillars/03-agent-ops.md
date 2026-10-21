# Agent Ops

Pillar 03 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when quality is anecdotal, or when changes
to models, instructions, tools, or data cannot be evaluated, released, and
rolled back safely.

## Pillar definition

Agent Ops (also written AgentOps) applies the discipline of DevOps and machine
learning operations to agentic systems. An agent's behavior depends on several
parts that change independently: the model and its version, instructions, tools
and their contracts, retrieval data, and configuration. This pillar makes each
change observable, testable, and reversible.

Sub-topics:

- **Continuous integration and delivery.** Pipelines that build, evaluate, and
  deploy the agent and its dependencies through separate environments with
  approvals.
- **Versioning.** Version control for instructions, tool definitions, evaluation
  sets, and configuration, with pinned model versions so that a result can be
  reproduced.
- **Tracing.** Distributed traces that follow a request through agent steps,
  model calls, tool calls, and downstream services, using open standards such as
  OpenTelemetry.
- **Evaluation gates.** Offline evaluation against a representative set before
  release, and online evaluation or sampling of production traffic after
  release.
- **Monitoring and alerting.** Dashboards and actionable alerts for task
  success, safety, latency, errors, and cost, with an owner for each alert.
- **Incident runbooks.** Procedures for triage, mitigation, rollback, safe
  disablement, and post-incident learning.

## Common signals

These signals are generalized from real-world implementations; they describe
recurring situations, not statistics.

- Teams commonly have traces but no route from them into a test set. The
  sequence that works is to define what "task complete" means first, sample and
  label traces against that definition, turn them into a versioned dataset, and
  run that dataset as a gate in continuous integration.
- Teams commonly standardize on OpenTelemetry and send agent, model, and tool
  spans to Application Insights or a Log Analytics workspace, so application and
  agent telemetry share one query surface.
- Teams commonly need a versioned deployment pipeline with environments and
  approvals before they can release more than once safely. Manual deployment
  from a developer workstation is a frequent hidden blocker.
- Teams that depend on preview features commonly carry that risk implicitly.
  Recording each preview dependency, its fallback, and who tracks its status
  turns a surprise into a managed risk.
- Teams commonly lose correlation when requests cross gateways, queues, or tool
  servers. Propagating one correlation ID end to end is what makes a multi-hop
  failure diagnosable.

## Entry questions

1. What does "task complete" mean for this workload, and who agreed the
   definition?
2. Is there a versioned, access-controlled evaluation set with representative
   tasks, failure cases, and misuse cases?
3. Which thresholds for task success, unsafe behavior, latency, reliability, and
   cost were agreed before testing?
4. Are the model version, instructions, tools, data, and configuration versioned
   together so that a result can be reproduced?
5. Does a pipeline deploy through separate environments with an evaluation gate
   and an approval?
6. Can one request be traced end to end across agent steps, model calls, tool
   calls, gateways, and downstream services?
7. Which alerts exist, what action does each one require, and who receives them?
8. How is a bad change rolled back or the agent safely disabled, and has that
   been rehearsed?
9. Which preview features does the workload depend on, and what is the fallback
   for each?

## Approach and play

Agent Ops is the primary owner of [Play B: Make quality and changes
observable](../readiness-review.md#play-b-make-quality-and-changes-observable). The steps
below expand Play B and add the feedback loop that keeps the evaluation set
current.

1. **Define success and build the evaluation set** (Play B step 1). Agree what
   task completion means, then assemble representative tasks, failure cases, and
   misuse cases. Prefer synthetic data; where production traces are used, follow
   the approved data-handling process.
2. **Agree thresholds before testing** (Play B step 2). Record workload-specific
   thresholds for task success, unsafe or unauthorized behavior, latency,
   reliability, and cost.
3. **Record a reproducible baseline** (Play B step 3). Run the evaluation and
   record the model, instructions, tools, data, and configuration versions
   alongside the results and their limitations.
4. **Gate material changes** (Play B step 4). Add the evaluation to the release
   pipeline so that a change to any versioned part triggers it. Include tool
   failures and degraded dependencies, not only model responses.
5. **Instrument the task path** (Play B step 5). Emit OpenTelemetry spans for
   agent steps, model calls, and tool calls with a propagated correlation ID.
   Minimize sensitive content in telemetry. Define alerts with owners and an
   escalation route.
6. **Close the loop.** Sample production traces and user feedback, label them
   against the success definition, and add them to the evaluation set under the
   same access controls.
7. **Rehearse recovery** (Play B step 6). Practice rollback and safe
   disablement, confirm the operating owner, and record the result in the
   [readiness record](../readiness-record.md#evidence-and-acceptance).

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Evaluation set | Versioned and access-controlled; covers representative tasks, failure cases, and relevant misuse cases. | Required (Quality and safety) |
| Baseline results | Results against thresholds agreed before testing, with the version of every component and known limitations. | Required (Quality and safety) |
| Release gate | A pipeline runs the evaluation on material changes and blocks release when agreed thresholds are not met. | Required (Change and recovery) |
| Rollback or disablement | A rehearsed procedure restores the previous version or safely disables the agent. | Required (Change and recovery) |
| End-to-end tracing | One correlation ID links agent steps, model calls, tool calls, and downstream services. | Required (Operations) |
| Alerts and escalation | Actionable alerts for quality, safety, errors, latency, and cost, each with an owner and escalation route. | Required (Operations) |
| Feedback loop | Production samples and user feedback are labeled and added to the evaluation set on an agreed cadence. | Recommended (Quality and safety) |
| Preview dependency record | Each preview dependency has a documented fallback and an owner who tracks its status. | Recommended (Residual risk) |

## Common pitfalls

- **Evaluating only the happy path.** An evaluation set without failure and
  misuse cases overstates readiness.
- **Thresholds chosen after seeing results.** Agree thresholds first, or the
  gate measures nothing.
- **Unpinned model versions.** An automatic model update changes behavior
  without passing the gate.
- **Instructions outside version control.** Prompts edited directly in a portal
  cannot be reviewed, gated, or rolled back.
- **Logging everything.** Full prompts and responses in telemetry create a new
  sensitive-data store. Log what diagnosis needs, and apply access and retention
  controls.
- **Alerts without owners.** An alert nobody acts on trains the team to ignore
  alerts.
- **Untested rollback.** A rollback that has never been rehearsed is a plan, not
  evidence.

## Collaboration boundaries

- **[AI CoE & Governance](01-ai-coe-and-governance.md):** agrees the graduation
  criteria and thresholds that the release gate enforces, and receives the
  resulting evidence.
- **[AI Landing Zones](02-ai-landing-zones.md):** provides environments,
  pipeline identities, and the monitoring workspace.
- **[AI Security & Observability](04-ai-security-and-observability.md):**
  contributes misuse and red-team cases to the evaluation set and defines which
  audit fields telemetry must capture and which content must not be logged.
- **[Solution Optimization](05-solution-optimization.md):** uses the baseline as
  the control for every experiment and ships changes through the same gate.
- **[Proven Patterns for Agent
  Architectures](06-proven-patterns-for-agent-architectures.md):** designs for
  testability with deterministic tool contracts, explicit state, and seams where
  evaluation can observe each step.
- **Roles:** application and agent engineering, evaluation, operations or site
  reliability engineering, and the operating owner, who accepts the alerts and
  procedures.

## References

- [MLOps and GenAIOps for AI workloads on Azure](https://learn.microsoft.com/en-us/azure/well-architected/ai/mlops-genaiops)
- [Test and evaluate AI workloads on Azure](https://learn.microsoft.com/en-us/azure/well-architected/ai/test)
- [AI workload operations on Azure](https://learn.microsoft.com/en-us/azure/well-architected/ai/operations)
- [Observability in generative AI](https://learn.microsoft.com/en-us/azure/foundry/concepts/observability)
- [Generative AI operations for organizations with MLOps investments](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/genaiops-for-mlops)
