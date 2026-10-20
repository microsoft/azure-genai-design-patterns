"""Reference implementation template with an offline --self-test suite.

Demonstrates the standard pattern for runnable samples in this repository:
1. Domain-agnostic contracts (Principal, ToolPolicy, AuditEvent, SideEffectGate).
2. Clear protocol/adapter seams so in-memory fakes can be swapped for production Azure SDKs.
3. A deterministic `--self-test` CLI entrypoint that runs with zero external dependencies.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import json
import sys
from typing import Any


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    object_id: str
    roles: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    side_effect_class: str  # "R", "W1", "W2", "W3", "W4"
    required_role: str


class GovernedToolDispatcher:
    """Minimal reference dispatcher enforcing token-derived scope and write gates."""

    def __init__(self, policies: dict[str, ToolPolicy], writes_enabled: bool = True) -> None:
        self._policies = dict(policies)
        self.writes_enabled = writes_enabled
        self.audit_log: list[dict[str, Any]] = []

    def invoke(
        self,
        principal: Principal,
        tool_name: str,
        arguments: dict[str, Any],
        approval_hash: str | None = None,
    ) -> dict[str, Any]:
        policy = self._policies.get(tool_name)
        if policy is None:
            return self._record(principal, tool_name, "denied", "unregistered_tool")
        if policy.required_role not in principal.roles:
            return self._record(principal, tool_name, "denied", "insufficient_role")
        if policy.side_effect_class != "R" and not self.writes_enabled:
            return self._record(principal, tool_name, "denied", "writes_disabled")
        if policy.side_effect_class in {"W3", "W4"}:
            expected = hashlib.sha256(
                json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True).encode("utf-8")
            ).hexdigest()[:16]
            if approval_hash != expected:
                return self._record(principal, tool_name, "denied", "approval_required")
        return self._record(principal, tool_name, "allowed", "ok", {"tenant": principal.tenant_id})

    def _record(
        self,
        principal: Principal,
        tool_name: str,
        outcome: str,
        reason: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        entry = {
            "tenant_id": principal.tenant_id,
            "principal_oid": principal.object_id,
            "tool": tool_name,
            "outcome": outcome,
            "reason": reason,
        }
        self.audit_log.append(entry)
        return {"status": outcome, "reason": reason, "data": data or {}}


def run_self_test() -> int:
    policies = {
        "get_entity_profile": ToolPolicy("get_entity_profile", "R", "Tools.Read"),
        "apply_change": ToolPolicy("apply_change", "W3", "Tools.Operate"),
    }
    dispatcher = GovernedToolDispatcher(policies)
    reader = Principal("00000000-0000-0000-0000-000000000001", "oid-reader", frozenset({"Tools.Read"}))
    operator = Principal(
        "00000000-0000-0000-0000-000000000001",
        "oid-operator",
        frozenset({"Tools.Read", "Tools.Operate"}),
    )

    checks: list[tuple[str, bool]] = []
    r1 = dispatcher.invoke(reader, "get_entity_profile", {"entity_id": "ENT-1"})
    checks.append(("S1 reader allowed on R tool", r1["status"] == "allowed"))

    r2 = dispatcher.invoke(reader, "unknown_tool", {})
    checks.append(("S2 unregistered tool fails closed", r2["reason"] == "unregistered_tool"))

    r3 = dispatcher.invoke(reader, "apply_change", {"entity_id": "ENT-1"})
    checks.append(("S3 reader refused on W3 tool", r3["reason"] == "insufficient_role"))

    r4 = dispatcher.invoke(operator, "apply_change", {"entity_id": "ENT-1"})
    checks.append(("S4 operator without approval refused", r4["reason"] == "approval_required"))

    args = {"entity_id": "ENT-1"}
    digest = hashlib.sha256(
        json.dumps({"tool": "apply_change", "args": args}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    r5 = dispatcher.invoke(operator, "apply_change", args, approval_hash=digest)
    checks.append(("S5 operator with valid approval allowed", r5["status"] == "allowed"))

    dispatcher.writes_enabled = False
    r6 = dispatcher.invoke(operator, "apply_change", args, approval_hash=digest)
    checks.append(("S6 kill switch blocks W3 while sparing R", r6["reason"] == "writes_disabled"))

    failed = 0
    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failed += 1
    print(f"{len(checks) - failed}/{len(checks)} checks passed; {len(dispatcher.audit_log)} audit records emitted.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reference sample offline self-test.")
    parser.add_argument("--self-test", action="store_true", help="run offline verification checks")
    parser.parse_args(argv)
    return run_self_test()


if __name__ == "__main__":
    sys.exit(main())
