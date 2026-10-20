"""Provision a governed MCP server into a Foundry Toolbox and bind a guardrail policy.

Reference script for Gates 1, 2 and 4 of the control matrix in
docs/threat-model-and-control-matrix.md. It is domain-agnostic: the same desired state works
for an ITSM / SecOps, FinOps / ERP or Customer Support / CRM MCP server.

What it does
------------
1. Registers the governed MCP server (samples/governed_mcp_server.py, or any MCP server that
   enforces source-side entitlement) in the Foundry private tool catalog, pinned to a version.
2. Publishes two toolsets from that server (Gate 1):
       ops-readonly-mcp   read (R) tools only
       ops-operator-mcp   read (R) and write (W) tools; every W tool requires approval
   Tools the server publishes but this script has not classified are never exposed.
3. Creates a guardrail policy and binds it to each agent deployment (Gate 4):
       UserPromptInjectionShield on user prompts, an indirect prompt-injection filter on
       ToolOutput, a PII and credential egress block on completions, and a groundedness check.
4. Validates least privilege (Gate 2): each agent identity holds only its tier's toolset role,
   and operator roles are eligible (PIM or time-bound Access Package), never standing.
5. Detects drift between the desired state and what is deployed, and reports findings.

Modes
-----
* Offline self-test (default when FOUNDRY_PROJECT_ENDPOINT is unset, or with --self-test).
  Runs everything against an in-memory control plane, injects eight kinds of drift, and
  asserts that each is detected and remediated. Standard library only.
* Live (--live, needs FOUNDRY_PROJECT_ENDPOINT and azure-identity). Without --apply it only
  prints the plan and the drift report. Toolbox, guardrail and agent-identity management
  surfaces are in preview and their payloads change: treat API_PATHS and the payload shapes
  in AzureControlPlane as placeholders to align with current documentation. The offline
  self-test is the verified part of this sample.

Usage
-----
    python provision_toolbox_and_guardrails.py                    # offline self-test
    python provision_toolbox_and_guardrails.py --json drift.json  # also write results
    python provision_toolbox_and_guardrails.py --live [--apply]   # against a Foundry project
"""
from __future__ import annotations

import argparse
import copy
import dataclasses
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional

# =============================================================================== desired state

# Tool classification is the single source of truth for Gate 1. R = read, W = write.
TOOL_CLASSES: dict[str, str] = {
    "whoami": "R",
    "list_records": "R",
    "get_record": "R",
    "get_record_activity": "R",
    "apply_change": "W",
}

READ_ROLE = "MCP.Tools.Read"
OPERATE_ROLE = "MCP.Tools.Operate"


@dataclasses.dataclass
class McpServerSpec:
    name: str
    url: str
    version: str
    audience: str
    auth: str = "agent_identity"  # or "oauth_passthrough" for on-behalf-of a signed-in user


@dataclasses.dataclass
class ToolsetSpec:
    name: str
    server: str
    server_version: Optional[str]  # None or "latest" means unpinned, which is a finding
    allowed_tools: list[str]
    require_approval: list[str]
    access_role: str  # Entra app role that entitles an identity to use this toolset


@dataclasses.dataclass
class GuardrailFilter:
    name: str
    applies_to: str  # UserPrompt | ToolOutput | Completion
    action: str  # block | block_or_refuse | flag
    enabled: bool = True
    settings: dict = dataclasses.field(default_factory=dict)


@dataclasses.dataclass
class GuardrailPolicySpec:
    name: str
    filters: list[GuardrailFilter]


@dataclasses.dataclass
class AgentSpec:
    name: str
    deployment: str
    toolset: str
    identity: str  # object ID of the Entra Agent ID (service principal) in live mode
    allowed_roles: list[str]
    eligible_only_roles: list[str]  # must never be a standing (permanent, active) assignment


