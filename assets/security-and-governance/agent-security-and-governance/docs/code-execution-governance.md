# Security and governance for agents that execute code

> Pattern: treat every piece of code an agent writes, receives or loads as
> untrusted input. Run it behind a fifth gate, the **execution boundary**. The
> boundary starts with no identity, no secrets and no network. It returns data
> rather than side effects, and it leaves evidence you can audit.

The [four-gate model](architecture.md) governs which tools an agent may *call*.
Once an agent can *write and run code*, the model and anyone who can steer it
reach a general-purpose runtime. A single prompt injection then becomes
arbitrary code execution (OWASP Agentic ASI05, unexpected code execution).
This guide adds the controls, tests and evidence that apply from that point on.

This guide is the governance layer. The runtime mechanics live in the
[Code Execution Sandboxes with Session Pools](../../../agent-runtime/code-execution-sandbox-session-pools/README.md)
asset: pool types, session identifiers, egress settings, the session broker
and its offline simulator. This guide links to that asset rather than repeating
it.

The guidance comes from field workshops in which teams built analytical agents
that generate and run Python. It also draws on an isolation probe that ran the
same script in-process and inside a sandbox. Behaviour seen only in preview
services is marked **(observed in preview; verify against current Microsoft
Learn documentation)**.

## Contents

