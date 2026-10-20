# Agent path-to-production delivery runbook

Use this runbook to coordinate a bounded improvement, not to replace an existing
delivery offering or the workload's operating procedures. The six lenses in the
[overview](../README.md) are a diagnostic map. Select work by customer problem and
evidence rather than starting a project for every lens.

## 1. Identify and frame

Record the following in the workload's approved private tracking system:

| Question | Evidence to capture |
|----------|---------------------|
| What user or business outcome is needed? | A specific task and how success will be measured. |
| What stage is the workload at? | Early design, prototype, pilot, or production; do not assume a committed project. |
| What prevents the next step? | A reproducible problem, missing decision, or readiness-evidence gap. |
| Does the requirement need an agent? | Comparison with a simpler application, retrieval flow, or deterministic workflow. |
| What may the system read or change? | Data classification, authorized users, tools, actions, and boundaries. |
| Who owns the outcome and the service? | Engagement coordinator, workload owner, and relevant specialists. |
| What constraints shape delivery? | Compliance, identity, platform, dependencies, and verified delivery access. |
| What decision should the next session make? | A bounded scope, explicit exit criteria, and the person accepting the result. |

Route quota-only requests to the capacity process, outages to operations, and
contract or entitlement questions to the appropriate authorized owner. Do not
infer delivery eligibility from a technical fit. When there are additional
technical gaps, retain them in this workstream and track the external dependency.

## 2. Triage and route

A clinic or asynchronous review should end with a primary gap, a next action,
an accountable owner, and required evidence. If evidence is insufficient, define
the smallest discovery task instead of declaring the workload ready. Each lens
links to its [pillar chapter](pillars/README.md), which adds entry questions,
an approach, and a readiness evidence table.

| Lens | Discovery questions | Typical contributors | Acceptance evidence |
|------|---------------------|----------------------|---------------------|
| [AI CoE & Governance](pillars/01-ai-coe-and-governance.md) | Who may approve use cases, access, exceptions, and release? What actions require human oversight? | Workload owner, governance, responsible AI, security. | Recorded boundaries, accountable decisions, and review/exception path. |
| [AI Landing Zones](pillars/02-ai-landing-zones.md) | Are identities, network paths, environments, deployment policies, and recovery dependencies understood? | Platform, infrastructure, identity, application engineering. | Reviewed environment design and evidence that required paths and controls work. |
| [Agent Ops](pillars/03-agent-ops.md) | Is there a representative test set? Can a model, prompt, tool, or data change be gated and rolled back? | Application/agent engineering, evaluation, operations. | Versioned evaluations, release checks, monitoring, and a rehearsed rollback. |
| [AI Security & Observability](pillars/04-ai-security-and-observability.md) | Can untrusted content induce unauthorized actions? Is each tool call authorized and attributable? | Security, identity, application engineering, operations. | Threat review, negative authorization tests, safe logs, and incident procedures. |
| [Solution Optimization](pillars/05-solution-optimization.md) | What are the measured quality, latency, reliability, and cost baselines? | Application engineering, operations, platform, optimization. | Like-for-like experiment results with quality and safety preserved. |
| [Proven Patterns for Agent Architectures](pillars/06-proven-patterns-for-agent-architectures.md) | Are agent boundaries and orchestration necessary? How are state, failures, tools, and human handoffs handled? | Application/agent engineering, architecture, integration, data. | Architecture decisions and an end-to-end success and failure demonstration. |

Application specialists lead application and agent implementation; platform
specialists lead platform concerns. Identity, data, and security contributors
participate wherever the customer outcome requires them. Organizational routing
must not leave a technical dependency without an owner.

The coordinator maintains the integrated plan. A specialist contributes a scoped
deliverable; the workload owner accepts operational ownership. Do not assign all
six lenses to one person or assume a contributor has accepted work.

### Select existing material without duplicating it

Prefer an existing delivery service, reference architecture, or asset that fits
the gap. Verify its scope, version, prerequisites, and required delivery skills.
Add supplemental guidance only for a demonstrated gap, and send corrections to
the source owner where possible.

Keep any organization-specific service mapping, commercial eligibility,
accreditation catalogue, and staffing directory in an approved private companion.
This public guide does not establish those offers or permissions.

## 3. Unblock with a bounded play

The three plays below are initial, generic starting points. They are not an
exhaustive catalogue and may be combined when evidence reveals dependencies.

### Play A: Choose an operable agent architecture

**Use when:** an early-stage team needs direction, or an existing agent cannot
reliably complete an end-to-end application task.

**Approach:** application/agent engineering coordinates with integration, data,
platform, and identity specialists as needed.

1. Define the user task, permitted actions, success measure, and non-goals.
2. Compare a deterministic flow or single agent with more complex orchestration.
   Record why additional agents or autonomy are necessary.
3. Draw data and control flows: user identity, model, state, retrieval, tool
   boundary, downstream authorization, and human handoff.
4. Specify tool contracts, timeouts, failure paths, state ownership, and controls
   for duplicate or partially completed actions.