def desired_state() -> dict[str, Any]:
    server = McpServerSpec(
        name="governed-records-mcp",
        url=os.getenv("MCP_SERVER_URL", "https://records-mcp.contoso.example/mcp"),
        version=os.getenv("MCP_SERVER_VERSION", "0.2.0"),
        audience=os.getenv("MCP_AUDIENCE", "api://governed-records-mcp"),
    )
    read_tools = sorted(t for t, c in TOOL_CLASSES.items() if c == "R")
    write_tools = sorted(t for t, c in TOOL_CLASSES.items() if c == "W")
    toolsets = [
        ToolsetSpec("ops-readonly-mcp", server.name, server.version, read_tools, [], READ_ROLE),
        ToolsetSpec("ops-operator-mcp", server.name, server.version, read_tools + write_tools,
                    list(write_tools), OPERATE_ROLE),
    ]
    guardrail = GuardrailPolicySpec("ops-agent-guardrail", [
        GuardrailFilter("UserPromptInjectionShield", "UserPrompt", "block"),
        GuardrailFilter("IndirectPromptInjection", "ToolOutput", "block_or_refuse"),
        GuardrailFilter("SensitiveDataEgress", "Completion", "block",
                        {"categories": ["credential", "access_token", "bank_account",
                                        "government_id", "payment_card"]}),
        GuardrailFilter("Groundedness", "Completion", "flag", {"min_score": 3}),
    ])
    agents = [
        AgentSpec("ops-reader-agent", "ops-reader-agent-prod", "ops-readonly-mcp",
                  os.getenv("READER_AGENT_ID", "agentid-ops-reader"), [READ_ROLE], []),
        AgentSpec("ops-operator-agent", "ops-operator-agent-prod", "ops-operator-mcp",
                  os.getenv("OPERATOR_AGENT_ID", "agentid-ops-operator"),
                  [READ_ROLE, OPERATE_ROLE], [OPERATE_ROLE]),
    ]
    return {"server": server, "toolsets": toolsets, "guardrail": guardrail, "agents": agents}


def fingerprint(obj: Any) -> str:
    """Stable short hash of a resource definition, used for drift detection."""
    data = dataclasses.asdict(obj) if dataclasses.is_dataclass(obj) else obj
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


# =============================================================================== control planes


class InMemoryControlPlane:
    """Offline stand-in for the Foundry project, private tool catalog and Entra ID.

    It stores exactly what the live service would return, so validators and drift detection
    run unchanged against either implementation. Every mutation is appended to self.log.
    """

    def __init__(self) -> None:
        self.servers: dict[str, dict] = {}
        self.toolsets: dict[str, ToolsetSpec] = {}
        self.guardrails: dict[str, GuardrailPolicySpec] = {}
        self.deployments: dict[str, dict] = {}
        self.assignments: dict[str, list[dict]] = {}
        self.log: list[dict] = []

    def _audit(self, op: str, target: str) -> None:
        self.log.append({"ts": round(time.time(), 3), "op": op, "target": target})

    # ---- private tool catalog (Gate 1)
    def register_server(self, spec: McpServerSpec, tools: list[str]) -> None:
        self.servers[spec.name] = {"spec": copy.deepcopy(spec), "tools": sorted(tools)}
        self._audit("register_server", f"{spec.name}@{spec.version}")

    def get_server(self, name: str) -> Optional[dict]:
        return copy.deepcopy(self.servers.get(name))

    def put_toolset(self, spec: ToolsetSpec) -> None:
        self.toolsets[spec.name] = copy.deepcopy(spec)
        self._audit("put_toolset", spec.name)

    def get_toolset(self, name: str) -> Optional[ToolsetSpec]:
        return copy.deepcopy(self.toolsets.get(name))

    # ---- guardrails (Gate 4)
    def put_guardrail(self, spec: GuardrailPolicySpec) -> None:
        self.guardrails[spec.name] = copy.deepcopy(spec)
        self._audit("put_guardrail", spec.name)

    def get_guardrail(self, name: str) -> Optional[GuardrailPolicySpec]:
        return copy.deepcopy(self.guardrails.get(name))

    # ---- agent deployments
    def bind(self, deployment: str, toolsets: list[str], guardrail: Optional[str]) -> None:
        self.deployments[deployment] = {"toolsets": sorted(toolsets), "guardrail": guardrail}
        self._audit("bind", f"{deployment}:{','.join(sorted(toolsets))}:{guardrail}")

    def get_deployment(self, deployment: str) -> dict:
        return copy.deepcopy(self.deployments.get(deployment, {"toolsets": [], "guardrail": None}))

    # ---- identity (Gate 2). type is "active" (standing) or "eligible" (PIM / time-bound).
    def role_assignments(self, identity: str) -> list[dict]:
        return copy.deepcopy(self.assignments.get(identity, []))

    def set_role_assignments(self, identity: str, assignments: list[dict]) -> None:
        self.assignments[identity] = copy.deepcopy(assignments)
        self._audit("set_role_assignments", identity)


