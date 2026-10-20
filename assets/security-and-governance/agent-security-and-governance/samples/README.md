# Samples

Sanitized reference code for the
[Enterprise Agent Security and Tool Governance](../README.md) asset. The samples
are domain-agnostic: the "records" they govern can be incidents (ITSM / SecOps),
invoices or journal entries (FinOps / ERP), or cases and accounts (Customer
Support / CRM). Everything uses synthetic partitions `org-a` and `org-b`,
illustrative identities and placeholder IDs such as `<tenant-id>` and
`<object-id>`. Replace them with your own values.

These samples are illustrative, not supported product samples. Several
dependencies are in preview; pin package versions and test before use.

| File | Gates | Runs offline | What it shows |
|------|-------|--------------|---------------|
| [`governed_mcp_server.py`](governed_mcp_server.py) | 3 (and 4 audit) | Yes, `--self-test` | MCP server that enforces source-side entitlement from token claims, approval binding and "no audit, no action" |
| [`governed_agent_example.py`](governed_agent_example.py) | 1, 2 | No (hosted agent) | Hosted agent with zero tool code and no credentials; tools come from a versioned Foundry Toolbox |
| [`provision_toolbox_and_guardrails.py`](provision_toolbox_and_guardrails.py) | 1, 2, 4 | Yes (default) | Registers the server in the private tool catalog, publishes split toolsets, binds a guardrail and detects drift |
| [`kill_switch_and_smoke_test.py`](kill_switch_and_smoke_test.py) | 1 to 4 | Yes (default) | Security smoke suite `S1` to `S8` and the three-tier kill-switch rehearsal |
| [`audit-queries.kql`](audit-queries.kql) | 4 | Needs a workspace | Eight detection and review queries joined on `correlation_id` |

Gate numbers follow the control matrix in
[`../docs/threat-model-and-control-matrix.md`](../docs/threat-model-and-control-matrix.md):
Gate 1 catalog and toolsets, Gate 2 Entra ID and RBAC, Gate 3 MCP server-side
entitlement, Gate 4 guardrails and audit.

## Quick start

From this folder, with Python 3.10 or later:

```powershell
python governed_mcp_server.py --self-test        # needs the mcp and PyJWT packages
python provision_toolbox_and_guardrails.py       # standard library only
python kill_switch_and_smoke_test.py             # standard library only
```

Each command exits `0` only when every check passes, so all three can run as CI
steps. See [`../docs/evaluation-and-release-gates.md`](../docs/evaluation-and-release-gates.md)
for the full promotion pipeline.

## governed_mcp_server.py

The enforcement point that prompts and orchestration bugs cannot bypass. It
validates the bearer token, maps app roles of the form `Records.Org<Id>.Read`
or `Records.Org<Id>.Operate` to entitlements, and decides every call from the
token's claims, not from caller-supplied arguments. Denials use stable reasons
such as `unmapped_principal`, `org_not_entitled` and `insufficient_level`, so
detections and tests can match on them.

- Write tools need explicit confirmation; without it the server returns `409`.
- Every decision is written as one `GOV_AUDIT` JSON line. If the audit write
  fails, the action is refused ("no audit, no action").
- The self-test runs the gate test matrix offline and exits non-zero on any
  failure.

```powershell
python governed_mcp_server.py --self-test    # offline gate test matrix
python governed_mcp_server.py --port 8000    # serve the MCP endpoint
```

Approval binding (approval tied to the exact tool, arguments and caller, with
replay protection) and poisoned-output framing are modeled in
`kill_switch_and_smoke_test.py` and described in the
[threat model](../docs/threat-model-and-control-matrix.md). Add them to your
server if it does not already enforce them.

## governed_agent_example.py

A hosted agent built with Microsoft Agent Framework. Tools come from a Foundry
Toolbox (`FoundryToolbox`), approval is read from the Toolbox `require_approval`
policy and applied as `approval_mode` (fail closed), and the agent is served
with `ResponsesHostServer`.

| Gate | Where it is enforced | What the sample does |
|------|----------------------|----------------------|
| 1. Catalog and toolsets | Foundry Toolbox | Binds to `TOOLBOX_NAME` (default version) or a pinned `TOOLBOX_ENDPOINT`. A new Toolbox session per request means promoting or rolling back a version takes effect without a redeploy. |
| 1. Approval policy | Toolbox policy, enforced by the runtime | `GovernedToolbox` reads `_meta.tool_configuration.require_approval` from `tools/list` and sets `approval_mode`. Unclassified tools require approval. `APPROVAL_REQUIRED_TOOLS` can only add approvals. |
| 2. Identity | Toolbox connection (Entra Agent ID or OAuth passthrough) | Nothing. The Toolbox brokers the token; the agent authenticates to Foundry with `DefaultAzureCredential`. |
| 3. Source entitlement | Downstream MCP server | Nothing. The prompt tells the model to report denials verbatim and not to work around them. |

The optional `fetch_url` tool (`ENABLE_WEB_FETCH=1`) is deliberately unrestricted
in code to show that network reach is governed by the guardrail's egress
allow-list. With egress in Enforce mode, unapproved hosts return HTTP 403.
`GovernedToolbox` overrides an internal method of the preview hosting package;
re-check it when you upgrade.

| Variable | Purpose |
|----------|---------|
| `FOUNDRY_PROJECT_ENDPOINT` | Foundry project endpoint (injected by the hosted runtime) |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | Model deployment name |
| `TOOLBOX_NAME` / `TOOLBOX_ENDPOINT` | Toolbox name (default version) or full MCP URL (pinned version) |
| `APPROVAL_REQUIRED_TOOLS` | Optional comma-separated tools that always need approval |
| `ENABLE_WEB_FETCH` | `1` to add `fetch_url` |

