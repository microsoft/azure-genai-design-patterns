# Pillar chapters

These chapters expand the six technical lenses that the
[runbook](../runbook.md#2-triage-and-route) uses to triage an agentic workload.
Each pillar describes one area where a working demonstration commonly falls
short of an operable production workload, the questions that reveal the gap, and
the evidence that closes it.

The pillars are a diagnostic map, not six mandatory projects, a maturity score,
or a certification. Find the pillar that holds the principal blocker, run the
smallest play that removes it, and involve other pillars only where the evidence
shows a dependency.

## Chapters

| # | Pillar | Focus | Primary play | Main readiness gates |
|---|--------|-------|--------------|----------------------|
| 01 | [AI CoE & Governance](01-ai-coe-and-governance.md) | Operating model, intake and graduation, responsible AI policy, agent inventory, cost accountability, and skills. | Framing in [section 1](../runbook.md#1-identify-and-frame) and the decision in [section 4](../runbook.md#4-release-and-operate) | Outcome and scope; residual risk |
| 02 | [AI Landing Zones](02-ai-landing-zones.md) | Network topology, private connectivity, identity and RBAC, policy, environments, and regional design. | Supports [Play A](../runbook.md#play-a-choose-an-operable-agent-architecture) and [Play C](../runbook.md#play-c-secure-agent-to-tool-and-downstream-integration) | Architecture and platform |
| 03 | [Agent Ops](03-agent-ops.md) | CI/CD, versioning, tracing, evaluation gates, monitoring, and incident runbooks. | [Play B](../runbook.md#play-b-make-quality-and-changes-observable) | Quality and safety; change and recovery; operations |
| 04 | [AI Security & Observability](04-ai-security-and-observability.md) | Agent and tool authorization, prompt-injection defenses, sensitive data, red teaming, and audit trails. | [Play C](../runbook.md#play-c-secure-agent-to-tool-and-downstream-integration) | Authorization and data; quality and safety |
| 05 | [Solution Optimization](05-solution-optimization.md) | Performance, latency, cost, retrieval quality, semantic layers, and scaling. | A bounded experiment on the [Play B](../runbook.md#play-b-make-quality-and-changes-observable) baseline | Quality and safety; operations |
| 06 | [Proven Patterns for Agent Architectures](06-proven-patterns-for-agent-architectures.md) | Orchestration, quality controls, interoperability, state, front doors, and scale. | [Play A](../runbook.md#play-a-choose-an-operable-agent-architecture) | Architecture and platform |

## How each chapter is organized

Every chapter uses the same sections, so specialists can compare them and a
coordinator can route work consistently:

1. **Pillar definition.** What the pillar covers and its sub-topics.
2. **Field signals.** Generalized observations of where teams commonly get
   stuck.
3. **Entry questions.** Questions an architect asks to triage the pillar.
4. **Approach and play.** Ordered steps, linked to the runbook plays.
5. **Readiness evidence and gate.** Evidence to record in the readiness record.
6. **Common pitfalls.** Mistakes that turn into release blockers.
7. **Collaboration boundaries.** Which other pillars and roles to involve, and
   what to hand over.
8. **References.** Public Microsoft documentation for further detail.

## Reading the gate column

Each evidence table names a gate from the [readiness
record](../readiness-record.md#evidence-and-acceptance) in parentheses, and
marks the item as one of the following:

- **Required.** Do not make a production release decision without this evidence,
  or without a recorded not-applicable reason and owner.
- **Recommended.** Strengthens confidence or reduces operating risk. If it is
  deferred, record it as a gap with an owner.

Thresholds remain workload-specific; the chapters describe what good evidence
looks like, not a universal pass mark. Record each result as met, gap, not
assessed, or not applicable. An absent test, an unavailable specialist, or an
open dependency is not readiness evidence.

## Recurring scenario shapes

Delivery reviews repeatedly surface a small number of scenario shapes. Use them
to anticipate which pillars a workload will exercise, not as references or proof
that an approach works.

| Scenario shape | Pillars most exercised |
|----------------|------------------------|
| A multi-agent assistant embedded in a collaboration suite for a large internal user population | 04, 05, 06 |
| Consolidating one-off agents onto a shared platform with team-owned agents | 01, 02, 03 |
| Bringing a custom agent harness onto a managed runtime with sandboxed code execution and an evaluation loop | 03, 05, 06 |
| A collaboration-suite front door backed by a cloud agent engine, delivered across productivity, cloud, and security teams | 01, 02, 04 |
| A software vendor exposing its application portfolio to AI clients through a governed tool gateway | 02, 04, 06 |

## About the field signals

Field signals are generalized and anonymized observations from delivery reviews.
They are not statistics, product guidance, customer references, or field
validation of this playbook. Product capabilities change; check current
documentation before implementing a product-specific control.