API_VERSION = os.getenv("FOUNDRY_API_VERSION", "2025-11-15-preview")
API_PATHS = {  # Placeholders: align with the current Foundry REST reference before use.
    "server": "/toolbox/mcpServers/{name}",
    "toolset": "/toolbox/toolsets/{name}",
    "guardrail": "/guardrails/{name}",
    "deployment": "/agents/{name}/configuration",
}
GRAPH = "https://graph.microsoft.com/v1.0"


def _from(cls: Any, data: dict) -> Any:
    return cls(**{f.name: data[f.name] for f in dataclasses.fields(cls) if f.name in data})


class AzureControlPlane:
    """Live adapter with the same interface as InMemoryControlPlane.

    Reads always run. Writes run only with --apply; otherwise they are recorded as a plan.
    Role assignments are never written: identity changes go through Access Packages and PIM.
    """

    def __init__(self, endpoint: str, apply: bool) -> None:
        try:
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:
            raise SystemExit("--live needs: pip install azure-identity") from exc
        self.endpoint, self.apply, self.log = endpoint.rstrip("/"), apply, []
        self._cred = DefaultAzureCredential()

    def _call(self, method: str, url: str, body: Any = None,
              scope: str = "https://ai.azure.com/.default") -> Optional[dict]:
        if method != "GET" and not self.apply:
            self.log.append({"op": f"plan:{method}", "target": url})
            return None
        data = None if body is None else json.dumps(body, default=str).encode()
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self._cred.get_token(scope).token,
            "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        if method != "GET":
            self.log.append({"op": method, "target": url})
        return json.loads(raw) if raw else {}

    def _url(self, kind: str, name: str) -> str:
        return f"{self.endpoint}{API_PATHS[kind].format(name=name)}?api-version={API_VERSION}"

    def register_server(self, spec: McpServerSpec, tools: list[str]) -> None:
        self._call("PUT", self._url("server", spec.name), {**dataclasses.asdict(spec), "tools": tools})

    def get_server(self, name: str) -> Optional[dict]:
        d = self._call("GET", self._url("server", name))
        return None if not d else {"spec": _from(McpServerSpec, d), "tools": sorted(d.get("tools", []))}

    def put_toolset(self, spec: ToolsetSpec) -> None:
        self._call("PUT", self._url("toolset", spec.name), dataclasses.asdict(spec))

    def get_toolset(self, name: str) -> Optional[ToolsetSpec]:
        d = self._call("GET", self._url("toolset", name))
        return _from(ToolsetSpec, d) if d else None

    def put_guardrail(self, spec: GuardrailPolicySpec) -> None:
        self._call("PUT", self._url("guardrail", spec.name), dataclasses.asdict(spec))

    def get_guardrail(self, name: str) -> Optional[GuardrailPolicySpec]:
        d = self._call("GET", self._url("guardrail", name))
        if not d:
            return None
        return GuardrailPolicySpec(d["name"], [_from(GuardrailFilter, f) for f in d.get("filters", [])])

    def bind(self, deployment: str, toolsets: list[str], guardrail: Optional[str]) -> None:
        self._call("PATCH", self._url("deployment", deployment),
                   {"toolsets": sorted(toolsets), "guardrail": guardrail})

    def get_deployment(self, deployment: str) -> dict:
        d = self._call("GET", self._url("deployment", deployment)) or {}
        return {"toolsets": sorted(d.get("toolsets", [])), "guardrail": d.get("guardrail")}

    def role_assignments(self, identity: str) -> list[dict]:
        graph = "https://graph.microsoft.com/.default"
        d = self._call("GET", f"{GRAPH}/servicePrincipals/{identity}/appRoleAssignments", scope=graph)
        out = []
        for a in (d or {}).get("value", []):
            sp = self._call("GET", f"{GRAPH}/servicePrincipals/{a['resourceId']}?$select=appRoles",
                            scope=graph) or {}
            names = {r["id"]: r["value"] for r in sp.get("appRoles", [])}
            out.append({"role": names.get(a["appRoleId"], a["appRoleId"]), "type": "active"})
        # Direct app role assignments are standing by definition. Eligible access (PIM for Groups,
        # time-bound Access Packages) is read from identityGovernance; add it here if you use it.
        return out

    def set_role_assignments(self, identity: str, assignments: list[dict]) -> None:
        self.log.append({"op": "manual:role_assignments", "target": identity,
                         "note": "request through an Access Package or PIM; not applied by script"})


