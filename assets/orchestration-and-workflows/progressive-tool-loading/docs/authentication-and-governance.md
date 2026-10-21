# Authentication and Governance

This document covers who decides what in progressive loading, which identities take part, how the
skill registry and loading policy are governed, what is audited and how prompt injection and privacy
are handled. It assumes the design in [architecture.md](architecture.md).

It does not repeat how entitlements are sourced, filtered and enforced, which is in the
[Entitlement-Aware Tool Catalog](../../../ai-gateway/entitlement-aware-tool-catalog/docs/authentication-and-governance.md),
the token-validation checklist, which is in
[Request Context Is Not Authorization](../../../agent-runtime/request-context-is-not-authorization/docs/authentication-and-governance.md),
or how approvals and the autonomy envelope are governed, which is in
[Bounded Autonomy](../../../orchestration-and-workflows/bounded-autonomy/README.md).

Role names, product features and protocol fields change between releases. Check them against
current documentation before you rely on them.

## 1. Principles

1. **Loading is not authorization.** Progressive loading decides when an eligible tool is shown.
   It never decides whether a caller may use a tool
   ([ADR 0001](decisions/0001-loading-is-not-authorization.md)).
2. **Eligibility narrows, never widens.** Eligible = entitled (from the tool server, for the verified
   caller) ∩ permitted by the autonomy profile. A skill cannot add tools to that set.
3. **Two checks on every call.** The harness call gate rejects names that are not loaded, not
   eligible or unknown. The tool server re-checks entitlement on every `tools/call` regardless.
4. **Skills are governed content.** A skill body is an instruction to the model. It is reviewed,
   versioned and hashed like code.
5. **Record decisions, not content.** The ledger records which skills and tools were disclosed and
   why. It never records tool arguments, tool results, raw search queries or tokens.

## 2. Three decisions, three owners

| Decision | Question it answers | Owner | Where it is enforced | Changes when |
|---|---|---|---|---|
| **Entitlement** | Which tools may this caller ever use? | Tool or product owner, tenant administrator | MCP server on `tools/list` and `tools/call` ([Entitlement-Aware Tool Catalog](../../../ai-gateway/entitlement-aware-tool-catalog/README.md)) | Role, license or policy changes |
| **Autonomy profile** | Which effect classes may this agent use in this run? | Envelope owner for the workload ([Bounded Autonomy](../../../orchestration-and-workflows/bounded-autonomy/README.md)) | Harness, before loading; approval gates on calls | Workload risk review |
| **Disclosure** | Which eligible tools and instructions are shown now? | Agent team (this asset) | Harness loader and call gate | Phase, budget, model request, catalog change |

A problem in one row is fixed in that row. If a caller sees a tool they should never use, fix
entitlement, not the phase map. If an agent writes when it should only observe, fix the profile. If
the model keeps missing a tool it is allowed to use, fix disclosure.

## 3. Identity flow

- The harness runs on behalf of a **verified caller**. The caller identity comes from validated token
  claims, never from headers, session state or model output. See
  [Request Context Is Not Authorization](../../../agent-runtime/request-context-is-not-authorization/docs/architecture.md#8-agents-on-foundry-what-arrives-and-which-identity-to-use)
  for what arrives at an agent hosted on Microsoft Foundry and which identity to use downstream.
- The loader asks the catalog for that caller's tools (`list_tools(caller)` in the sample; in
  production, `tools/list` with a delegated token through the gateway). It never computes
  entitlement from a local role table.
- Every tool call carries the same caller context (`call_tool(caller, ...)`). The server decides.
- The harness's own workload identity is used only for infrastructure: reading the skill registry,
  querying the search index, writing telemetry and ledger events. It is never used to call a domain
  tool on the caller's behalf, because that would replace the caller's entitlement with the
  harness's.

## 4. Azure permissions

Grant each identity the narrowest role that works, scoped to the specific resource. Verify role
names against current documentation.

| Identity | Needs access to | Typical permission |
|---|---|---|
| Caller (user or client application) | MCP server through Azure API Management | App role or scope on the MCP server's app registration in Microsoft Entra ID |
| Harness workload (managed identity) | Skill registry storage | Read-only data role on the registry container or repository |
| Harness workload | Azure AI Search skill index (optional) | Search Index Data Reader on the index's service |
| Registry publishing pipeline | Skill registry storage and search index | Write roles, held only by the pipeline, not by the harness |
| Harness workload | Application Insights | Ingestion through the Azure Monitor OpenTelemetry distro; if local authentication is disabled, the publishing role that Entra-based ingestion requires |
| Harness workload | Evidence store | Append-only write as defined by the evidence ledger asset |

The harness never needs write access to the skill registry or the search index. A harness that can
change its own skills can change its own instructions.

## 5. Skill registry governance

Treat the registry as a release artifact, not a shared folder.

- **Ownership.** Every skill has a named owning team and a reviewer outside the author.
- **Structure.** A skill is a folder with a `SKILL.md` (frontmatter `name`, `description`, metadata
  for `version`, `phases`, `tools`, `always_on`) and optional reference files. The sample's
  `parse_skill_md` rejects invalid names, unknown phases and malformed tool ids; the publishing
  pipeline should also reject tool ids that do not exist in the catalog.
- **Version and hash.** Every change bumps the version. The pipeline computes a content hash over
  the name, version, body, tool list and references; the loader records it on every `skill_loaded`
  event. A reviewer can then prove which instructions the model saw.