Packages: `agent-framework`, `agent-framework-foundry`,
`agent-framework-foundry-hosting`, `azure-identity` and `httpx`. Deploy as a
Foundry hosted agent; see the
[Foundry hosted agents documentation](https://learn.microsoft.com/azure/foundry/agents/overview).

## provision_toolbox_and_guardrails.py

Desired-state provisioning for Gates 1, 2 and 4, with drift detection:

1. Registers `governed-records-mcp` in the private tool catalog, pinned to a version.
2. Publishes two toolsets: `ops-readonly-mcp` (R tools only) and
   `ops-operator-mcp` (R and W tools, every W tool with `require_approval`).
   Tools the server publishes but the script has not classified are never exposed.
3. Creates guardrail `ops-agent-guardrail` with `UserPromptInjectionShield`,
   an indirect prompt-injection filter on `ToolOutput` (`block_or_refuse`), a PII
   and credential egress block, and a groundedness check, and binds it to each
   agent deployment.
4. Checks least privilege: `ops-reader-agent` holds only `MCP.Tools.Read`;
   `ops-operator-agent` holds `MCP.Tools.Operate` as an eligible (PIM or
   time-bound Access Package) assignment, never standing.
5. Injects eight kinds of drift into the in-memory control plane and asserts
   each is detected and remediated.

| Mode | Command | Notes |
|------|---------|-------|
| Offline self-test | `python provision_toolbox_and_guardrails.py` | Default when `FOUNDRY_PROJECT_ENDPOINT` is unset |
| Plan | `python provision_toolbox_and_guardrails.py --live` | Prints the plan and drift report; changes nothing |
| Apply | `python provision_toolbox_and_guardrails.py --live --apply` | Needs `azure-identity` and project permissions |

Toolbox, guardrail and agent-identity management APIs are in preview. Treat
`API_PATHS` and the payload shapes in `AzureControlPlane` as placeholders to
align with current documentation; the offline self-test is the verified part.

## kill_switch_and_smoke_test.py

A zero-dependency harness that models the four gates in memory and runs:

| Case | Expected |
|------|----------|
| `S1` Unauthenticated call | `401 missing_bearer_token` |
| `S2` Token for another audience | `401 wrong_audience` |
| `S3` Cross-tenant `tenant_id` override | `403 org_not_entitled`, claimed tenant ignored and audited |
| `S4` Read caller invokes a write tool | `403 insufficient_level` |
| `S5` Write without a valid approval | `409` for missing, swapped, replayed and expired approvals |
| `S6` Write with a valid approval | `200` with `change_id` and a `write_authorized` audit record |
| `S7` Poisoned tool output | Gate 4 `block_or_refuse`; no write issued |
| `S8` Gate 1 kill switch | Write tools leave `tools/list` in under 60 seconds without an agent redeploy |
| `X1` Audit sink unavailable | `503 audit_unavailable`, no change made |
| `TIER1` Toolbox detach | Write refused at Gate 1, reads healthy, recovery to `200` |
| `TIER2` MCP write flag | W tools `writes_disabled`, R tools healthy, recovery to `200` |
| `TIER3` Entra identity disable | Cached token `401 principal_revoked`, new tokens refused, other identities unaffected |

Every case also asserts audit completeness: each MCP call carries a
`correlation_id` and the expected audit events are present. A parity step runs
`governed_mcp_server.py --self-test` so the in-memory model cannot drift from
the reference server; it is skipped (not failed) if `mcp` or `PyJWT` is missing.

```powershell
python kill_switch_and_smoke_test.py --json smoke-results.json   # CI artifact
python kill_switch_and_smoke_test.py --no-parity --verbose       # print GOV_AUDIT records
```

The simulated clock makes the 60-second Tier 1 budget deterministic. In a real
environment, rehearse each tier against the deployed resources and record the
measured times.

## audit-queries.kql

Queries for Log Analytics. `MCPToolAudit_CL` holds the server's decision
records; query 0 shows how to derive it from Container Apps console logs if you
do not ingest it through a data collection rule.

| Query | Use it to | Related test |
|-------|-----------|--------------|
| 1. Cross-tenant probes | Find callers that named one partition and hit another, or were not entitled | `S3` |
| 2. Privilege escalation | Find read-level callers invoking write tools | `S4` |
| 3. Unapproved writes | Count `409` refusals; flag swapped or replayed approvals | `S5` |
| 4. End-to-end trace | Join `AppRequests`, guardrail `AzureDiagnostics` and `MCPToolAudit_CL` by `correlation_id`; flag writes that bypassed the agent | Red-team unauthorized-write metric |
| 5. XPIA blocks | Count indirect prompt-injection blocks and list the poisoned records | `S7` |
| 6. Write velocity anomaly | Detect runaway loops or abuse per principal | Load test |
| 7. Kill-switch timeline | Show activation, refusals and release across Toolbox, Entra ID and the MCP server | `S8`, Tier 1 to 3 |
| 8. Entitlement review | Find unused or over-privileged `MCP.Tools.Operate` assignments | Access reviews |

The schema is described in
[the architecture document](../docs/architecture.md#unified-audit-schema).
`AzureDiagnostics` categories and column names vary by resource and API
version; validate queries 4 and 5 against your workspace before alerting on them.