# =============================================================================== provision and drift


def provision(plane: Any, state: dict, published_tools: list[str]) -> None:
    """Idempotently converge the control plane to the desired state."""
    unclassified = set(published_tools) - set(TOOL_CLASSES)
    plane.register_server(state["server"], published_tools)
    for ts in state["toolsets"]:
        leaked = unclassified & set(ts.allowed_tools)
        if leaked:  # Deny by default: a tool nobody classified is never exposed.
            raise ValueError(f"{ts.name} would expose unclassified tools {sorted(leaked)}")
        plane.put_toolset(ts)
    plane.put_guardrail(state["guardrail"])
    for agent in state["agents"]:
        plane.bind(agent.deployment, [agent.toolset], state["guardrail"].name)
        plane.set_role_assignments(agent.identity, [
            {"role": r, "type": "eligible" if r in agent.eligible_only_roles else "active"}
            for r in agent.allowed_roles])


STRENGTH = {"flag": 1, "block_or_refuse": 2, "block": 3}


def detect_drift(plane: Any, state: dict) -> list[dict]:
    """Compare deployed state with desired state and with the Gate 1/2/4 invariants."""
    findings: list[dict] = []

    def add(code: str, resource: str, detail: str, severity: str = "high") -> None:
        findings.append({"code": code, "severity": severity, "resource": resource, "detail": detail})

    server = state["server"]
    srv = plane.get_server(server.name)
    if srv is None:
        add("server_not_registered", server.name, "MCP server is not in the private tool catalog")
    else:
        if srv["spec"].version != server.version:
            add("server_version_mismatch", server.name,
                f"catalog has {srv['spec'].version}, desired {server.version}")
        extra = sorted(set(srv["tools"]) - set(TOOL_CLASSES))
        if extra:
            add("unclassified_tool_published", server.name,
                f"server publishes {extra}; classify R or W before any toolset can expose it", "medium")

    for want in state["toolsets"]:
        got = plane.get_toolset(want.name)
        if got is None:
            add("toolset_missing", want.name, "toolset not found")
            continue
        before = len(findings)
        if got.server_version in (None, "", "latest"):
            add("toolset_unpinned", want.name, "server_version is not pinned")
        elif got.server_version != server.version:
            add("server_version_mismatch", want.name, f"pinned {got.server_version}, desired {server.version}")
        unknown = sorted(t for t in got.allowed_tools if t not in TOOL_CLASSES)
        writes = sorted(t for t in got.allowed_tools if TOOL_CLASSES.get(t) == "W")
        if unknown:
            add("unclassified_tool_exposed", want.name, f"exposes unclassified tools {unknown}")
        if want.access_role == READ_ROLE and writes:
            add("write_tool_in_readonly_toolset", want.name, f"read-only toolset exposes {writes}")
        no_approval = sorted(set(writes) - set(got.require_approval))
        if no_approval:
            add("write_tool_without_approval", want.name, f"{no_approval} do not require approval")
        if len(findings) == before and fingerprint(got) != fingerprint(want):
            add("toolset_definition_drift", want.name, "differs from desired state", "medium")

    policy = state["guardrail"]
    deployed = plane.get_guardrail(policy.name)
    if deployed is None:
        add("guardrail_missing", policy.name, "guardrail policy not found")
    else:
        have = {(f.name, f.applies_to): f for f in deployed.filters}
        for f in policy.filters:
            h = have.get((f.name, f.applies_to))
            if (h is None or not h.enabled or STRENGTH.get(h.action, 0) < STRENGTH[f.action]
                    or h.settings != f.settings):
                add("guardrail_filter_weakened", policy.name,
                    f"{f.name} on {f.applies_to} is missing, disabled, weaker or reconfigured")

    for agent in state["agents"]:
        dep = plane.get_deployment(agent.deployment)
        if dep["toolsets"] != [agent.toolset]:
            add("deployment_toolset_mismatch", agent.deployment,
                f"bound to {dep['toolsets']}, desired [{agent.toolset}]")
        if dep["guardrail"] != policy.name:
            add("guardrail_not_bound", agent.deployment, f"guardrail is {dep['guardrail']!r}")
        held = plane.role_assignments(agent.identity)
        for a in held:
            if a["role"] not in agent.allowed_roles:
                add("excess_role", agent.identity, f"{agent.name} holds {a['role']}")
            elif a["role"] in agent.eligible_only_roles and a["type"] != "eligible":
                add("standing_privileged_assignment", agent.identity,
                    f"{a['role']} is {a['type']}; it must be eligible or time-bound")
        missing = sorted(set(agent.allowed_roles) - {a["role"] for a in held})
        if missing:
            add("missing_role", agent.identity, f"{agent.name} lacks {missing}", "low")
    return findings