5. Exercise one representative synthetic task and at least one failure path.
   Identify missing production evidence rather than treating the demo as release.

**Outputs:** architecture decision, flow diagram, representative test, and a
prioritized readiness backlog.

**Exit:** the team can explain and demonstrate the selected design and its
boundaries; unresolved gaps have owners. Production release still needs the gate
in section 4.

### Play B: Make quality and changes observable

**Use when:** a pilot appears successful but quality, operations, and safe change
management are not evidenced.

**Approach:** application/agent engineering works with evaluation and operations
specialists.

1. Build a versioned, authorized evaluation set with representative tasks,
   failure cases, and relevant misuse cases. Use synthetic data where possible.
2. Agree workload-specific thresholds for task success, harmful or unauthorized
   behavior, latency, reliability, and cost before testing.
3. Record the baseline with the model, prompt, tools, data, and configuration
   versions. Track evaluation limitations and sampled cases.
4. Add release gates for material changes. Test tool failures and degraded
   downstream dependencies, not only model responses.
5. Define telemetry for the task path and tool actions while minimizing sensitive
   logging. Specify actionable alerts, escalation, and feedback handling.
6. Rehearse rollback or a safe disablement path and confirm an operating owner.

**Outputs:** evaluation baseline, release-gate evidence, dashboards/alerts, and a
rollback and ownership record.

**Exit:** measured results meet the agreed criteria and the workload owner
accepts the remaining risks. An unmet or untested criterion is a gap, not a pass.

### Play C: Secure agent-to-tool and downstream integration

**Use when:** an agent needs to access enterprise services or act on behalf of a
user, but authority, data exposure, or auditability is unclear.

**Approach:** application/agent engineering works with identity, security, data,
and platform specialists.

1. Enumerate tools, authorized callers, allowed operations, affected data, and
   the impact of a wrong or repeated action.
2. Distinguish application identity from delegated user authority. Use supported
   identity mechanisms and explicit permission checks for the chosen service.
3. Constrain tool capabilities and credentials. Enforce authorization downstream
   instead of assuming a model's proposed action is authorized.
4. Treat retrieved content and tool responses as untrusted. Test attempts to
   override instructions, exfiltrate data, or invoke unauthorized tools.
5. Require human approval where impact warrants it, and bind approval to the
   actual action rather than an open-ended session.
6. Capture sufficient audit evidence without logging secrets or unnecessary
   sensitive content. Test permission denial, expired authority, and partial
   failure.

**Outputs:** trust-boundary diagram, threat review, tool/identity contracts,
negative tests, and incident/disablement procedures.

**Exit:** required controls are implemented and tested, exceptions have explicit
acceptance, and unsafe actions can be stopped. Product-specific implementation
requires current documentation and the relevant specialists.

## 4. Release and operate

Use the [readiness record](readiness-record.md) to capture evidence. Criteria are
workload-specific: there is no universal pass threshold or default release
approval in this guide.

| Gate | Minimum decision evidence |
|------|---------------------------|
| Outcome and scope | Agreed user outcome, permitted use, and accountable workload owner. |
| Architecture and platform | Reviewed dependencies, environment boundaries, identities, and deployment prerequisites. |
| Quality and safety | Representative evaluation results against agreed thresholds, including relevant failure and misuse cases. |
| Authorization and data | Tested permission boundaries, approved data handling, and tool/action safeguards. |
| Change and recovery | Versioned release artefacts, test results, rollback or safe disablement, and recovery procedures. |
| Operations | Monitoring and alerts, support and escalation, incident responsibilities, and feedback intake. |
| Residual risk | Explicit acceptance by the appropriate owner or a no-go decision with next actions. |

Mark each gate **met**, **gap**, **not assessed**, or **not applicable** with a
reason and owner. An absent test, unavailable specialist, or open dependency is
not readiness evidence. The accountable owner makes the release decision under
the workload's governance process.

After release, compare observed behavior with the evaluation baseline. Route
incidents through operations; feed recurring quality and delivery gaps into the
next iteration. Track readiness outcomes and lessons without presenting training
completion as proof of a production outcome.

## 5. Harvest reusable learning

Keep the engagement record, customer scenario, datasets, logs, commercial details,
and organization-specific links in private systems. Extract a generic problem,
approach, trade-offs, and synthetic example that stands independently.

For a repository contribution:

1. Search for an existing asset before creating another one.
2. Remove identifying customer details, personal data, internal links, secrets,
   and unreleased information. Check diagrams and screenshots as well as text.
3. Add evidence that supports the generic claim, not a private engagement story.
   Do not claim field validation unless it is cleared and evidenced.
4. Follow the manifest, changelog, validation, and curator-review process in
   [CONTRIBUTING.md](../../../../CONTRIBUTING.md).
5. Link related assets by their stable ids; use version or commit permalinks when
   prescribing a specific implementation.

Curator review remains required even when validation succeeds. This workflow
does not authorize publication of customer information.