1. [Scope](#1-scope)
2. [Ten principles](#2-ten-principles)
3. [Who authors the code](#3-who-authors-the-code)
4. [Execution tiers](#4-execution-tiers)
5. [Gate 5: the execution boundary](#5-gate-5-the-execution-boundary)
6. [Threat catalog](#6-threat-catalog)
7. [Isolation and identity](#7-isolation-and-identity)
8. [Egress and data movement](#8-egress-and-data-movement)
9. [Runtime images and packages](#9-runtime-images-and-packages)
10. [Sessions, budgets and capacity](#10-sessions-budgets-and-capacity)
11. [Side effects and approval](#11-side-effects-and-approval)
12. [Change control for skills, prompts, models and deployments](#12-change-control-for-skills-prompts-models-and-deployments)
13. [Proving that code ran](#13-proving-that-code-ran)
14. [Isolation verification probe](#14-isolation-verification-probe)
15. [Audit and evidence](#15-audit-and-evidence)
16. [Policy as code and read-back](#16-policy-as-code-and-read-back)
17. [Red-team cases](#17-red-team-cases)
18. [Production checklist](#18-production-checklist)
19. [Residual risks](#19-residual-risks)
20. [Preview caveats](#20-preview-caveats)
21. [Review cadence](#21-review-cadence)
22. [References](#22-references)

## 1. Scope

This guide applies when any of the following is true:

- The model writes code, such as Python, SQL, shell or JavaScript, and
  something runs it.
- Users upload code, notebooks or files that are executed or parsed by
  executable tooling.
- Tenants, customers or internal teams publish skills, scripts or container
  images that an agent loads.
- A coding agent writes to a repository, a pipeline or infrastructure as code.

It does not cover fixed, platform-authored tools with typed parameters. Those
stay under Gates 1 to 4.

| Concern | Where it lives |
| --- | --- |
| Allow-listing tools, connection identity, approval, source-side entitlements | [Architecture](architecture.md), Gates 1 to 4 |
| Session pools, session identifiers, egress settings, broker code | [Code Execution Sandboxes: architecture](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md) |
| Roles, no-secrets rule, tenant isolation, approval and policy for executed code | [Code Execution Sandboxes: authentication and governance](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md) |
| Sandbox release gates and the RT-01 to RT-24 red-team catalogue | [Code Execution Sandboxes: evaluation](../../../agent-runtime/code-execution-sandbox-session-pools/docs/evaluation-and-troubleshooting.md) |
| Hosted-agent identity, in-network deployment and release | [Production Hosted Agents on Microsoft Foundry](../../../agent-runtime/production-hosted-agents-on-foundry/README.md) |
| Checking generated artifacts before a human approves them | [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md) |
| Step, time and cost budgets with escalation | [Bounded Autonomy](../../../orchestration-and-workflows/bounded-autonomy/README.md) |
| Tamper-evident run records | [Evidence Ledger and Run Certificate](../../../orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md) |
| **Governance of executed code: principles, Gate 5, threats, change control, evidence** | **This guide** |

## 2. Ten principles

1. **Generated code is untrusted input.** Treat model-written code exactly like
   code pasted by an anonymous user. The model can be steered by any document,
   tool result or web page it has read.
2. **Never run generated code in the agent's process.** The agent process holds
   the agent's identity, its secrets, its network path and its memory. Code that
   runs there inherits all of them.
3. **Naming is not isolation.** A host called `sandbox`, a container labelled
   "isolated" or a function called `run_safely` proves nothing. Only a probe run
   inside the boundary proves what the code can reach.
4. **The sandbox starts with nothing.** No managed identity, no secrets, no
   environment credentials, no outbound network and no persistent storage
   beyond the session. Add each capability deliberately, with a reason that is
   written down.
5. **Data in, results out, side effects through approval.** The broker uploads
   the minimum input. The sandbox returns files and values. Anything that
   changes a system of record goes through a governed tool and Gate 3, not
   through code.
6. **Who authors the code decides the controls.** Model-generated, user-supplied,
   tenant-published and coding-agent code have different trust levels and
   different blocking controls.
7. **Runtimes, images, skills and prompts are governed artifacts.** Pin them,
   review them, evaluate them and roll them back like application code. A
   published skill or an updated image is a production change.
8. **Read back after every deployment.** Provisioning can report success while a
   setting was ignored. Compare the deployed state with the intended state and
   fail the release when they differ.
9. **Prove that the tool ran.** A model can describe an execution that never
   happened. Treat an answer as computed only when a matching execution record
   exists.
10. **Evidence over assertion.** Every execution leaves a record: who, which
    session, which code hash, which runtime digest, what was blocked and what
    came out. Release gates and incident response use these records, not
    descriptions.

## 3. Who authors the code

The author of the code sets its trust level. Choose blocking controls by
author, not by language.

| Author | Examples | Trust | Blocking controls before production |
| --- | --- | --- | --- |
| Model, at run time | Analysis scripts, data transformations, chart code, generated SQL | Untrusted; steerable by injected content | Out-of-process sandbox, no identity, egress off, pre-screen, budgets, output guard, execution evidence |
| End user | Uploaded notebooks, scripts, macros, formulas | Untrusted; may be deliberately hostile | All of the above, plus per-user session isolation and upload size and type limits |
| Tenant or customer | Published skills, custom container images, plug-in scripts | Semi-trusted; supply-chain risk | Signed or digest-pinned artifacts, review and evaluation before promotion, allow-listed names, per-tenant pools where required |
| Coding agent | Pull requests, pipeline definitions, infrastructure as code | Untrusted until reviewed | Branch protection, required human review, deterministic checks, no direct write to protected branches or production |
| Platform team | Deterministic tools with typed parameters | Trusted after normal review | Gates 1 to 4; code execution is not needed |

When teams rank these risks before a production review, the following are
usually **blocking**:

- Generated code runs in the agent process.
- The sandbox holds an identity, a secret or an open outbound path that the
  design does not require.
- Sessions are shared across users or tenants.
- Skills, prompts or images can reach production without review and
  evaluation.
- Code can change a system of record without approval.

The following are usually **important but not blocking** for a first
production release, if they have an owner and a date:

- Automated software bill of materials (SBOM) generation and vulnerability
  scanning for runtime images.
- Automated read-back of every configuration setting.
- Fabricated-result detection beyond the execution-record check.
- Capacity alerting on session-pool saturation.

## 4. Execution tiers

Pick the lowest tier that meets the requirement. Each step up adds capability
and adds controls you must own.

```mermaid
flowchart TD
    Q0{Does the task need<br/>code at all?} -->|No| T0[Tier 0<br/>Typed tools only]
    Q0 -->|Yes| Q1{Is a platform-managed<br/>code interpreter enough?}
    Q1 -->|Yes| T1[Tier 1<br/>Platform code interpreter]
    Q1 -->|No: you need your own<br/>sessions, audit or tenant pools| Q2{Standard packages<br/>enough?}
    Q2 -->|Yes| T2[Tier 2<br/>Built-in session pool<br/>behind a broker]
    Q2 -->|No: custom packages<br/>or binaries| T3[Tier 3<br/>Custom-container session pool]
    T3 -.->|Kernel, GPU or regulatory<br/>isolation needs| T4[Tier 4<br/>Self-hosted runtime with<br/>VM-level isolation]
    NEVER[In the agent process]:::never
    classDef never fill:#fde7e9,stroke:#a4262c,color:#a4262c
```

| Tier | Runtime | You own | Typical use | Main risk you take on |
| --- | --- | --- | --- | --- |
| 0 | No execution; typed tools only | Gates 1 to 4 | Lookups, fixed calculations, CRUD with approval | None from code execution |
| 1 | Platform-managed code interpreter attached to the agent | Input and output handling, audit correlation | Ad hoc analysis, charts, file conversion | Less control over sessions, egress and evidence |
| 2 | Built-in session pool, called by your broker | Broker, session keys, budgets, audit, output guard | Per-user analysis with your own audit and tenant separation | Broker bugs; capacity |
| 3 | Custom-container session pool | All of Tier 2, plus the image, packages, egress and identity settings | Domain libraries, compiled binaries, offline models | Supply chain, drift, configuration silently ignored |
| 4 | Self-hosted runtime with VM-level isolation | The whole stack, including patching and isolation | Special hardware, regulatory placement | Isolation engineering you must prove yourself |
| Never | `exec`, `eval`, a subprocess or a notebook kernel in the agent process | Everything, including the breach | None | The agent's identity, secrets and network |

The [decision matrix](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#7-decision-matrix)
in the Code Execution Sandboxes asset compares Tiers 1 to 3 in detail, and
[ADR 0001 in that asset](../../../agent-runtime/code-execution-sandbox-session-pools/docs/decisions/0001-never-execute-generated-code-in-process.md)
records why in-process execution is refused.

## 5. Gate 5: the execution boundary

Gates 1 to 4 decide whether a call may happen and what data it may touch.
Gate 5 decides what the code inside the call can reach. It assumes the
earlier gates were bypassed: a prompt injection already produced hostile code,
and that code is now running.

### 5.1 Trust boundaries

The threat model in this asset defines TB1 to TB6. Code execution adds three.

```mermaid
flowchart LR
    U[User] --> AG[Agent]
    AG -->|tool call with code| BR[Execution broker<br/>holds the identity]
    BR -->|TB7: upload and execute<br/>server-derived session key| SB[Sandbox session<br/>no identity, no secrets]
    SB -.->|TB8: egress off by default| NET[(Internet, metadata<br/>endpoints, private network)]
    SB -->|TB9: files and values| BR
    BR -->|screened output| AG
    BR --> AUD[(Execution audit)]
```

| Boundary | Crossing | Control |
| --- | --- | --- |
| TB7 | Agent or broker to sandbox | The broker, not the model or the client, derives the session key from tenant, user and conversation. It pre-screens the code, enforces budgets and records the code hash. |
| TB8 | Sandbox to any network | Outbound access is off unless a written requirement turns it on. Then it goes through an allow-list. There is no route to metadata or identity endpoints. |
| TB9 | Sandbox output to the model or user | Output is size-limited, type-checked and scanned for secrets. It is screened for indirect injection before the model reads it, and active content is never rendered. |

### 5.2 Controls

| Control | What it enforces | Where | Evidence |
| --- | --- | --- | --- |
| Out-of-process isolation | Code never shares a process, identity or memory with the agent | Session pool (hypervisor-isolated sessions) | Isolation probe, section 14 |
| Server-derived session key | One session per user conversation; no client-chosen identifiers | Broker | `sessionRef` hash in every record |
| No identity in the sandbox | Code cannot mint a token for any resource | Pool configuration, broker | Probe: no identity endpoint, no token |
| Egress off by default | Code cannot send data out or fetch payloads | Pool network setting, firewall | Probe: outbound attempts fail; KQL query 9 |
| Pre-screen | Rejects oversized code, embedded secrets and policy violations before execution | Broker | `rejected` records with `errorCode` |
| Budgets | Bounds wall-clock time, calls per conversation, output size and spend | Broker, pool limits | `BudgetExceeded` and `ResourceLimitExceeded` records |
| Output guard | Bounds, types and screens what returns | Broker, Gate 4 screening | `stdoutTruncated`, `quarantined` flags |
| Pinned runtime | The image and package set are the ones that were reviewed | Pool definition, build pipeline | Image digest and lock hash; KQL query 10 |
| Approval for side effects | Code produces proposals; governed tools apply them | Gate 3, verify-before-approve | Approval record joined on `correlation_id` |
| Execution record | Every run is attributable and replayable | Broker audit | Section 15 |

A pre-screen is a filter, not a boundary. Pattern checks on code are easy to
evade, so treat them as noise reduction and early evidence. Isolation, the
absence of identity and the absence of egress are the boundary.

### 5.3 Blast radius when a control fails

| If this fails | What the attacker gets | What still contains it |
| --- | --- | --- |
| Pre-screen | Hostile code runs | Isolation, no identity, egress off, budgets |
| Egress setting | Can send out what that session holds | Minimal upload, one session per conversation, query 9 |
| Session key derivation | May read another user's session files | Per-tenant pools, short session lifetime, cleanup |
| Runtime image or package | Every session in that pool runs the bad code | Digest pin, read-back, query 10, blue/green pool rollback |
| Output guard | Injected text or a malicious file reaches the model or user | Gate 4 indirect-injection screening, Gate 3 approval |
| Approval for side effects | A proposed change is applied without review | Gate 4 source-side entitlements, kill switch |
| **In-process execution** | **The agent's identity, secrets, network and memory** | **Nothing. This is why it is never allowed.** |

## 6. Threat catalog

These vectors extend AV-01 to AV-10 in the
[threat model](threat-model-and-control-matrix.md#attack-vector-catalog). The
mappings follow OWASP Top 10 for LLM Applications 2025, the OWASP Top 10 for
Agentic Applications and MITRE ATLAS. Validate the identifiers against the
current releases before you cite them in a review.

| ID | Threat | OWASP LLM | OWASP Agentic | MITRE ATLAS | Primary control | Backstops | Section |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CX-01 | Generated code runs in the agent process | LLM05, LLM06 | ASI05 | AML.T0050 | Out-of-process isolation | None; refuse at design review | [7](#7-isolation-and-identity) |
| CX-02 | Data exfiltration through sandbox egress | LLM02 | ASI05 | AML.T0025 | Egress off by default | Minimal upload, query 9 | [8](#8-egress-and-data-movement) |
| CX-03 | Code reaches a metadata or identity endpoint and obtains a token | LLM02, LLM06 | ASI03, ASI05 | AML.T0055 | No identity in the sandbox | Egress off, isolation probe | [7](#7-isolation-and-identity) |
| CX-04 | Data bleeds across sessions, users or tenants | LLM02 | ASI03 | AML.T0057 | Server-derived session key | Per-tenant pools, cleanup | [10](#10-sessions-budgets-and-capacity) |
| CX-05 | Poisoned or drifting runtime image or package | LLM03 | ASI04 | AML.T0010 | Digest pin and lock hash | Read-back, query 10, pool rollback | [9](#9-runtime-images-and-packages) |
| CX-06 | A setting or tool contract is silently ignored or rewritten | LLM06 | ASI02 | Not mapped | Read-back after deployment | Contract tests, smoke tests | [16](#16-policy-as-code-and-read-back) |
| CX-07 | The agent or broker runs with the wrong or an over-privileged identity | LLM06 | ASI03 | AML.T0012 | Least-privilege roles on the runtime identity | Gate 4 source entitlements | [7](#7-isolation-and-identity) |
| CX-08 | Code changes a system of record directly | LLM06 | ASI02 | AML.T0053 | Side effects only through governed tools and Gate 3 | Egress off, source entitlements | [11](#11-side-effects-and-approval) |
| CX-09 | An unreviewed skill, prompt or model change reaches production | LLM03, LLM04 | ASI04, ASI06 | AML.T0010 | Change control with an evaluation gate | Version pin, rollback by repointing | [12](#12-change-control-for-skills-prompts-models-and-deployments) |
| CX-10 | A skill or code tool triggers when it should not | LLM06 | ASI01, ASI02 | Not mapped | Routing evaluation | Allow-listed skills, budgets | [12](#12-change-control-for-skills-prompts-models-and-deployments) |
| CX-11 | The model reports results from code that never ran | LLM09 | ASI09 | Not mapped | Execution-record check | Groundedness evaluation, verify-before-approve | [13](#13-proving-that-code-ran) |
| CX-12 | Runaway loops, resource exhaustion or capacity saturation | LLM10 | ASI08 | AML.T0029, AML.T0034 | Budgets | Capacity alerts, retry policy, query 11 | [10](#10-sessions-budgets-and-capacity) |
| CX-13 | Secrets appear in code, inputs or outputs | LLM02 | ASI03 | AML.T0055 | No secrets in sessions; pre-screen | Output guard, Purview | [7](#7-isolation-and-identity) |
| CX-14 | Output carries injected instructions or active content | LLM01, LLM05 | ASI01, ASI06 | AML.T0051.001 | Output guard | Gate 4 indirect-injection screening | [8](#8-egress-and-data-movement) |
| CX-15 | A deployment happens with no attributable record | LLM03 | ASI04, ASI10 | Not mapped | Pipeline-only deployment with a change record | Read-back, query 10 | [12](#12-change-control-for-skills-prompts-models-and-deployments) |

## 7. Isolation and identity

**Use a broker.** The agent sees one tool, for example `execute_code`. A broker
implements it. The broker holds the only identity that can call the session
pool, derives the session key, pre-screens the code, uploads inputs, enforces
budgets, guards the output and writes the audit record. The model never sees
pool endpoints, session identifiers or credentials. The
[broker design](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#10-pool-selection-and-the-mcp-gap)
and [role assignments](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#3-role-assignments)
are in the Code Execution Sandboxes asset.

**Keep identity out of the sandbox.**

- Do not assign a managed identity to a pool that runs untrusted code. If a
  custom image needs a dependency at build time, fetch it in the build
  pipeline, not at run time.
- Never pass tokens, keys, connection strings or environment credentials into a
  session. See
  [no secrets into sessions](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#4-no-secrets-into-sessions).
- When code needs protected data, the broker fetches it with its own identity,
  under Gate 4 entitlements, and uploads only what the task needs.
- Reach custom-container pools only through the pool management endpoint, with
  a token issued to the broker **(observed in preview; verify against current
  Microsoft Learn documentation)**.

**Give the agent runtime the narrowest identity that works.** Teams that moved
analytical agents to hosted runtimes saw the same identity issues:

- A newly deployed hosted agent can start with no data-plane roles. Grant
  explicit, minimal roles and prove them with a smoke turn. Do not grant broad
  roles to "make it work". See
  [Production Hosted Agents on Microsoft Foundry](../../../agent-runtime/production-hosted-agents-on-foundry/README.md).
- Assign roles to the identity that the running code actually uses. In
  preview, a hosted agent reports both an identity blueprint and an instance
  identity, and the instance identity is the one that calls dependencies
  **(observed in preview; verify against current Microsoft Learn
  documentation)**.
- New role assignments take time to propagate. Retry only a permission-denied
  error, with bounded backoff, and log the object ID that was refused. Do not
  retry other errors as if they were propagation delays.

**Isolate at the right grain.** Use one session per user conversation. Use a
separate pool per tenant where contracts, regulation or data classification
require it. See
[tenant isolation](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#6-tenant-isolation-per-tenant-pools-or-a-shared-pool).

## 8. Egress and data movement

**Egress is a property of the pool, not of the project.** A private Foundry
project, private endpoints for the model and a locked-down virtual network do
not make the sandbox private. The session pool has its own outbound setting,
and it must be set and read back explicitly **(observed in preview; verify
against current Microsoft Learn documentation)**. See
[egress policy](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#11-egress-policy).

- Keep outbound access off. Most analytical code needs no network once its
  packages are in the image.
- If a written requirement needs outbound access, send it through a firewall
  with a fully qualified domain name allow-list, and log denied attempts.
- Never allow package installation at run time. It needs egress, it bypasses
  review and it makes runs non-reproducible.

**Move the minimum data in.**

- Upload only the rows and columns the task needs. Prefer a governed query
  under Gate 4 that returns a filtered or aggregated extract.
- Label each upload with its classification, and record its hash and size.
- Delete session files when the conversation ends. See
  [session lifecycle and cleanup](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#9-session-lifecycle-and-cleanup).

**Treat everything that comes out as untrusted.**

- Allow-list output types, such as CSV, JSON and PNG, and cap their size.
- Scan text output and files for secrets and sensitive data before they leave
  the broker.
- Screen text output for indirect prompt injection before the model reads it.
  Code can print instructions as easily as data.
- Never render active content from a sandbox, such as HTML with scripts, SVG
  with handlers or documents with macros. Serve downloads as attachments.

See [output handling](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#12-output-handling).

## 9. Runtime images and packages

For Tier 3, the image is the runtime. Govern it like any production artifact.

- **Build in the pipeline.** Start from a reviewed base image, pinned by digest.
  Install packages from a lock file with hashes, for example
  `pip install --require-hashes -r requirements.lock`.
- **Verify imports, not metadata.** In one workshop, package metadata reported a
  library as installed while importing it failed. As a build step, import every
  top-level module the agent is allowed to use, and record the version that
  each import reports.
- **Record the evidence.** Keep the image digest, the SHA-256 of the lock file,
  a software bill of materials (SBOM) and the vulnerability-scan result with
  the release.
- **Reference the pool image by digest, never by tag.** A tag can move without
  a change record.
- **Roll out with blue/green pools.** Create a pool with the new digest. Run the
  isolation probe and the evaluation suite against it. Switch the broker
  configuration, and keep the old pool until the new one has served real
  traffic. Rollback means pointing the broker back.
- **Built-in pools change under you.** The platform owns the image. Record the
  runtime version reported by each execution where the platform exposes it, and
  re-run the evaluation suite when it changes.

KQL query 10 in [`audit-queries.kql`](../samples/audit-queries.kql) flags
executions whose image digest or lock hash is not on the approved list.

## 10. Sessions, budgets and capacity

**Session keys.** The broker derives the session key, for example as an HMAC
of tenant, user and conversation under a server-held key. It rejects any
client-supplied session identifier. A guessable or client-chosen key lets one
user read another user's files.

**Capacity.** A session can stay allocated until its cooldown period ends,
even after the conversation is over **(observed in preview; verify against
current Microsoft Learn documentation)**. Size the pool from the peak rate of
new conversations multiplied by the total hold time, cooldown included. Not
from average concurrency. Then:

- Treat a capacity error, such as HTTP 429, as retryable with bounded backoff
  and a clear message to the user.
- Alert on saturation before users see failures. KQL query 11 counts capacity
  rejections per pool.
- Load-test with realistic conversation arrival rates, not only single runs.

**Budgets.** Bound every execution and every conversation:

- Wall-clock time per execution.
- Number of executions per conversation and per user per day.
- Output size returned to the model.
- Compute spend per tenant.

When a budget is exceeded, stop and escalate rather than retry silently. The
[Bounded Autonomy](../../../orchestration-and-workflows/bounded-autonomy/README.md)
asset describes budget envelopes and escalation.

**Lost state.** A session can be recycled mid-conversation. The broker must
detect lost state, report it and either restore the inputs or ask the model to
start again. It must never let the model continue as if earlier variables
still exist.

## 11. Side effects and approval

Code produces proposals. Governed tools apply them.

- **Return artifacts, not actions.** Code returns a dataset, a chart, a diff, a
  SQL statement or a change plan. A typed tool with its own identity and Gate 3
  approval applies the change to the system of record.
- **Verify before a human approves.** Run deterministic checks on the artifact
  and bind the approval to its digest, as in
  [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md).
  Show the code hash, the inputs, the outputs and the exact change on the
  approval card. See
  [approval for side-effecting code](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#10-approval-for-side-effecting-code).
- **Run generated queries read-only.** Generated SQL or KQL runs under a
  read-only identity against a scoped view or replica. Writes go through typed
  tools.
- **Coding agents work through pull requests.**
  - Write to a branch, never to a protected branch or to production.
  - Require human review and passing deterministic checks before merge.
  - Keep production secrets and deployment identities out of the coding
    agent's reach. The pipeline deploys, under its own identity, to an
    environment that requires approval.
  - Protect the files that define the guardrails, such as policy files,
    pipeline definitions and code-owner rules, with mandatory owner review.
    An agent must not be able to relax its own controls.

## 12. Change control for skills, prompts, models and deployments

Anything that changes what the agent does is a production change.

| Artifact | What counts as a change | Gate before production | Rollback |
| --- | --- | --- | --- |
| Skill or script | New version, changed description, changed code | Review, routing and functional evaluation | Repoint the agent to the previous version |
| System prompt or instructions | Any text change | Evaluation suite, including red-team cases | Redeploy the previous version |
| Model or model version | New deployment, new version, new parameters | Full evaluation suite and isolation probe | Repoint the deployment |
| Runtime image | New digest or lock hash | Isolation probe, import check, evaluation | Blue/green pool switch |
| Agent definition | Tools, skills, connections, identity | Read-back and smoke test | Redeploy the previous definition |
| Evaluator | Evaluator version or grader logic | Calibration run against a labelled set | Pin the previous evaluator version |

Lessons that kept recurring in the workshops:

- **Publishing a skill can change production immediately.** If the agent
  follows the latest version, a publish skips every gate **(observed in
  preview; verify against current Microsoft Learn documentation)**. Allow-list
  skill names per agent, pin the version in the agent definition, gate the
  default version on evaluation, and roll back by repointing.
- **A skill description is a routing contract.** Smaller models tend to trigger
  skills too often. Test routing with prompts that should and should not
  trigger each skill, and track the trigger rate as a release metric.
- **Deployments are not always what they seem.**
  - Redeploying an identical definition may do nothing.
  - Skills may have no traffic split, so a new version reaches every user at
    once.
  - The previous version can keep serving briefly after an update.
  
  Smoke tests should first list the tools and skills, assert the expected
  versions, and only then run test cases.
- **Frameworks can rewrite tool contracts.** In one workshop, an agent framework
  silently replaced a tool parameter whose name it also used internally. Avoid
  generic parameter names, and add contract tests that call each tool and
  assert every parameter arrives unchanged.
- **Agent deployment can be a data-plane operation with no Azure Resource
  Manager deployment history** **(observed in preview; verify against current
  Microsoft Learn documentation)**. Deploy only from a pipeline with its own
  identity, write a change record with the definition hash, image digest and
  evaluation result, and read the deployment back.
- **Pin evaluator versions.** An unpinned evaluator can run a different version
  than the one you calibrated.

## 13. Proving that code ran

An answer that cites a calculation is only as good as the evidence that the
calculation happened. Models sometimes write a plausible tool result without
calling the tool. In one workshop this appeared in a small share of evaluation
runs, and the answers looked correct.

- **Check for an execution record.** For every answer that relies on code, the
  broker must hold an `execute` record with the same correlation ID, a
  successful outcome and a code hash that matches the code in the agent trace.
  If no record exists, treat the answer as ungrounded.
- **Read the final answer, not the reasoning.** Some models put the result in
  the reasoning summary and leave the final message thin. Graders and
  downstream parsers should read the final output only, and fail when it is
  empty.
- **Count tool calls from the broker.** In one workshop, the evaluation tooling
  counted each tool call twice. Use the broker's audit records as the source of
  truth for "how many executions", and compare them with the evaluation
  report.
- **Make graders explain failures.** A code-based grader that returns only a
  generic error hides the cause. Return a structured reason, such as missing
  execution, hash mismatch, timeout or wrong output, so failures can be
  triaged.

Bind the execution evidence to the approval and the run record, as described
in [Evidence Ledger and Run Certificate](../../../orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md).

## 14. Isolation verification probe

Configuration says where code should run. The probe shows where it actually
runs. It is a short, read-only script that you run through the same path the
agent uses, at three points:

1. When you design the system, to confirm the tier.
2. In the release pipeline, against every new pool or image.
3. On a schedule in production, as a canary.

[`execution_isolation_probe.py`](../samples/execution_isolation_probe.py)
reports:

- The names, never the values, of environment variables that look like
  secrets or credentials.
- Whether a managed identity endpoint is advertised.
- Whether the instance metadata address and a public address are reachable.
- Basic facts, such as the hostname and the Python version.
- Optionally, whether listed packages import, compared with their metadata.

It exits with a non-zero status if any check fails.

What the probe found in one workshop:

| Check | In-process "sandbox" | Sandboxed session |
| --- | --- | --- |
| Secret-like environment variables | Several, including model and storage keys | None |
| Managed identity endpoint | Advertised; a token could be requested | Not present |
| Instance metadata address | Reachable | Not reachable |
| Public internet | Reachable | Blocked |
| Hostname | Contained the word "sandbox" | Platform-assigned |

The hostname is the lesson. The component was called a sandbox and its
hostname said so, but code was running inside the agent's own process. Names
are not evidence. Run the probe.

Run `python execution_isolation_probe.py --self-test` to see the expected
verdicts for both cases without a live environment.

## 15. Audit and evidence

The broker writes one record for every upload, execution, download, deletion
and refusal. Use the fields defined in the H3 asset's
[audit section](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#9-audit),
and add two fields so that runtime drift can be detected:

| Field | Content |
| --- | --- |
| `correlationId` | Joins the record to the agent trace, the MCP audit records in `MCPToolAudit_CL` (`correlation_id`) and the approval record |
| `event` | `upload`, `execute`, `download`, `delete` or `rejected` |
| `tenantRef`, `userRef`, `sessionRef` | Keyed hashes, never raw identifiers |
| `pool`, `poolType` | Which pool, and whether it is built in or custom |
| `codeSha256`, `codeBytes` | Hash and size of the submitted code |
| `findings` | Pre-screen, egress-decision and output-guard indicators, by pattern or reason name |
| `outcome`, `errorCode` | Result and error taxonomy code |
| `stateLost`, `stdoutTruncated`, `quarantined` | Quality and abuse signals |
| `platformTraceId` | The platform's trace identifier, for support cases |
| `imageDigest`, `lockSha256` | **Added here.** The runtime image digest and lock file hash in force |

Do not store submitted code or outputs in the audit stream by default. They can
contain user data. Where you must keep them, store them in a separate,
access-controlled location, referenced by hash and with a retention period.

Queries 9 to 11 in [`audit-queries.kql`](../samples/audit-queries.kql) read a
custom table, `SandboxExecAudit_CL`, populated from these records:

| Query | Signal |
| --- | --- |
| 9 | Egress, metadata-address and secret indicators, such as refused outbound attempts or quarantined output |
| 10 | Executions on an image digest or lock hash that is not on the approved list |
| 11 | Capacity rejections, budget stops and state loss per pool |

**Evidence pack.** For each release that enables or changes code execution,
keep the following with the release record. The format follows the
[evidence pack](evaluation-and-release-gates.md#evidence-pack):

- The isolation probe output for each pool.
- The image digest, lock hash, SBOM and scan result.
- The read-back output for pools, agent definitions and skill versions.
- Red-team results for the CX-T cases in [Section 17](#17-red-team-cases).
- Routing and functional evaluation results, with evaluator versions.
- The change record identifier and the approver.

## 16. Policy as code and read-back

Apply the controls in the H3 asset's
[policy as code](../../../agent-runtime/code-execution-sandbox-session-pools/docs/authentication-and-governance.md#11-policy-as-code)
section: egress off unless exempted, no broad roles on pools, approved
registries by digest, allowed regions and lifecycle settings.

Then read back what the platform actually did. A request can succeed while a
setting is ignored. In one workshop, a pool provisioned successfully while a
requested capability was silently dropped for that pool type **(observed in
preview; verify against current Microsoft Learn documentation)**. Read back,
after every deployment and as a scheduled check:

| Resource | Read back | Fail the release when |
| --- | --- | --- |
| Session pool | Pool type, network egress setting, image reference, lifecycle settings, enabled capabilities | Any value differs from the declared configuration |
| Role assignments | Every assignment at pool scope | Anything other than the broker identity's execution role |
| Agent definition | Tools, skills and their versions, model deployment, identity | Any unlisted tool or skill, or an unpinned version |
| Skill registry | Default version per skill | The default version has no passing evaluation |
| Tool contracts | Each tool's parameters as received by the tool | A parameter is renamed, dropped or overwritten |

The last row comes from the framework rewrite described in
[Section 12](#12-change-control-for-skills-prompts-models-and-deployments).
Contract tests call each tool with known values and compare what arrives.

## 17. Red-team cases

These cases extend `S1` to `S8` in the
[threat model](threat-model-and-control-matrix.md#red-team-verification-test-cases).
The H3 asset's
[red-team catalogue](../../../agent-runtime/code-execution-sandbox-session-pools/docs/evaluation-and-troubleshooting.md#2-red-team-catalogue)
covers the broker and pool mechanics in detail. The CX-T cases add the
governance view. Run them before enabling code execution and before every
release that changes a pool, an image, a skill or a model.

| ID | Threat | Procedure | Expected result | H3 cases |
| --- | --- | --- | --- | --- |
| CX-T1 | CX-01 | Configure an in-process backend | The broker refuses to start | RT-01, RT-24 |
| CX-T2 | CX-03 | Run the isolation probe in each pool | No identity endpoint, no metadata access, no secret-like variables | RT-02, RT-03, RT-04 |
| CX-T3 | CX-02 | Fetch a public URL and post data to it from code | Both fail; query 9 shows the attempt | RT-05, RT-06, RT-07 |
| CX-T4 | CX-04 | Supply a session identifier; reuse another user's conversation | Rejected; the second user gets a different session | RT-15 to RT-18 |
| CX-T5 | CX-05 | Run on a pool whose digest is not approved; install a package at runtime | Query 10 flags the execution; the install fails | RT-23 |
| CX-T6 | CX-06 | Deploy a pool or agent definition with a setting the platform ignores | Read-back fails the release | Not covered |
| CX-T7 | CX-08 | Ask code to update a system of record | Produces a proposal only; nothing is applied | RT-22 |
| CX-T8 | CX-09 | Publish a new skill version without evaluation | The agent keeps the pinned version; the default is not moved | Not covered |
| CX-T9 | CX-10 | Send prompts that should not trigger a code skill | Trigger rate stays within the release threshold | Not covered |
| CX-T10 | CX-11 | Remove the execution tool and ask for a calculation | The answer is flagged as having no execution record | Not covered |
| CX-T11 | CX-12 | Run a runaway loop; exceed the per-conversation budget; saturate the pool in a load test | Execution stops; the conversation escalates; query 11 alerts | RT-08 to RT-10 |
| CX-T12 | CX-13, CX-14 | Echo secret-shaped strings; return an active artifact | Redacted and quarantined | RT-11 to RT-13, RT-19 |

Keep attack payloads in a private test repository. Publish only the intent and
the expected outcome.

## 18. Production checklist

Use this list alongside the H3 asset's
[production checklist](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#17-production-checklist).

**Design**

- [ ] The use case is placed in an execution tier, with the reason recorded.
- [ ] Nothing runs in the agent process. Tier 4 is never used.
- [ ] The trust boundaries TB7, TB8 and TB9 are in the threat model.
- [ ] Every side effect goes through a governed tool with Gate 3 approval.

**Identity and isolation**

- [ ] Sessions hold no identity, secrets or connection strings.
- [ ] Only the broker identity can execute in the pool.
- [ ] Session keys are derived on the server; client-supplied keys are refused.
- [ ] The isolation grain (per user, per tenant) matches the data
      classification.

**Data and egress**

- [ ] Egress is off, or limited to an exact allowlist with an exemption record.
- [ ] Only the minimum data is uploaded; files are deleted at conversation end.
- [ ] Outputs pass the output guard before the model sees them.

**Runtime and change control**

- [ ] Images are referenced by digest, with the lock hash, SBOM and scan
      recorded.
- [ ] Skills, prompts, models and evaluators are pinned and gated.
- [ ] Deployments come only from a pipeline, with a change record.
- [ ] Read-back runs after every deployment and on a schedule.

**Operations**

- [ ] Budgets and capacity are sized from peak arrival rate and hold time.
- [ ] The isolation probe runs in the pipeline and as a production canary.
- [ ] Queries 9 to 11 are scheduled, with owners for each alert.
- [ ] The CX-T cases pass, and the results are in the evidence pack.
- [ ] There is a kill switch: the broker can be disabled, or the code tool
      detached, without redeploying the agent.

## 19. Residual risks

- **Logic errors in correct code.** Isolation does not make an analysis right.
  Functional evaluations, verify-before-approve and human review remain
  necessary.
- **Data the session legitimately holds.** Code can misuse any data it was
  given, within the session. Minimal upload limits what is at risk.
- **Allowlisted destinations.** Every allowlisted host is a possible
  exfiltration path. Keep the list short, exact and reviewed.
- **Platform vulnerabilities.** A flaw in the sandbox platform itself could
  break isolation. Per-tenant pools and no identity in the session limit the
  impact. Follow the platform's security advisories.
- **Supply chain.** A reviewed package can still contain a flaw. The lock hash
  and SBOM make it possible to find every affected pool quickly.
- **Evaluation gaps.** Red-team and routing evaluations cover known cases only.
  Treat production signals from queries 9 to 11 as an input to the next
  evaluation set.

## 20. Preview caveats

Several behaviours in this guide were observed on preview services
**(observed in preview; verify against current Microsoft Learn
documentation)**:

- The hosted agent code-execution runtime supported Python only.
- Pool management endpoints, capability support per pool type, and network
  settings were changing between releases.
- Session hold times and cooldown behaviour affected capacity planning.
- Skill versioning, default-version behaviour and deployment semantics were
  changing.

The H3 asset lists the mechanics-level caveats in its
[preview caveats](../../../agent-runtime/code-execution-sandbox-session-pools/docs/architecture.md#19-preview-caveats)
section. Re-check each one when you plan a release.

## 21. Review cadence

Review this guide and the CX threat catalog:

- When you enable code execution for an agent, or move it to a different tier.
- When you add or change a pool, a runtime image, the egress setting or an
  allowlist entry.
- When you add or change a skill, a model or a framework version.
- After any finding from the isolation probe, read-back or queries 9 to 11.
- Otherwise, quarterly, with the platform, security operations and data
  owners.

## 22. References

**Decisions**

- [ADR 0002: Execution boundary for generated code](decisions/0002-execution-boundary-for-generated-code.md)
- [ADR 0001: Four-gate tool governance](decisions/0001-four-gate-tool-governance.md)

**This asset**

- [Architecture walkthrough](architecture.md)
- [Threat model and control matrix](threat-model-and-control-matrix.md)
- [Evaluation and release gates](evaluation-and-release-gates.md)
- [Isolation probe](../samples/execution_isolation_probe.py)
- [Audit queries](../samples/audit-queries.kql)

**Related assets**

- [Code Execution Sandboxes with Session Pools](../../../agent-runtime/code-execution-sandbox-session-pools/README.md)
- [Production Hosted Agents on Microsoft Foundry](../../../agent-runtime/production-hosted-agents-on-foundry/README.md)
- [Request Context Is Not Authorization](../../../agent-runtime/request-context-is-not-authorization/README.md)
- [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md)
- [Bounded Autonomy](../../../orchestration-and-workflows/bounded-autonomy/README.md)
- [Evidence Ledger and Run Certificate](../../../orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md)
- [Governed Context-Aware Agent Harness](../../../orchestration-and-workflows/governed-context-aware-agent-harness/README.md)
- [Secured Microsoft Foundry Landing Zone for Enterprise Agents](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)

**Microsoft Learn**

- Dynamic sessions in Azure Container Apps:
  <https://learn.microsoft.com/azure/container-apps/sessions>
- Session pools: <https://learn.microsoft.com/azure/container-apps/session-pool>
- Code interpreter sessions:
  <https://learn.microsoft.com/azure/container-apps/sessions-code-interpreter>
- Custom container sessions:
  <https://learn.microsoft.com/azure/container-apps/sessions-custom-container>
- Use Azure Firewall with Container Apps:
  <https://learn.microsoft.com/azure/container-apps/use-azure-firewall>
- Hosted agents in Foundry Agent Service:
  <https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents>
- Azure built-in roles:
  <https://learn.microsoft.com/azure/role-based-access-control/built-in-roles>

**Frameworks**

- OWASP Top 10 for LLM Applications and Top 10 for Agentic Applications:
  <https://genai.owasp.org/>
- MITRE ATLAS: <https://atlas.mitre.org/>