# =============================================================================== offline self-test

UNCLASSIFIED_TOOL = "export_all_records"


def _op(p: InMemoryControlPlane, s: dict) -> str:
    return s["agents"][1].identity


def _drift_cases() -> list[tuple[str, str, Any]]:
    """(case id, finding code that must be detected, mutation applied to a converged plane)."""

    def server_upgraded(p, s):
        p.servers[s["server"].name]["spec"].version = "0.3.0"

    def unpin(p, s):
        p.toolsets["ops-operator-mcp"].server_version = "latest"

    def write_in_readonly(p, s):
        p.toolsets["ops-readonly-mcp"].allowed_tools.append("apply_change")

    def drop_approval(p, s):
        p.toolsets["ops-operator-mcp"].require_approval.clear()

    def expose_unclassified(p, s):
        p.toolsets["ops-operator-mcp"].allowed_tools.append(UNCLASSIFIED_TOOL)

    def publish_unclassified(p, s):
        p.servers[s["server"].name]["tools"].append(UNCLASSIFIED_TOOL)

    def disable_xpia(p, s):
        next(f for f in p.guardrails["ops-agent-guardrail"].filters
             if f.applies_to == "ToolOutput").enabled = False

    def unbind_guardrail(p, s):
        p.deployments["ops-operator-agent-prod"]["guardrail"] = None

    def reader_on_operator(p, s):
        p.deployments["ops-reader-agent-prod"]["toolsets"] = ["ops-operator-mcp"]

    def standing_operate(p, s):
        next(a for a in p.assignments[_op(p, s)] if a["role"] == OPERATE_ROLE)["type"] = "active"

    def reader_gets_operate(p, s):
        p.assignments[s["agents"][0].identity].append({"role": OPERATE_ROLE, "type": "active"})

    return [
        ("D01", "server_version_mismatch", server_upgraded),
        ("D02", "toolset_unpinned", unpin),
        ("D03", "write_tool_in_readonly_toolset", write_in_readonly),
        ("D04", "write_tool_without_approval", drop_approval),
        ("D05", "unclassified_tool_exposed", expose_unclassified),
        ("D06", "unclassified_tool_published", publish_unclassified),
        ("D07", "guardrail_filter_weakened", disable_xpia),
        ("D08", "guardrail_not_bound", unbind_guardrail),
        ("D09", "deployment_toolset_mismatch", reader_on_operator),
        ("D10", "standing_privileged_assignment", standing_operate),
        ("D11", "excess_role", reader_gets_operate),
    ]


