# Evaluation and Troubleshooting

This document covers release gates for hosted agent versions, negative tests that prove the security posture, session diagnostics, a troubleshooting runbook and an operational runbook for rotation, rollback, draining and orphan cleanup. Evaluation methodology beyond release gates (datasets, graders, online evaluation) is covered in the [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md).

Every command and API shape referenced here is a preview detail. Use the current SDK or REST reference from Microsoft Learn; the steps are expressed as intent, not as exact commands.

## Release gates

All gates run on the in-network runner or job. A gate that runs from outside the network fails with 403 and proves nothing about the agent.

| Gate | Stage | What it proves | Pass criteria | On failure |
|---|---|---|---|---|
| Build and dependency pin check | Build | Reproducible image from an approved base | Base image on the approved list; dependencies pinned | Stop |
| Vulnerability and secret scan | Scan | No known critical issues or embedded secrets | No unaccepted critical findings; no secrets | Stop |
| Signature verification (optional) | Sign | The digest about to be deployed is the one the pipeline built | Signature verifies | Stop |
| Role verification | Grant or verify roles | The runtime identity has exactly the expected data-plane roles | Expected set present; no control-plane roles | Stop; report missing roles by name |
| Definition accepted | Create version | The version definition is valid | Version created and inspectable | Stop |
| Readiness | Smoke | The session sandbox starts and the server accepts a turn | A pinned session reaches ACTIVE and a minimal turn succeeds within a bounded retry budget | Roll back |
| Dependency smoke | Smoke | Model, Cosmos DB, tools and telemetry work with the runtime identity | A scripted turn that touches each dependency returns the expected markers | Roll back |
| Version assertion | Smoke | The turn ran on the new version | Response or span reports the new version | Roll back |
| Idempotency check | Smoke | Replays do not duplicate side effects | The same idempotency key twice yields one side effect and a replay marker | Roll back |
| Telemetry check | Smoke | Spans arrive with correlation fields | A span with the smoke correlation id and version is queryable | Warn in test, stop in production |
| Evaluation gate | Evaluate | Quality, groundedness and safety are within thresholds | Scores at or above the thresholds agreed with the business owner | Roll back |
| Approval | Promote | A human accepts the release | Approver is not the author | Hold |

### Smoke turn design

- Use a dedicated smoke prompt that exercises each dependency and returns deterministic markers, for example `MODEL_OK`, `STATE_OK`, `TOOL_OK`, plus the version.
- Send a fixed correlation id prefix (for example `smoke-<build-id>`) so telemetry queries can find the spans.
- Pin the smoke session to the new version and end it explicitly afterwards. A smoke test that leaves sessions behind is a session leak in every pipeline run.
- Classify failures: placement (403 public access), role (401 from a dependency, 403 authorization), readiness (424 or stall within budget), contract (400 or 404), behaviour (wrong markers). Report the class, not just "smoke failed".

## Negative tests

Negative tests prove that the controls work. Run them per environment after infrastructure changes and on a schedule.

| Test | Run from | Expected | If it unexpectedly succeeds |
|---|---|---|---|
| Invoke from outside the network | Public build agent or laptop | 403, public access disabled | Public network access is on. Treat as an incident |
| Invoke without a token | Inside the network | 401 | Authentication is misconfigured |
| Invoke with an identity that has no project role | Inside the network | 403 | Role scope is too broad |
| Runtime calls a dependency it has no role for (diagnostic version) | Sandbox | 401 or 403 from the dependency | Over-privileged runtime identity |
| Runtime resolves an external name not on the allowlist (diagnostic version) | Sandbox | Blocked or not resolved | DNS egress is open |
| Runtime connects to a non-allowlisted host (diagnostic version) | Sandbox | Blocked and logged at the firewall | Egress is open |
| Key-based access to Cosmos DB | Inside the network | Rejected because local auth is disabled | Local auth is enabled |
| Spoofed caller header in metadata | Inside the network | Tools reject the request or ignore the hint | Tools trust forwarded context |

Deploy diagnostic versions only to non-production projects, and retire them after the test.

## Session diagnostics

When a session misbehaves, collect these in order. Each item names the intent; use the current SDK or REST operation for it.

| Step | What to inspect | What it tells you |
|---|---|---|
| 1 | Agent definition and version: image digest, protocol, environment variable names (not values), resources, identity blueprint and instance principal | Whether the version you think is running is the one defined, and which principal it authenticates as |
| 2 | Session list for the agent: session id, status, version, creation and last activity | Whether the session exists, which version it is on, and whether there is a leak |
| 3 | Session status for the failing session | CREATED, ACTIVE, IDLE, EXPIRED, CLOSED or FAILED (names vary by API version) |
| 4 | Session log stream | Container start, process crash, port binding, dependency errors, your structured logs |
| 5 | Traces in Application Insights filtered by correlation id, session id and version | Which turn failed, which dependency, the error class, retry count, idempotent replays |
| 6 | Firewall logs for the agent subnet | Blocked egress, including identity and telemetry endpoints |
| 7 | Role assignments for the instance principal | Missing data-plane roles |
| 8 | Registry: digest exists, pull identity has AcrPull, retention did not delete it | Image pull failures |

Notes from preview testing:

- Session listing and the session log stream were the most useful diagnostics. Silence the SDK's HTTP logger in scripts so diagnostic output is readable **(observed in preview; verify against current docs)**.
- A session whose status is ACTIVE may still reject the first invoke while your server starts. Look for the process start and port-binding lines in the log stream before concluding the image is broken.
- Write logs to standard output with unbuffered output (for example `PYTHONUNBUFFERED=1` for Python), or the log stream may show nothing before a crash.

## Troubleshooting runbook

| Symptom | First check | Likely cause | Fix |
|---|---|---|---|
| 403 with a public-access message from the pipeline | Where did the call run? | Outside the network | Move the step to the in-network runner |
| 403 without a public-access message | Role assignments of the caller on the project | Missing project data-plane role | Grant the project role (verify name) to the caller or pipeline identity |
| 401 from the model client inside the agent | Instance principal roles on the Foundry account | Missing Cognitive Services OpenAI User | Grant it; wait for propagation; rerun smoke |
| 401 from the Cosmos DB client | Cosmos DB SQL role assignments | Missing data-plane role, or an Azure RBAC role was assigned instead of a Cosmos DB SQL role | Assign the Cosmos DB built-in data role through Cosmos DB role assignments |
| 401 with roles in place | Decoded token `aud` | Token for the wrong audience | Request and cache tokens per audience |
| Token acquisition errors in the log stream | Firewall logs for identity endpoints | Egress to identity endpoints blocked or TLS-inspected | Allowlist identity endpoints and exempt them from inspection |
| Session FAILED immediately | Log stream; registry | Image pull failure, bad entrypoint, crash at import | Fix the pull role or digest; fix the entrypoint vector; check missing modules |
| Session ACTIVE but first invoke returns 424 | Log stream timestamps | Server not ready yet | Gate on a real turn with bounded retry |
| Invoke hangs on the Responses route | Request body | Invocations-style session field sent to Responses | Use the protocol's own continuity fields |
| Duplicate records after retries | Idempotency records | Key missing or not forwarded | Send the key in a forwarded header or metadata; check before acting |
| Session count rising | Session list by creation time | Unpinned retries, clients not ending sessions, smoke tests leaking | Pin, end sessions, fix smoke cleanup, run the sweeper |
| Old behaviour after rollout | `agent.version` in spans | Existing sessions still on the previous version | Drain sessions on the old version |
| No telemetry | Exporter errors in the log stream; firewall logs | Ingestion endpoint blocked, no flush on shutdown, missing ingestion role | Allowlist, flush on shutdown, grant the role |
| Telemetry contains prompts | Content-recording setting | Content capture enabled | Disable; purge per data policy; review access |
| Configuration from a previous run appears | Runner job definition | Job environment variables persisted | Pass the full environment on every execution |
| Pipeline changed the wrong subscription | Script preamble | Active subscription not pinned | Pin the subscription in every script |
| Registry build task fails | Build logs | Build service cannot reach the private registry | Build on the in-network runner |

## Operational runbook

### Rotate

Managed identities have no secrets to rotate. Rotation covers the remaining credentials and the images themselves.

1. **Base image refresh.** On the scheduled refresh, rebuild from the updated approved base, run the full pipeline and promote like any release.
2. **Secrets for non-Entra systems.** Rotate in Key Vault. The container re-reads secrets on a short cache lifetime; no redeploy is required. Verify with a smoke turn that touches the dependency.
3. **Signing keys (if signing is used).** Rotate in the signing service; re-sign the active and rollback digests; verify the pipeline verification step still passes.
4. **Federated credentials for the pipeline.** Review the federated subject (repository, environment, branch) on a schedule; remove stale subjects.

### Roll back

1. Read the recorded rollback target (previous active version and its digest) from the release record.
2. Confirm the digest still exists in the registry.
3. Re-activate the recorded version. Pin new sessions to it.
4. Run the smoke turn against the rollback version from inside the network.
5. Drain sessions that are still on the bad version (next section).
6. Record the rollback, the reason and the affected window. Keep the bad version available for analysis, then retire it.

### Drain sessions

1. Stop routing new conversations to the version being drained (pin new sessions to the target version).
2. List sessions on the old version.
3. Let active conversations finish within a drain window agreed with the business owner, or end them immediately for a security fix.
4. End the remaining sessions explicitly. The client creates a new pinned session on the next turn and rehydrates from Cosmos DB.
5. Confirm in telemetry that no new spans report the old version.

### Clean up orphans

1. Run the sweeper inside the network on a schedule, as a dry run by default.
2. It lists sessions, computes the time since last activity from platform timestamps, and selects sessions beyond the policy window.
3. It skips sessions on the keep list (sessions under investigation, smoke sessions in progress).
4. It reports the candidate list and, with an explicit apply flag, ends them.
5. It also flags sessions whose only activity is heartbeats with no user turns, which is the keep-warm pattern ([ADR 0003](decisions/0003-explicit-session-lifecycle-no-keep-warm.md)).
6. Alert when the candidate count grows between runs; that indicates a client that is not ending sessions.

The simulator demonstrates the sweeper and the keep-warm detector in [`samples/hosted_agent_ops_sim.py`](../samples/hosted_agent_ops_sim.py).

## Related

- [Architecture](architecture.md), especially [failure modes](architecture.md#15-failure-modes)
- [Authentication and governance](authentication-and-governance.md)
- [Deployment and operations](deployment-and-operations.md)
- [Secured Foundry Landing Zone: network and DNS troubleshooting](../../../landing-zones-and-infra/secured-foundry-landing-zone/docs/network-and-dns-troubleshooting.md)
- [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md)