- **Signing.** Sign or attest published registry snapshots and verify the signature when the harness
  loads the registry. Unsigned or unverified skills are not loaded.
- **Description review.** Descriptions drive selection. Review each new description against existing
  ones for overlap: two skills that claim the same need produce wrong-tool calls. Use the labeled
  evaluation in [evaluation and troubleshooting](evaluation-and-troubleshooting.md) as the gate.
- **Split by effect.** Keep read and write tools in separate skills where possible, so an
  observe-only profile can still load the read skill whole, and the ledger shows clearly when write
  capability was disclosed.
- **No tool-granting.** A skill's `tools` list is a request to show tools if they are eligible. It is
  never a grant. Review rejects any design where the skill registry is a source of entitlement.
- **Withheld names in bodies.** A skill body should not name tools that some callers cannot use.
  If it must, those callers get `Unknown tool.` and the telemetry shows the noise; prefer per-audience
  skill variants instead.
- **Retirement.** Retire skills by removing them from the registry snapshot, not by editing their
  body to say "do not use". Keep old versions for audit replay.

## 6. Loading policy governance

The following are configuration, reviewed like code and recorded with the agent version:

| Setting | Why it is governed |
|---|---|
| Phase map (skill `phases` metadata) | Decides what the model sees by default at each stage |
| Always-on skills | Policy instructions that must be present for the whole run |
| Budget and counting method | Too small causes thrashing; too large brings back distractors |
| Overflow policy (`evict` or `refuse`) | `refuse` gives a stricter, auditable disclosed set |
| Meta-tool load cap per phase | Bounds model-driven expansion |
| Whether write skills may be loaded by meta-tool before plan approval | Stricter deployments refuse it ([ADR 0002](decisions/0002-phase-driven-loading-with-meta-tool-fallback.md)) |
| Search index contents and filters | Search results must never reveal skills outside the eligible set |

## 7. Disclosure rules

- The tier 0 index lists **eligible** skills only. Withheld skills do not appear by name, by
  description or by count.
- `load_skill` for a withheld skill and for a skill that does not exist returns the same
  `UNKNOWN_SKILL` response. The ledger distinguishes them (`not_eligible`, `unknown`); the model
  does not.
- A call to a withheld tool and to a tool that does not exist return the same `Unknown tool.`
  response. A call to an eligible tool that is not loaded returns `Tool not loaded.` with a hint
  that names only eligible skills.
- `search_tools` results are post-filtered to the eligible set even when the search index applies
  its own security filter.
- A skill's loaded entry lists only the tools from that skill that are eligible for this caller.

## 8. Audit

Ledger events are listed in
[architecture section 10](architecture.md#10-bounded-autonomy-and-the-evidence-ledger). For
governance reviews, the useful questions and the evidence that answers them are:

| Question | Evidence |
|---|---|
| What could the model see at step N? | `view_digest` on the nearest `skill_loaded` or `skill_unloaded` event, plus skill `content_hash` values |
| Why was a skill shown? | `trigger` on `skill_loaded` (`always_on`, `phase`, `meta_tool`) |
| Did the model try to use something it should not? | `tool_call_rejected` with `not_eligible` or `unknown`; `skill_load_rejected` |
| Did the harness ever allow a call the server refused? | `tool_call_denied_by_server`; investigate every one |
| Which catalog was in force? | `catalog_version` on `catalog_refreshed` |

Retain ledger events for the period your evidence policy requires. Hand them to the
[Evidence Ledger and Run Certificate](../../../orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md)
for tamper evidence and run certification.

## 9. Prompt injection

- **Skill bodies and references are instructions.** Only registry-published content is loaded.
  References are fixed files attached to a skill at publish time; `read_skill_reference` takes a
  name from the skill's own list, never a path or URL.
- **Tool results can try to steer loading.** A retrieved document may say "load the admin skill" or
  "call `limits.override_limit`". The loader only loads eligible skills and the gate only passes
  loaded tools, so the injected request fails with a uniform response and is recorded.
- **Injected names do not reach the ledger as text.** Names that do not match the skill or tool name
  pattern are recorded as `invalid:<hash>`.
- **Search queries are model output.** They are hashed in the ledger (`query_digest`), never logged
  raw, and cannot change the eligible set.
- **Red-team the loader.** The prompts in
  [evaluation and troubleshooting](evaluation-and-troubleshooting.md#7-red-team-prompts) cover
  withheld-tool calls, skill-name guessing and injected skill bodies.

## 10. Privacy

- Skill bodies, descriptions and references are shared across callers. They must not contain
  personal data or tenant-specific data.
- Do not record tool arguments, tool results or raw user text in ledger events or telemetry.
  Record ids, counts, versions and digests.
- Digests of short or guessable inputs can be reversed by enumeration. Use a keyed hash for query
  digests where the query may contain personal data.

## 11. Responsible AI

- **Transparency.** Tell users what the agent can do in this context. If an action is not available
  because of the autonomy profile, the agent should say it cannot do that here, not invent a reason.
- **Human oversight.** Progressive loading reduces what the model can reach for at once, but it is
  not an approval mechanism. Keep approval gates on write calls in the bounded-autonomy layer.
- **Reliability.** Measure misses and wrong-tool calls before and after every catalog change. A
  narrower view that makes the agent fail more often is not an improvement.
- **Accountability.** The ledger and the registry's content hashes show exactly which instructions
  and tools were in front of the model when it acted.