def run_self_test() -> dict:
    state = desired_state()
    plane = InMemoryControlPlane()
    published = sorted(TOOL_CLASSES)
    results: list[dict] = []

    def check(case: str, ok: bool, detail: str) -> None:
        results.append({"case": case, "passed": bool(ok), "detail": detail})
        print(f"  {'PASS' if ok else 'FAIL'} {case:<4} {detail}")

    print("Desired state")
    for ts in state["toolsets"]:
        print(f"  toolset {ts.name:<17} pinned {ts.server_version}  tools={ts.allowed_tools}"
              f"  approval={ts.require_approval}  role={ts.access_role}")
    print(f"  guardrail {state['guardrail'].name}: "
          + ", ".join(f"{f.name}@{f.applies_to}={f.action}" for f in state["guardrail"].filters))

    print("Provision and baseline")
    provision(plane, state, published)
    baseline = detect_drift(plane, state)
    check("P01", not baseline, f"converged with {len(baseline)} findings after provisioning")
    check("P02", "apply_change" not in plane.get_toolset("ops-readonly-mcp").allowed_tools,
          "read-only toolset exposes no W tools")
    for agent in state["agents"]:
        held = sorted(f"{a['role']}({a['type']})" for a in plane.role_assignments(agent.identity))
        check("P03", held == sorted(f"{r}({'eligible' if r in agent.eligible_only_roles else 'active'})"
                                    for r in agent.allowed_roles),
              f"{agent.name} least privilege: {held}")
    bad = desired_state()
    bad["toolsets"][1].allowed_tools.append(UNCLASSIFIED_TOOL)
    try:
        provision(InMemoryControlPlane(), bad, published + [UNCLASSIFIED_TOOL])
        check("P04", False, "provisioning accepted an unclassified tool")
    except ValueError as exc:
        check("P04", True, f"provisioning refused: {exc}")

    print("Drift injection, detection and remediation")
    for case, code, mutate in _drift_cases():
        mutate(plane, state)
        found = detect_drift(plane, state)
        codes = sorted({f["code"] for f in found})
        detected = code in codes
        provision(plane, state, sorted(plane.servers[state["server"].name]["tools"]))
        after = detect_drift(plane, state)
        high_after = [f for f in after if f["severity"] == "high"]
        if code == "unclassified_tool_published":
            # Deny by default: remediation cannot classify a tool. The finding stays open for a
            # human decision, and no toolset may expose the tool meanwhile.
            exposed = any(UNCLASSIFIED_TOOL in plane.get_toolset(t.name).allowed_tools
                          for t in state["toolsets"])
            ok = detected and not high_after and not exposed
            plane.servers[state["server"].name]["tools"].remove(UNCLASSIFIED_TOOL)
        else:
            ok = detected and not after
        check(case, ok, f"{code:<31} detected={detected} findings={codes} remaining={len(after)}")

    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} checks passed; {len(plane.log)} control-plane operations logged")
    return {"mode": "offline", "passed": passed, "failed": len(results) - passed,
            "results": results, "operations": plane.log}


def run_live(endpoint: str, apply: bool) -> dict:
    state = desired_state()
    plane = AzureControlPlane(endpoint, apply)
    before = detect_drift(plane, state)
    provision(plane, state, sorted(TOOL_CLASSES))  # recorded as a plan unless --apply
    after = detect_drift(plane, state) if apply else before
    for f in after:
        print(f"  [{f['severity']}] {f['code']} {f['resource']}: {f['detail']}")
    for op in plane.log:
        print(f"  {op['op']} {op['target']}")
    high = [f for f in after if f["severity"] == "high"]
    return {"mode": "live-apply" if apply else "live-plan", "findings_before": before,
            "findings_after": after, "operations": plane.log, "failed": len(high)}


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Provision Toolbox toolsets and guardrails; detect drift.")
    ap.add_argument("--self-test", action="store_true", help="force the offline self-test")
    ap.add_argument("--live", action="store_true", help="use FOUNDRY_PROJECT_ENDPOINT")
    ap.add_argument("--apply", action="store_true", help="with --live, apply instead of plan")
    ap.add_argument("--json", metavar="PATH", help="write results to a JSON file")
    args = ap.parse_args(argv)
    endpoint = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if args.live and not endpoint:
        print("--live needs FOUNDRY_PROJECT_ENDPOINT", file=sys.stderr)
        return 2
    if args.self_test or not (args.live or endpoint):
        result = run_self_test()
    else:
        result = run_live(endpoint, args.apply)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
    return 1 if result["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
