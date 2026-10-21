"""Bounded autonomy governor.

Server-selected autonomy profiles, harness-enforced budgets, allowed-action
envelopes, plan-before-act and fail-closed escalation, in one stdlib-only file.

Everything here is a simulation: the policy store, decision sink, clock and tools
are in-memory fakes. Budget values are illustrative simulation parameters, not
recommendations. Size real budgets from your own evaluation runs.

Usage (Python 3.10+):
    python autonomy_governor.py                       # demo on the default domain
    python autonomy_governor.py --json                # demo records + final state as JSON
    python autonomy_governor.py --self-test           # every scenario x every domain pack
    python autonomy_governor.py --list                # list scenarios
    python autonomy_governor.py --scenario NAME --domain NAME
    python autonomy_governor.py --list-domains
    python autonomy_governor.py --policy --domain NAME
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

SCHEMA_DECISION = "bounded-autonomy.policy-decision/v1"
SCHEMA_STATE = "bounded-autonomy.runtime-state/v1"
SCHEMA_ESCALATION = "bounded-autonomy.escalation/v1"
SCHEMA_POLICY = "bounded-autonomy.policy/v1"

ALLOW, DENY, ESCALATE = "allow", "deny", "escalate"

READ = "read"
REVERSIBLE_WRITE = "reversible_write"
IRREVERSIBLE = "irreversible_external"
UNKNOWN_CLASS = "unknown"

RULE_ALLOW, RULE_PROPOSE, RULE_ESCALATE, RULE_DENY = "allow", "propose", "escalate", "deny"

PROFILE_ORDER = ("observe-only", "propose", "act-with-approval", "act")

LABEL_RANK = {"public": 0, "internal": 1, "confidential": 2, "regulated": 3}

RUNNING, ESCALATED, SUSPENDED = "running", "escalated", "suspended"
COMPLETED, STOPPED, REFUSED, CONTINUED = "completed", "stopped", "refused", "continued"

PHASE_PLAN, PHASE_ACT, PHASE_VERIFY, PHASE_FINALIZE = "plan", "act", "verify", "finalize"

APPROVE_STEP = "approve-step"
CLARIFY_PLAN = "clarify-plan"
RETRY_WITH_NEW_PLAN = "retry-with-new-plan"
CONTINUE_IN_NEW_RUN = "continue-in-new-run"
STOP = "stop"


class R:
    """Reason codes. Stable strings: dashboards, alerts and audits key on them."""

    IN_ENVELOPE = "IN_ENVELOPE"
    APPROVAL_GRANTED_RESUME = "APPROVAL_GRANTED_RESUME"
    TOOL_NOT_IN_PROFILE = "TOOL_NOT_IN_PROFILE"
    SIDE_EFFECT_EXCEEDS_PROFILE = "SIDE_EFFECT_EXCEEDS_PROFILE"
    PROPOSE_ONLY = "PROPOSE_ONLY"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    ARG_CONSTRAINT_VIOLATED = "ARG_CONSTRAINT_VIOLATED"
    DATA_BOUNDARY_VIOLATED = "DATA_BOUNDARY_VIOLATED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    VERIFIER_PASSED = "VERIFIER_PASSED"
    VERIFIER_FAILED = "VERIFIER_FAILED"
    VERIFIER_FAILURES_EXCEEDED = "VERIFIER_FAILURES_EXCEEDED"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    POLICY_RESTORED = "POLICY_RESTORED"
    AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
    AUDIT_RESTORED = "AUDIT_RESTORED"
    PROFILE_REVOKED = "PROFILE_REVOKED"
    PROFILE_ASSIGNED = "PROFILE_ASSIGNED"
    NO_PROFILE_ASSIGNED = "NO_PROFILE_ASSIGNED"
    PLAN_REQUIRED = "PLAN_REQUIRED"
    PLAN_ACCEPTED = "PLAN_ACCEPTED"
    STEP_NOT_IN_PLAN = "STEP_NOT_IN_PLAN"
    STEP_ALREADY_EXECUTED = "STEP_ALREADY_EXECUTED"
    RUN_NOT_RUNNING = "RUN_NOT_RUNNING"
    RUN_NOT_SUSPENDED = "RUN_NOT_SUSPENDED"
    RUN_COMPLETED = "RUN_COMPLETED"
    ESCALATION_NOT_PENDING = "ESCALATION_NOT_PENDING"
    RESOLUTION_NOT_ALLOWED = "RESOLUTION_NOT_ALLOWED"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    APPROVAL_MISMATCH = "APPROVAL_MISMATCH"
    SELF_APPROVAL = "SELF_APPROVAL"
    APPROVER_NOT_ELIGIBLE = "APPROVER_NOT_ELIGIBLE"
    APPROVAL_GRANTED = "APPROVAL_GRANTED"
    PLAN_CLARIFIED = "PLAN_CLARIFIED"
    RETRY_GRANTED = "RETRY_GRANTED"
    CONTINUATION_GRANTED = "CONTINUATION_GRANTED"
    STOPPED_BY_APPROVER = "STOPPED_BY_APPROVER"


def canonical_digest(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Principal:
    """Caller identity. In production, build this from validated Microsoft Entra
    token claims (subject, app roles, tenant), never from the prompt or request body."""

    subject: str
    roles: tuple[str, ...] = ()
    tenant: str = "tenant-a"


@dataclass(frozen=True)
class Budgets:
    # Illustrative simulation values only; not recommendations.
    attempts: int = 3
    tool_calls: int = 12
    tokens: int = 40_000
    wall_clock_seconds: int = 900
    cost: float | None = None


@dataclass(frozen=True)
class AutonomyProfile:
    name: str
    allowed_tools: tuple[str, ...]
    side_effect_rules: dict[str, str]
    arg_constraints: dict[str, dict[str, dict[str, Any]]]
    data_boundary: dict[str, tuple[str, ...]]
    budgets: Budgets
    max_consecutive_verifier_failures: int = 2
    min_plan_confidence: float = 0.7
    max_continuations: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyDocument:
    policy_id: str
    version: str
    tool_classes: dict[str, str]
    profiles: dict[str, AutonomyProfile]
    role_profiles: dict[str, str]
    workload_caps: dict[str, str]
    approver_role: str = "Autonomy.Approver"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA_POLICY,
            "policy_id": self.policy_id,
            "version": self.version,
            "tool_classes": self.tool_classes,
            "role_profiles": self.role_profiles,
            "workload_caps": self.workload_caps,
            "approver_role": self.approver_role,
            "profiles": {name: p.to_dict() for name, p in self.profiles.items()},
        }


@dataclass(frozen=True)
class DomainPack:
    key: str
    organization: str
    workload: str
    read_tool: str
    read_args: dict[str, Any]
    write_tool: str
    scope_arg: str
    allowed_scopes: tuple[str, ...]
    out_of_scope: str
    amount_arg: str
    amount_max: int
    irreversible_tool: str
    irreversible_args: dict[str, Any]
    irreversible_destination: str
    unlisted_tool: str
    regulated_example: str


@dataclass(frozen=True)
class PlannedStep:
    step_id: str
    tool: str
    args: dict[str, Any]
    destination: str = "internal"
    input_labels: tuple[str, ...] = ("internal",)

    @property
    def data_class(self) -> str:
        # Unknown labels are treated as the most restrictive class.
        ranks = [LABEL_RANK.get(label, LABEL_RANK["regulated"]) for label in self.input_labels]
        top = max(ranks) if ranks else LABEL_RANK["regulated"]
        return next(name for name, rank in LABEL_RANK.items() if rank == top)

    def digest(self) -> str:
        return canonical_digest({
            "step_id": self.step_id,
            "tool": self.tool,
            "args": self.args,
            "destination": self.destination,
            "input_labels": sorted(self.input_labels),
        })

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "args": self.args,
            "destination": self.destination,
            "data_class": self.data_class,
            "step_digest": self.digest(),
        }


@dataclass(frozen=True)
class Plan:
    steps: tuple[PlannedStep, ...]
    confidence: float = 0.9
    ambiguities: tuple[str, ...] = ()
    requested_profile: str | None = None  # recorded, never used to select a profile
    human_confirmed: bool = False

    def digest(self) -> str:
        return canonical_digest({
            "steps": [s.digest() for s in self.steps],
            "confidence": self.confidence,
            "ambiguities": list(self.ambiguities),
        })


@dataclass
class Decision:
    outcome: str
    reasons: tuple[str, ...]
    record: dict[str, Any]
    packet: dict[str, Any] | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class Resolution:
    run_id: str
    escalation_id: str
    action: str
    policy_version: str
    subject_digest: str
    note: str = ""


@dataclass(frozen=True)
class EnvelopeResult:
    verdict: str
    reasons: tuple[str, ...]
    side_effect_class: str
    violations: tuple[str, ...] = ()


@dataclass
class RuntimeState:
    run_id: str
    principal: Principal
    workload: str
    profile: str
    policy_id: str
    policy_version: str
    budgets: Budgets
    started_at: datetime
    status: str = RUNNING
    phase: str = PHASE_PLAN
    attempts_used: int = 0
    tool_calls_used: int = 0
    tokens_used: int = 0
    cost_used: float = 0.0
    consecutive_verifier_failures: int = 0
    paused_seconds: float = 0.0
    paused_at: datetime | None = None
    plan_digest: str | None = None
    plan_accepted: bool = False
    planned_steps: dict[str, PlannedStep] = field(default_factory=dict)
    pending_plan: Plan | None = None
    completed_steps: list[str] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    approval_grants: set[str] = field(default_factory=set)
    pending_escalation: dict[str, Any] | None = None
    suspended_from: str | None = None
    suspended_reason: str | None = None
    parent_run_id: str | None = None
    continuation_index: int = 0
    checkpoint: dict[str, Any] | None = None
    last_decision_id: str | None = None

    def active_seconds(self, now: datetime) -> int:
        # Time spent waiting on a human or a suspended dependency is not charged.
        paused = self.paused_seconds
        if self.paused_at is not None:
            paused += (now - self.paused_at).total_seconds()
        return max(0, int((now - self.started_at).total_seconds() - paused))

    def budget_remaining(self, now: datetime) -> dict[str, Any]:
        b = self.budgets
        remaining: dict[str, Any] = {
            "attempts": b.attempts - self.attempts_used,
            "tool_calls": b.tool_calls - self.tool_calls_used,
            "tokens": b.tokens - self.tokens_used,
            "wall_clock_seconds": b.wall_clock_seconds - self.active_seconds(now),
        }
        if b.cost is not None:
            remaining["cost"] = round(b.cost - self.cost_used, 4)
        return remaining

    def snapshot(self, now: datetime) -> dict[str, Any]:
        return {
            "schema": SCHEMA_STATE,
            "run_id": self.run_id,
            "parent_run_id": self.parent_run_id,
            "continuation_index": self.continuation_index,
            "status": self.status,
            "phase": self.phase,
            "profile": self.profile,
            "policy": {"policy_id": self.policy_id, "version": self.policy_version},
            "principal": {"subject": self.principal.subject, "tenant": self.principal.tenant},
            "workload": self.workload,
            "budgets": asdict(self.budgets),
            "used": {
                "attempts": self.attempts_used,
                "tool_calls": self.tool_calls_used,
                "tokens": self.tokens_used,
                "cost": round(self.cost_used, 4),
                "active_seconds": self.active_seconds(now),
            },
            "budget_remaining": self.budget_remaining(now),
            "consecutive_verifier_failures": self.consecutive_verifier_failures,
            "plan_digest": self.plan_digest,
            "plan_accepted": self.plan_accepted,
            "completed_steps": list(self.completed_steps),
            "proposals": [p["step_id"] for p in self.proposals],
            "pending_escalation_id": (self.pending_escalation or {}).get("escalation_id"),
            "suspended_reason": self.suspended_reason,
            "checkpoint": self.checkpoint,
            "last_decision_id": self.last_decision_id,
            "updated_at": iso(now),
        }


def build_policy(
    pack: DomainPack,
    *,
    version: str = "2026.10.1",
    budgets: Budgets | None = None,
    max_verifier_failures: int = 2,
    max_continuations: int = 1,
    min_plan_confidence: float = 0.7,
) -> PolicyDocument:
    budgets = budgets or Budgets()
    tool_classes = {
        pack.read_tool: READ,
        pack.write_tool: REVERSIBLE_WRITE,
        pack.irreversible_tool: IRREVERSIBLE,
        pack.unlisted_tool: IRREVERSIBLE,  # registered, but in no profile's allowlist
    }
    arg_constraints = {
        pack.write_tool: {
            pack.scope_arg: {"allowed": list(pack.allowed_scopes)},
            pack.amount_arg: {"min": 1, "max": pack.amount_max},
        }
    }
    boundary = {
        "internal": ("public", "internal", "confidential", "regulated"),
        "external": ("public", "internal"),
    }
    every_tool = (pack.read_tool, pack.write_tool, pack.irreversible_tool)
    rules = {
        "observe-only": ((pack.read_tool,), {READ: RULE_ALLOW, REVERSIBLE_WRITE: RULE_DENY, IRREVERSIBLE: RULE_DENY}),
        "propose": (every_tool, {READ: RULE_ALLOW, REVERSIBLE_WRITE: RULE_PROPOSE, IRREVERSIBLE: RULE_DENY}),
        "act-with-approval": (every_tool, {READ: RULE_ALLOW, REVERSIBLE_WRITE: RULE_ESCALATE, IRREVERSIBLE: RULE_ESCALATE}),
        "act": (every_tool, {READ: RULE_ALLOW, REVERSIBLE_WRITE: RULE_ALLOW, IRREVERSIBLE: RULE_ESCALATE}),
    }
    profiles = {
        name: AutonomyProfile(
            name=name,
            allowed_tools=tools,
            side_effect_rules=dict(side_effects),
            arg_constraints=arg_constraints,
            data_boundary=boundary,
            budgets=budgets,
            max_consecutive_verifier_failures=max_verifier_failures,
            min_plan_confidence=min_plan_confidence,
            max_continuations=max_continuations,
        )
        for name, (tools, side_effects) in rules.items()
    }
    return PolicyDocument(
        policy_id=f"autonomy-{pack.key}",
        version=version,
        tool_classes=tool_classes,
        profiles=profiles,
        role_profiles={
            "Autonomy.Observe": "observe-only",
            "Autonomy.Propose": "propose",
            "Autonomy.ActWithApproval": "act-with-approval",
            "Autonomy.Act": "act",
        },
        workload_caps={pack.workload: "act", f"{pack.workload}-restricted": "act-with-approval"},
    )


def resolve_profile(doc: PolicyDocument, principal: Principal, workload: str) -> tuple[str | None, dict[str, Any]]:
    """Profile = min(highest role grant, workload cap). The prompt is never an input."""
    levels = [PROFILE_ORDER.index(doc.role_profiles[r]) for r in principal.roles if r in doc.role_profiles]
    cap = doc.workload_caps.get(workload)
    if not levels or cap is None:
        return None, {"role_profile": None, "workload_cap": cap}
    role_level = max(levels)
    chosen = PROFILE_ORDER[min(role_level, PROFILE_ORDER.index(cap))]
    return chosen, {"role_profile": PROFILE_ORDER[role_level], "workload_cap": cap}


def arg_violations(constraints: dict[str, dict[str, Any]], args: dict[str, Any]) -> tuple[str, ...]:
    found: list[str] = []
    for name, rule in constraints.items():
        if name not in args:
            found.append(f"{name}:missing")
            continue
        value = args[name]
        if "allowed" in rule and value not in rule["allowed"]:
            found.append(f"{name}:not-allowed")
        if "min" in rule or "max" in rule:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                found.append(f"{name}:not-numeric")
            elif "max" in rule and value > rule["max"]:
                found.append(f"{name}:above-max")
            elif "min" in rule and value < rule["min"]:
                found.append(f"{name}:below-min")
    return tuple(found)


def envelope_check(doc: PolicyDocument, profile: AutonomyProfile, step: PlannedStep) -> EnvelopeResult:
    """Precedence: tool allowlist, data boundary, side-effect rule, argument constraints."""
    cls = doc.tool_classes.get(step.tool, UNKNOWN_CLASS)
    if cls == UNKNOWN_CLASS or step.tool not in profile.allowed_tools:
        return EnvelopeResult(DENY, (R.TOOL_NOT_IN_PROFILE,), cls)
    if step.data_class not in profile.data_boundary.get(step.destination, ()):
        return EnvelopeResult(DENY, (R.DATA_BOUNDARY_VIOLATED,), cls)
    rule = profile.side_effect_rules.get(cls, RULE_DENY)
    violations = arg_violations(profile.arg_constraints.get(step.tool, {}), step.args)
    if rule == RULE_DENY:
        return EnvelopeResult(DENY, (R.SIDE_EFFECT_EXCEEDS_PROFILE,), cls, violations)
    if rule == RULE_PROPOSE:
        return EnvelopeResult(DENY, (R.PROPOSE_ONLY,), cls, violations)
    if rule == RULE_ESCALATE:
        extra = (R.ARG_CONSTRAINT_VIOLATED,) if violations else ()
        return EnvelopeResult(ESCALATE, (R.APPROVAL_REQUIRED,) + extra, cls, violations)
    if violations:
        return EnvelopeResult(ESCALATE, (R.ARG_CONSTRAINT_VIOLATED,), cls, violations)
    return EnvelopeResult(ALLOW, (R.IN_ENVELOPE,), cls)


class PolicyStoreUnavailable(RuntimeError):
    pass


class DecisionSinkUnavailable(RuntimeError):
    pass


class PolicyStore(Protocol):
    def current(self) -> PolicyDocument: ...

    def is_revoked(self, version: str, profile: str) -> bool: ...


class InMemoryPolicyStore:
    """Test fake. `available=False` simulates an unreachable store."""

    def __init__(self, doc: PolicyDocument) -> None:
        self._doc = doc
        self._revoked: set[tuple[str, str]] = set()
        self.available = True

    def _check(self) -> None:
        if not self.available:
            raise PolicyStoreUnavailable("policy store unreachable")

    def current(self) -> PolicyDocument:
        self._check()
        return self._doc

    def is_revoked(self, version: str, profile: str) -> bool:
        self._check()
        return (version, profile) in self._revoked or ("*", profile) in self._revoked

    def publish(self, doc: PolicyDocument) -> None:
        self._doc = doc

    def revoke(self, profile: str, version: str = "*") -> None:
        self._revoked.add((version, profile))


class AppConfigurationPolicyStore:
    """Production adapter seam (not implemented in this sample).

    Expected shape: read one key (for example `autonomy-policy`) by label or from an
    immutable snapshot in Azure App Configuration, authenticate with the harness
    managed identity holding the App Configuration Data Reader role, validate the
    document against the policy schema, and keep revocations under a separate key
    that is read on every gate check. Raise PolicyStoreUnavailable on any read or
    validation failure. Never fall back to built-in defaults.
    """

    def __init__(self, endpoint: str, key: str, label: str) -> None:
        self.endpoint, self.key, self.label = endpoint, key, label

    def current(self) -> PolicyDocument:
        raise NotImplementedError("Wire this to your policy store; see docs/architecture.md.")

    def is_revoked(self, version: str, profile: str) -> bool:
        raise NotImplementedError("Wire this to your policy store; see docs/architecture.md.")


class DecisionSink(Protocol):
    def emit(self, record: dict[str, Any]) -> None: ...


class InMemoryDecisionSink:
    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self.available = True

    def emit(self, record: dict[str, Any]) -> None:
        if not self.available:
            raise DecisionSinkUnavailable("decision sink unreachable")
        self.records.append(record)


class EvidenceLedgerSink:
    """Production adapter seam: append each decision record to the evidence ledger
    (see the evidence-ledger-and-run-certificate asset). Raise DecisionSinkUnavailable
    when the append is not durably acknowledged."""

    def emit(self, record: dict[str, Any]) -> None:
        raise NotImplementedError("Append to your evidence ledger; see the sibling asset.")


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class IdGen:
    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def next(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}-{self._counters[prefix]:04d}"


class AutonomyGovernor:
    """Policy decision point owned by the harness loop. The model proposes; this decides."""

    def __init__(self, store: PolicyStore, sink: DecisionSink, clock: FakeClock | None = None, ids: IdGen | None = None) -> None:
        self.store = store
        self.sink = sink
        self.clock = clock or FakeClock()
        self.ids = ids or IdGen()
        self.runs: dict[str, RuntimeState] = {}
        self._pinned: dict[str, PolicyDocument] = {}

    # ---- run lifecycle -------------------------------------------------
    def start_run(
        self,
        principal: Principal,
        workload: str,
        requested_profile: str | None = None,
        *,
        parent: RuntimeState | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> Decision:
        run_id = self.ids.next("run")
        try:
            doc = self.store.current()
            profile_name, detail = resolve_profile(doc, principal, workload)
            revoked = profile_name is not None and self.store.is_revoked(doc.version, profile_name)
        except PolicyStoreUnavailable:
            return self._refuse(run_id, principal, workload, R.POLICY_UNAVAILABLE, {}, None)
        detail["requested_profile"] = requested_profile
        detail["requested_profile_ignored"] = requested_profile is not None
        if profile_name is None:
            return self._refuse(run_id, principal, workload, R.NO_PROFILE_ASSIGNED, detail, doc)
        if revoked:
            return self._refuse(run_id, principal, workload, R.PROFILE_REVOKED, detail, doc)
        state = RuntimeState(
            run_id=run_id, principal=principal, workload=workload, profile=profile_name,
            policy_id=doc.policy_id, policy_version=doc.version,
            budgets=doc.profiles[profile_name].budgets, started_at=self.clock.now(),
        )
        if parent is not None:
            state.parent_run_id = parent.run_id
            state.continuation_index = parent.continuation_index + 1
            state.checkpoint = checkpoint
            state.completed_steps = list(parent.completed_steps)
            state.proposals = [dict(p) for p in parent.proposals]
            detail["parent_run_id"] = parent.run_id
        self.runs[run_id] = state
        self._pinned[run_id] = doc  # policy is pinned for the life of the run
        decision = self._record(state, ALLOW, (R.PROFILE_ASSIGNED,), {"type": "run", "workload": workload}, detail)
        if decision.outcome != ALLOW:
            state.status = REFUSED
        return decision

    def _refuse(self, run_id: str, principal: Principal, workload: str, reason: str,
                detail: dict[str, Any], doc: PolicyDocument | None) -> Decision:
        state = RuntimeState(
            run_id=run_id, principal=principal, workload=workload, profile="none",
            policy_id=doc.policy_id if doc else "unknown", policy_version=doc.version if doc else "unknown",
            budgets=Budgets(0, 0, 0, 0, None), started_at=self.clock.now(), status=REFUSED,
        )
        self.runs[run_id] = state
        return self._record(state, DENY, (reason,), {"type": "run", "workload": workload}, detail)

    # ---- recording and state helpers ----------------------------------
    def _record(self, state: RuntimeState, outcome: str, reasons: tuple[str, ...], subject: dict[str, Any],
                details: dict[str, Any] | None = None, escalation_id: str | None = None,
                resolutions: tuple[str, ...] = ()) -> Decision:
        now = self.clock.now()
        record = {
            "schema": SCHEMA_DECISION,
            "decision_id": self.ids.next("dec"),
            "run_id": state.run_id,
            "evaluated_at": iso(now),
            "outcome": outcome,
            "reason_codes": list(reasons),
            "policy": {"policy_id": state.policy_id, "version": state.policy_version},
            "profile": state.profile,
            "principal": {"subject": state.principal.subject, "tenant": state.principal.tenant},
            "phase": state.phase,
            "subject": subject,
            "budget_remaining": state.budget_remaining(now),
            "escalation_id": escalation_id,
            "allowed_resolutions": list(resolutions),
            "details": details or {},
        }
        try:
            self.sink.emit(record)
        except DecisionSinkUnavailable:
            # No audit record, no action: fail closed and suspend the run.
            self._suspend(state, R.AUDIT_UNAVAILABLE)
            failed = {**record, "outcome": DENY, "reason_codes": [R.AUDIT_UNAVAILABLE],
                      "details": {"intended_outcome": outcome, "intended_reasons": list(reasons)}}
            return Decision(DENY, (R.AUDIT_UNAVAILABLE,), failed, run_id=state.run_id)
        state.last_decision_id = record["decision_id"]
        return Decision(outcome, tuple(reasons), record, run_id=state.run_id)

    def _pause(self, state: RuntimeState) -> None:
        if state.paused_at is None:
            state.paused_at = self.clock.now()

    def _unpause(self, state: RuntimeState) -> None:
        if state.paused_at is not None:
            state.paused_seconds += (self.clock.now() - state.paused_at).total_seconds()
            state.paused_at = None

    def _suspend(self, state: RuntimeState, reason: str) -> None:
        if state.status in (RUNNING, ESCALATED):
            state.suspended_from = state.status
            state.suspended_reason = reason
            state.status = SUSPENDED
            self._pause(state)

    def _resume(self, state: RuntimeState) -> None:
        state.pending_escalation = None
        state.status = RUNNING
        self._unpause(state)

    def _require_running(self, state: RuntimeState, subject: dict[str, Any]) -> Decision | None:
        if state.status != RUNNING:
            return self._record(state, DENY, (R.RUN_NOT_RUNNING,), subject, {"status": state.status})
        return None

    def _policy_gate(self, state: RuntimeState, subject: dict[str, Any]) -> Decision | None:
        """Live check on every action: store reachable and profile not revoked (kill switch)."""
        try:
            revoked = self.store.is_revoked(state.policy_version, state.profile)
        except PolicyStoreUnavailable:
            self._suspend(state, R.POLICY_UNAVAILABLE)
            return self._record(state, DENY, (R.POLICY_UNAVAILABLE,), subject, {"status": state.status})
        if revoked:
            state.status = STOPPED
            return self._record(state, DENY, (R.PROFILE_REVOKED,), subject)
        return None

    def _exhausted(self, state: RuntimeState, *, next_attempt: bool = False, next_tool_call: bool = False) -> list[str]:
        b, now = state.budgets, self.clock.now()
        out: list[str] = []
        if next_attempt and state.attempts_used + 1 > b.attempts:
            out.append("attempts")
        if next_tool_call and state.tool_calls_used + 1 > b.tool_calls:
            out.append("tool_calls")
        if state.tokens_used >= b.tokens:
            out.append("tokens")
        if state.active_seconds(now) >= b.wall_clock_seconds:
            out.append("wall_clock_seconds")
        if b.cost is not None and state.cost_used >= b.cost:
            out.append("cost")
        return out

    def _state_digest(self, state: RuntimeState) -> str:
        return canonical_digest({
            "run_id": state.run_id,
            "attempts_used": state.attempts_used,
            "tool_calls_used": state.tool_calls_used,
            "tokens_used": state.tokens_used,
            "consecutive_verifier_failures": state.consecutive_verifier_failures,
            "completed_steps": state.completed_steps,
            "plan_digest": state.plan_digest,
        })

    def _escalate(self, state: RuntimeState, reasons: tuple[str, ...], subject: dict[str, Any], subject_digest: str,
                  resolutions: tuple[str, ...], findings: list[str] | None = None,
                  details: dict[str, Any] | None = None) -> Decision:
        escalation_id = self.ids.next("esc")
        now = self.clock.now()
        packet = {
            "schema": SCHEMA_ESCALATION,
            "escalation_id": escalation_id,
            "run_id": state.run_id,
            "profile": state.profile,
            "policy": {"policy_id": state.policy_id, "version": state.policy_version},
            "phase": state.phase,
            "reason_codes": list(reasons),
            "subject": subject,
            "subject_digest": subject_digest,
            "allowed_resolutions": list(resolutions),
            "budget_remaining": state.budget_remaining(now),
            "attempts_used": state.attempts_used,
            "tool_calls_used": state.tool_calls_used,
            "consecutive_verifier_failures": state.consecutive_verifier_failures,
            "completed_steps": list(state.completed_steps),
            "findings": list(findings or []),
            "requested_by": state.principal.subject,
            "created_at": iso(now),
        }
        decision = self._record(state, ESCALATE, reasons, subject, details, escalation_id, resolutions)
        if decision.outcome == ESCALATE:
            state.pending_escalation = packet
            state.status = ESCALATED
            self._pause(state)
            decision.packet = packet
        return decision

    def _budget_resolutions(self, state: RuntimeState) -> tuple[str, ...]:
        profile = self._pinned[state.run_id].profiles[state.profile]
        if state.continuation_index < profile.max_continuations:
            return (CONTINUE_IN_NEW_RUN, STOP)
        return (STOP,)

    # ---- plan-before-act ----------------------------------------------
    def submit_plan(self, run_id: str, plan: Plan) -> Decision:
        state = self.runs[run_id]
        subject = {"type": "plan", "plan_digest": plan.digest(), "steps": len(plan.steps)}
        blocked = self._require_running(state, subject) or self._policy_gate(state, subject)
        if blocked:
            return blocked
        state.phase = PHASE_PLAN
        exhausted = self._exhausted(state, next_attempt=True)
        if exhausted:
            return self._escalate(state, (R.BUDGET_EXHAUSTED,), subject, self._state_digest(state),
                                  self._budget_resolutions(state), details={"exhausted": exhausted})
        state.attempts_used += 1  # charged before evaluation so denied plans still count
        state.plan_accepted, state.plan_digest, state.planned_steps, state.pending_plan = False, None, {}, None
        doc, profile = self._pinned[run_id], self._pinned[run_id].profiles[state.profile]
        evaluations: list[dict[str, Any]] = []
        hard_denials: list[str] = []
        for step in plan.steps:
            result = envelope_check(doc, profile, step)
            evaluations.append({
                "step_id": step.step_id, "tool": step.tool, "side_effect_class": result.side_effect_class,
                "data_class": step.data_class, "verdict": result.verdict, "reason_codes": list(result.reasons),
                "violations": list(result.violations), "step_digest": step.digest(),
            })
            if result.verdict == DENY and R.PROPOSE_ONLY not in result.reasons:
                hard_denials.extend(r for r in result.reasons if r not in hard_denials)
        details: dict[str, Any] = {
            "evaluations": evaluations,
            "requested_profile": plan.requested_profile,
            "requested_profile_ignored": plan.requested_profile is not None,
            "confidence": plan.confidence,
            "ambiguities": list(plan.ambiguities),
        }
        if hard_denials:
            return self._record(state, DENY, tuple(hard_denials), subject, details)
        if (plan.confidence < profile.min_plan_confidence or plan.ambiguities) and not plan.human_confirmed:
            decision = self._escalate(state, (R.LOW_CONFIDENCE,), subject, plan.digest(), (CLARIFY_PLAN, STOP),
                                      findings=list(plan.ambiguities), details=details)
            if decision.outcome == ESCALATE:
                state.pending_plan = plan
            return decision
        details["escalation_points"] = [e["step_id"] for e in evaluations if e["verdict"] == ESCALATE]
        details["proposals"] = [e["step_id"] for e in evaluations if R.PROPOSE_ONLY in e["reason_codes"]]
        decision = self._record(state, ALLOW, (R.PLAN_ACCEPTED,), subject, details)
        if decision.outcome == ALLOW:
            self._accept_plan(state, plan)
        return decision

    def _accept_plan(self, state: RuntimeState, plan: Plan) -> None:
        state.plan_accepted = True
        state.plan_digest = plan.digest()
        state.planned_steps = {s.digest(): s for s in plan.steps}
        state.pending_plan = None
        state.phase = PHASE_ACT

    def authorize(self, run_id: str, step: PlannedStep) -> Decision:
        """Called by the harness before every tool call. Records first, then mutates state."""
        state = self.runs[run_id]
        digest = step.digest()
        subject = {"type": "step", "step_id": step.step_id, "tool": step.tool, "step_digest": digest}
        blocked = self._require_running(state, subject) or self._policy_gate(state, subject)
        if blocked:
            return blocked
        state.phase = PHASE_ACT
        if not state.plan_accepted:
            return self._record(state, DENY, (R.PLAN_REQUIRED,), subject)
        if digest not in state.planned_steps:
            return self._record(state, DENY, (R.STEP_NOT_IN_PLAN,), subject, {"plan_digest": state.plan_digest})
        if step.step_id in state.completed_steps or any(p["step_id"] == step.step_id for p in state.proposals):
            return self._record(state, DENY, (R.STEP_ALREADY_EXECUTED,), subject)
        profile = self._pinned[run_id].profiles[state.profile]
        result = envelope_check(self._pinned[run_id], profile, step)
        details = {"side_effect_class": result.side_effect_class, "data_class": step.data_class,
                   "destination": step.destination, "violations": list(result.violations)}
        if result.verdict == DENY:
            decision = self._record(state, DENY, result.reasons, subject, details)
            if R.PROPOSE_ONLY in decision.reasons:
                state.proposals.append({"step_id": step.step_id, "tool": step.tool, "args": step.args,
                                        "step_digest": digest, "decision_id": decision.record["decision_id"]})
            return decision
        exhausted = self._exhausted(state, next_tool_call=True)
        if exhausted:
            return self._escalate(state, (R.BUDGET_EXHAUSTED,), subject, self._state_digest(state),
                                  self._budget_resolutions(state), details={**details, "exhausted": exhausted})
        if state.consecutive_verifier_failures >= profile.max_consecutive_verifier_failures:
            return self._escalate(state, (R.VERIFIER_FAILURES_EXCEEDED,), subject, self._state_digest(state),
                                  (RETRY_WITH_NEW_PLAN, STOP), details=details)
        if result.verdict == ESCALATE:
            if digest in state.approval_grants:
                decision = self._record(state, ALLOW, (R.APPROVAL_GRANTED_RESUME,), subject,
                                        {**details, "original_reasons": list(result.reasons)})
                if decision.outcome == ALLOW:
                    state.approval_grants.discard(digest)  # grants are single use
                    self._consume(state, step)
                return decision
            return self._escalate(state, result.reasons, subject, digest, (APPROVE_STEP, STOP), details=details)
        decision = self._record(state, ALLOW, result.reasons, subject, details)
        if decision.outcome == ALLOW:
            self._consume(state, step)
        return decision

    def _consume(self, state: RuntimeState, step: PlannedStep) -> None:
        state.tool_calls_used += 1
        state.completed_steps.append(step.step_id)

    def record_verification(self, run_id: str, step_id: str, passed: bool, findings: list[str] | tuple[str, ...] = ()) -> Decision:
        state = self.runs[run_id]
        subject = {"type": "verification", "step_id": step_id}
        blocked = self._require_running(state, subject)
        if blocked:
            return blocked
        state.phase = PHASE_VERIFY
        profile = self._pinned[run_id].profiles[state.profile]
        if passed:
            decision = self._record(state, ALLOW, (R.VERIFIER_PASSED,), subject)
            if decision.outcome == ALLOW:
                state.consecutive_verifier_failures = 0
            return decision
        state.consecutive_verifier_failures += 1
        if step_id in state.completed_steps:
            state.completed_steps.remove(step_id)
        state.plan_accepted, state.plan_digest, state.planned_steps = False, None, {}
        details = {"findings": list(findings), "consecutive_failures": state.consecutive_verifier_failures,
                   "max_consecutive_failures": profile.max_consecutive_verifier_failures}
        if state.consecutive_verifier_failures >= profile.max_consecutive_verifier_failures:
            return self._escalate(state, (R.VERIFIER_FAILURES_EXCEEDED,), subject, self._state_digest(state),
                                  (RETRY_WITH_NEW_PLAN, STOP), findings=list(findings), details=details)
        return self._record(state, DENY, (R.VERIFIER_FAILED,), subject, details)

    def charge(self, run_id: str, tokens: int = 0, cost: float = 0.0) -> None:
        """Harness metering from model usage responses; the model never reports its own usage."""
        state = self.runs[run_id]
        state.tokens_used += tokens
        state.cost_used += cost

    def complete_run(self, run_id: str) -> Decision:
        state = self.runs[run_id]
        subject = {"type": "run"}
        blocked = self._require_running(state, subject)
        if blocked:
            return blocked
        state.phase = PHASE_FINALIZE
        decision = self._record(state, ALLOW, (R.RUN_COMPLETED,), subject,
                                {"completed_steps": list(state.completed_steps), "proposals": len(state.proposals)})
        if decision.outcome == ALLOW:
            state.status = COMPLETED
        return decision

    def retry_after_suspension(self, run_id: str) -> Decision:
        state = self.runs[run_id]
        subject = {"type": "run"}
        if state.status != SUSPENDED:
            return self._record(state, DENY, (R.RUN_NOT_SUSPENDED,), subject, {"status": state.status})
        try:
            revoked = self.store.is_revoked(state.policy_version, state.profile)
        except PolicyStoreUnavailable:
            return self._record(state, DENY, (R.POLICY_UNAVAILABLE,), subject, {"suspended_reason": state.suspended_reason})
        if revoked:
            state.status = STOPPED
            return self._record(state, DENY, (R.PROFILE_REVOKED,), subject)
        reason = R.AUDIT_RESTORED if state.suspended_reason == R.AUDIT_UNAVAILABLE else R.POLICY_RESTORED
        previous = state.suspended_from or RUNNING
        decision = self._record(state, ALLOW, (reason,), subject, {"resumed_status": previous})
        if decision.outcome == ALLOW:
            state.status, state.suspended_from, state.suspended_reason = previous, None, None
            if previous == RUNNING:
                self._unpause(state)
        return decision

    # ---- escalation resolution ----------------------------------------
    def resolve_escalation(self, resolution: Resolution, approver: Principal) -> Decision:
        """Apply a human decision. The approver comes from a validated token, not the run."""
        state = self.runs[resolution.run_id]
        packet = state.pending_escalation
        subject = {"type": "resolution", "escalation_id": resolution.escalation_id,
                   "action": resolution.action, "approver": approver.subject}

        def deny(reason: str, **extra: Any) -> Decision:
            return self._record(state, DENY, (reason,), subject, extra or None)

        if state.status != ESCALATED or packet is None or packet["escalation_id"] != resolution.escalation_id:
            return deny(R.ESCALATION_NOT_PENDING, status=state.status)
        if resolution.action not in packet["allowed_resolutions"]:
            return deny(R.RESOLUTION_NOT_ALLOWED, allowed=packet["allowed_resolutions"])
        if resolution.policy_version != state.policy_version:
            return deny(R.POLICY_VERSION_MISMATCH, expected=state.policy_version)
        if resolution.subject_digest != packet["subject_digest"]:
            return deny(R.APPROVAL_MISMATCH)
        if approver.subject == state.principal.subject:
            return deny(R.SELF_APPROVAL)
        doc = self._pinned[state.run_id]
        if doc.approver_role not in approver.roles or approver.tenant != state.principal.tenant:
            return deny(R.APPROVER_NOT_ELIGIBLE)
        escalation_id = packet["escalation_id"]
        details = {"escalation_id": escalation_id, "approver": approver.subject, "note": resolution.note}
        if resolution.action == STOP:
            # Stopping only reduces autonomy, so it does not need a live policy read.
            decision = self._record(state, DENY, (R.STOPPED_BY_APPROVER,), subject, details, escalation_id)
            if decision.reasons == (R.STOPPED_BY_APPROVER,):
                state.status, state.pending_escalation = STOPPED, None
            return decision
        try:
            revoked = self.store.is_revoked(state.policy_version, state.profile)
        except PolicyStoreUnavailable:
            return deny(R.POLICY_UNAVAILABLE)
        if revoked:
            state.status, state.pending_escalation = STOPPED, None
            return deny(R.PROFILE_REVOKED)

        if resolution.action == CONTINUE_IN_NEW_RUN:
            return self._continue_in_new_run(state, packet, subject, details)
        reason = {APPROVE_STEP: R.APPROVAL_GRANTED, CLARIFY_PLAN: R.PLAN_CLARIFIED,
                  RETRY_WITH_NEW_PLAN: R.RETRY_GRANTED}[resolution.action]
        decision = self._record(state, ALLOW, (reason,), subject, details, escalation_id)
        if decision.outcome != ALLOW:
            return decision
        if resolution.action == APPROVE_STEP:
            state.approval_grants.add(packet["subject_digest"])
            self._resume(state)
        elif resolution.action == CLARIFY_PLAN and state.pending_plan is not None:
            plan = state.pending_plan
            self._resume(state)
            self._accept_plan(state, Plan(plan.steps, plan.confidence, plan.ambiguities,
                                          plan.requested_profile, human_confirmed=True))
        elif resolution.action == RETRY_WITH_NEW_PLAN:
            state.consecutive_verifier_failures = 0
            state.plan_accepted, state.plan_digest, state.planned_steps = False, None, {}
            self._resume(state)
        return decision

    def _continue_in_new_run(self, state: RuntimeState, packet: dict[str, Any], subject: dict[str, Any],
                             details: dict[str, Any]) -> Decision:
        checkpoint = {
            "parent_run_id": state.run_id,
            "parent_escalation_id": packet["escalation_id"],
            "completed_steps": list(state.completed_steps),
            "remaining_steps": [s.step_id for s in state.planned_steps.values() if s.step_id not in state.completed_steps],
            "proposals": [p["step_id"] for p in state.proposals],
        }
        # The child re-resolves the profile and budgets from the current policy.
        child = self.start_run(state.principal, state.workload, parent=state, checkpoint=checkpoint)
        if child.outcome != ALLOW:
            return self._record(state, DENY, child.reasons, subject, {**details, "continuation_run_id": child.run_id})
        decision = self._record(state, ALLOW, (R.CONTINUATION_GRANTED,), subject,
                                {**details, "continuation_run_id": child.run_id}, packet["escalation_id"])
        if decision.outcome == ALLOW:
            state.status, state.pending_escalation = CONTINUED, None
        return decision


DOMAIN_PACKS: dict[str, DomainPack] = {
    "it-operations": DomainPack(
        key="it-operations", organization="Contoso", workload="service-operations",
        read_tool="get_service_health", read_args={"service": "orders-api"},
        write_tool="scale_service", scope_arg="environment", allowed_scopes=("dev", "test"),
        out_of_scope="prod", amount_arg="instance_delta", amount_max=2,
        irreversible_tool="purge_snapshots", irreversible_args={"service": "orders-api", "older_than_days": 30},
        irreversible_destination="internal", unlisted_tool="modify_firewall_rule",
        regulated_example="customer personal data in application logs",
    ),
    "payments-servicing": DomainPack(
        key="payments-servicing", organization="Fabrikam", workload="dispute-servicing",
        read_tool="get_transaction_history", read_args={"account_ref": "acct-1001"},
        write_tool="apply_goodwill_credit", scope_arg="segment", allowed_scopes=("retail",),
        out_of_scope="commercial", amount_arg="amount", amount_max=25,
        irreversible_tool="issue_card_refund", irreversible_args={"account_ref": "acct-1001", "amount": 40},
        irreversible_destination="external", unlisted_tool="change_account_owner",
        regulated_example="cardholder data",
    ),
    "healthcare-scheduling": DomainPack(
        key="healthcare-scheduling", organization="Northwind Health", workload="appointment-scheduling",
        read_tool="search_open_slots", read_args={"specialty": "dermatology"},
        write_tool="hold_appointment_slot", scope_arg="clinic", allowed_scopes=("clinic-east", "clinic-west"),
        out_of_scope="clinic-north", amount_arg="slots", amount_max=3,
        irreversible_tool="send_patient_message",
        irreversible_args={"patient_ref": "pt-2001", "template": "appointment-confirmation"},
        irreversible_destination="external", unlisted_tool="update_clinical_record",
        regulated_example="protected health information",
    ),
    "supply-chain": DomainPack(
        key="supply-chain", organization="Northwind Traders", workload="replenishment",
        read_tool="get_inventory_levels", read_args={"sku": "sku-3001"},
        write_tool="reserve_stock", scope_arg="warehouse", allowed_scopes=("wh-01", "wh-02"),
        out_of_scope="wh-99", amount_arg="quantity", amount_max=100,
        irreversible_tool="submit_purchase_order",
        irreversible_args={"supplier_ref": "sup-4001", "sku": "sku-3001", "quantity": 500},
        irreversible_destination="external", unlisted_tool="change_supplier_bank_details",
        regulated_example="supplier contract pricing",
    ),
}
DEFAULT_DOMAIN = "it-operations"


def read_step(pack: DomainPack, step_id: str = "s1") -> PlannedStep:
    return PlannedStep(step_id, pack.read_tool, dict(pack.read_args))


def write_step(pack: DomainPack, step_id: str = "s2", scope: str | None = None, amount: Any = 1) -> PlannedStep:
    return PlannedStep(step_id, pack.write_tool, {pack.scope_arg: scope or pack.allowed_scopes[0], pack.amount_arg: amount})


def irreversible_step(pack: DomainPack, step_id: str = "s3", destination: str | None = None,
                      labels: tuple[str, ...] = ("internal",)) -> PlannedStep:
    return PlannedStep(step_id, pack.irreversible_tool, dict(pack.irreversible_args),
                       destination or pack.irreversible_destination, tuple(labels))


def unlisted_step(pack: DomainPack, step_id: str = "s9") -> PlannedStep:
    return PlannedStep(step_id, pack.unlisted_tool, {"target": "example"})


# ---- self-test helpers -------------------------------------------------
OPERATOR = "user-operator-01"
APPROVER = Principal("user-approver-01", ("Autonomy.Approver",))


class ScenarioFailure(AssertionError):
    pass


class Env:
    def __init__(self, pack: DomainPack, **policy_kwargs: Any) -> None:
        self.pack = pack
        self.doc = build_policy(pack, **policy_kwargs)
        self.store = InMemoryPolicyStore(self.doc)
        self.sink = InMemoryDecisionSink()
        self.clock = FakeClock()
        self.gov = AutonomyGovernor(self.store, self.sink, self.clock, IdGen())

    def start(self, roles: tuple[str, ...] = ("Autonomy.Act",), subject: str = OPERATOR,
              workload: str | None = None, requested_profile: str | None = None) -> tuple[str, Decision]:
        decision = self.gov.start_run(Principal(subject, tuple(roles)), workload or self.pack.workload, requested_profile)
        return decision.run_id or "", decision

    def state(self, run_id: str) -> RuntimeState:
        return self.gov.runs[run_id]

    def plan(self, run_id: str, *steps: PlannedStep, **kwargs: Any) -> Decision:
        return self.gov.submit_plan(run_id, Plan(tuple(steps), **kwargs))

    def resolve(self, packet: dict[str, Any], action: str, approver: Principal = APPROVER, **overrides: Any) -> Decision:
        return self.gov.resolve_escalation(resolution_for(packet, action, **overrides), approver)


def expect(decision: Decision, outcome: str, *reasons: str) -> Decision:
    if decision.outcome != outcome or not set(reasons) <= set(decision.reasons):
        raise ScenarioFailure(f"expected {outcome} {list(reasons)}, got {decision.outcome} {list(decision.reasons)}")
    return decision


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ScenarioFailure(message)


def resolution_for(packet: dict[str, Any], action: str, **overrides: Any) -> Resolution:
    values = {
        "run_id": packet["run_id"],
        "escalation_id": packet["escalation_id"],
        "action": action,
        "policy_version": packet["policy"]["version"],
        "subject_digest": packet["subject_digest"],
    }
    values.update(overrides)
    return Resolution(**values)


# ---- scenarios -----------------------------------------------------------
def sc_allow_in_envelope_read(pack: DomainPack) -> None:
    env = Env(pack)
    run, started = env.start()
    expect(started, ALLOW, R.PROFILE_ASSIGNED)
    step = read_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)
    expect(env.gov.record_verification(run, step.step_id, True), ALLOW, R.VERIFIER_PASSED)
    expect(env.gov.complete_run(run), ALLOW, R.RUN_COMPLETED)
    st = env.state(run)
    check(st.status == COMPLETED and st.tool_calls_used == 1 and st.attempts_used == 1, "counters after a clean run")
    check(len(env.sink.records) == 5, "one decision record per governed action")
    check(all(r["policy"]["version"] == env.doc.version for r in env.sink.records), "policy version on every record")


def sc_server_policy_selects_profile(pack: DomainPack) -> None:
    env = Env(pack)
    run, started = env.start(("Autonomy.Propose",), requested_profile="act")
    expect(started, ALLOW, R.PROFILE_ASSIGNED)
    check(env.state(run).profile == "propose", "a requested profile must not raise autonomy")
    check(started.record["details"]["requested_profile_ignored"] is True, "ignored request is recorded")
    planned = expect(env.plan(run, read_step(pack), requested_profile="act"), ALLOW, R.PLAN_ACCEPTED)
    check(planned.record["details"]["requested_profile_ignored"] is True, "plan-level request is recorded and ignored")
    capped, _ = env.start(("Autonomy.Act",), workload=f"{pack.workload}-restricted")
    check(env.state(capped).profile == "act-with-approval", "workload cap limits the role grant")
    highest, _ = env.start(("Autonomy.Observe", "Autonomy.Act"))
    check(env.state(highest).profile == "act", "highest role grant wins below the cap")
    expect(env.start(())[1], DENY, R.NO_PROFILE_ASSIGNED)
    unknown, refused = env.start(workload="unregistered-workload")
    expect(refused, DENY, R.NO_PROFILE_ASSIGNED)
    check(env.state(unknown).status == REFUSED, "no profile means no run")


def sc_observe_only_denies_write(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start(("Autonomy.Observe",))
    expect(env.plan(run, read_step(pack), write_step(pack)), DENY, R.TOOL_NOT_IN_PROFILE)
    check(not env.state(run).plan_accepted, "a plan with a denied step is not accepted")
    expect(env.plan(run, read_step(pack)), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, write_step(pack)), DENY, R.STEP_NOT_IN_PLAN)
    expect(env.gov.authorize(run, read_step(pack)), ALLOW, R.IN_ENVELOPE)


def sc_propose_records_proposal(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start(("Autonomy.Propose",))
    read, write = read_step(pack), write_step(pack)
    planned = expect(env.plan(run, read, write), ALLOW, R.PLAN_ACCEPTED)
    check(planned.record["details"]["proposals"] == [write.step_id], "plan lists the proposal")
    expect(env.gov.authorize(run, read), ALLOW, R.IN_ENVELOPE)
    expect(env.gov.authorize(run, write), DENY, R.PROPOSE_ONLY)
    st = env.state(run)
    check(len(st.proposals) == 1 and st.tool_calls_used == 1, "proposal recorded, write not executed")
    expect(env.gov.authorize(run, write), DENY, R.STEP_ALREADY_EXECUTED)


def sc_propose_denies_irreversible(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start(("Autonomy.Propose",))
    expect(env.plan(run, read_step(pack), irreversible_step(pack)), DENY, R.SIDE_EFFECT_EXCEEDS_PROFILE)
    st = env.state(run)
    check(st.status == RUNNING and st.tool_calls_used == 0, "deny leaves the run able to re-plan")


def sc_tool_not_in_profile_denied(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    denied = expect(env.plan(run, unlisted_step(pack)), DENY, R.TOOL_NOT_IN_PROFILE)
    check(denied.record["details"]["evaluations"][0]["side_effect_class"] == IRREVERSIBLE, "registered but unlisted")
    unknown = expect(env.plan(run, PlannedStep("s8", "unregistered_tool", {})), DENY, R.TOOL_NOT_IN_PROFILE)
    check(unknown.record["details"]["evaluations"][0]["side_effect_class"] == UNKNOWN_CLASS, "unknown tool class")


def sc_data_boundary_denied(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    leak = irreversible_step(pack, destination="external", labels=("internal", "regulated"))
    expect(env.plan(run, leak), DENY, R.DATA_BOUNDARY_VIOLATED)
    unlabelled = irreversible_step(pack, destination="external", labels=("unlabelled",))
    expect(env.plan(run, unlabelled), DENY, R.DATA_BOUNDARY_VIOLATED)
    nowhere = PlannedStep("s7", pack.read_tool, dict(pack.read_args), destination="unregistered-destination")
    expect(env.plan(run, nowhere), DENY, R.DATA_BOUNDARY_VIOLATED)
    check(env.state(run).status == RUNNING, "boundary violations deny; they do not escalate")


def sc_plan_required_before_act(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    expect(env.gov.authorize(run, read_step(pack)), DENY, R.PLAN_REQUIRED)
    check(env.state(run).tool_calls_used == 0, "nothing charged without a plan")


def sc_unplanned_step_denied(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    read = read_step(pack)
    expect(env.plan(run, read), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, write_step(pack)), DENY, R.STEP_NOT_IN_PLAN)
    tampered = PlannedStep(read.step_id, read.tool, {**read.args, "extra": "value"})
    expect(env.gov.authorize(run, tampered), DENY, R.STEP_NOT_IN_PLAN)
    expect(env.gov.authorize(run, read), ALLOW, R.IN_ENVELOPE)
    expect(env.gov.authorize(run, read), DENY, R.STEP_ALREADY_EXECUTED)


def sc_arg_constraint_escalates(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    inside = write_step(pack, "s2")
    out_of_scope = write_step(pack, "s3", scope=pack.out_of_scope)
    too_large = write_step(pack, "s4", amount=pack.amount_max + 1)
    planned = expect(env.plan(run, inside, out_of_scope, too_large), ALLOW, R.PLAN_ACCEPTED)
    check(planned.record["details"]["escalation_points"] == ["s3", "s4"], "plan shows escalation points up front")
    expect(env.gov.authorize(run, inside), ALLOW, R.IN_ENVELOPE)
    escalated = expect(env.gov.authorize(run, out_of_scope), ESCALATE, R.ARG_CONSTRAINT_VIOLATED)
    check(escalated.packet is not None and escalated.packet["subject_digest"] == out_of_scope.digest(), "packet binds the step")
    expect(env.resolve(escalated.packet, APPROVE_STEP), ALLOW, R.APPROVAL_GRANTED)
    expect(env.gov.authorize(run, out_of_scope), ALLOW, R.APPROVAL_GRANTED_RESUME)
    expect(env.gov.authorize(run, too_large), ESCALATE, R.ARG_CONSTRAINT_VIOLATED)


def sc_irreversible_escalates_under_act(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = irreversible_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    escalated = expect(env.gov.authorize(run, step), ESCALATE, R.APPROVAL_REQUIRED)
    packet = escalated.packet or {}
    check(packet.get("subject_digest") == step.digest(), "packet digest is the step digest")
    check(packet.get("allowed_resolutions") == [APPROVE_STEP, STOP], "approve or stop")
    check(env.state(run).tool_calls_used == 0, "nothing executed while escalated")
    expect(env.gov.authorize(run, step), DENY, R.RUN_NOT_RUNNING)
    gated, _ = env.start(("Autonomy.ActWithApproval",))
    write = write_step(pack)
    expect(env.plan(gated, write), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(gated, write), ESCALATE, R.APPROVAL_REQUIRED)


def sc_budget_exhausted_escalates(pack: DomainPack) -> None:
    env = Env(pack, budgets=Budgets(tool_calls=2))
    run, _ = env.start()
    steps = [read_step(pack, f"r{i}") for i in (1, 2, 3)]
    expect(env.plan(run, *steps), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, steps[0]), ALLOW, R.IN_ENVELOPE)
    expect(env.gov.authorize(run, steps[1]), ALLOW, R.IN_ENVELOPE)
    escalated = expect(env.gov.authorize(run, steps[2]), ESCALATE, R.BUDGET_EXHAUSTED)
    check(escalated.record["details"]["exhausted"] == ["tool_calls"], "names the exhausted budget")
    st = env.state(run)
    check(st.tool_calls_used == 2 and st.status == ESCALATED, "stops at the budget")
    check((escalated.packet or {}).get("allowed_resolutions") == [CONTINUE_IN_NEW_RUN, STOP], "budget resolutions")


def sc_token_wall_clock_and_cost_budgets(pack: DomainPack) -> None:
    cases = (
        ("tokens", Budgets(tokens=1000), lambda env, run: env.gov.charge(run, tokens=1000)),
        ("wall_clock_seconds", Budgets(wall_clock_seconds=60), lambda env, run: env.clock.advance(61)),
        ("cost", Budgets(cost=0.5), lambda env, run: env.gov.charge(run, cost=0.5)),
    )
    for name, budgets, spend in cases:
        env = Env(pack, budgets=budgets)
        run, _ = env.start()
        step = read_step(pack)
        expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
        spend(env, run)
        escalated = expect(env.gov.authorize(run, step), ESCALATE, R.BUDGET_EXHAUSTED)
        check(name in escalated.record["details"]["exhausted"], f"{name} budget is enforced by the harness")
        check(env.state(run).tool_calls_used == 0, f"no tool call after {name} exhaustion")


def sc_attempts_budget_escalates(pack: DomainPack) -> None:
    env = Env(pack, budgets=Budgets(attempts=2))
    run, _ = env.start()
    expect(env.plan(run, unlisted_step(pack)), DENY, R.TOOL_NOT_IN_PROFILE)
    expect(env.plan(run, unlisted_step(pack)), DENY, R.TOOL_NOT_IN_PROFILE)
    escalated = expect(env.plan(run, read_step(pack)), ESCALATE, R.BUDGET_EXHAUSTED)
    check(escalated.record["details"]["exhausted"] == ["attempts"], "denied plans still consume attempts")
    check(env.state(run).attempts_used == 2, "the escalating attempt is not charged")


def sc_verifier_failures_escalate(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = write_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)
    expect(env.gov.record_verification(run, step.step_id, False, ["postcondition-not-met"]), DENY, R.VERIFIER_FAILED)
    st = env.state(run)
    check(not st.plan_accepted and step.step_id not in st.completed_steps, "a failed step invalidates the plan")
    expect(env.gov.authorize(run, step), DENY, R.PLAN_REQUIRED)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)
    escalated = expect(env.gov.record_verification(run, step.step_id, False, ["postcondition-not-met"]),
                       ESCALATE, R.VERIFIER_FAILURES_EXCEEDED)
    packet = escalated.packet or {}
    check(packet.get("findings") == ["postcondition-not-met"], "packet carries verifier findings")
    check(packet.get("allowed_resolutions") == [RETRY_WITH_NEW_PLAN, STOP], "retry or stop")
    expect(env.resolve(packet, RETRY_WITH_NEW_PLAN), ALLOW, R.RETRY_GRANTED)
    check(env.state(run).consecutive_verifier_failures == 0, "approved retry resets the failure streak")
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)


def sc_low_confidence_escalates(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = read_step(pack)
    escalated = expect(env.plan(run, step, confidence=0.4), ESCALATE, R.LOW_CONFIDENCE)
    check((escalated.packet or {}).get("allowed_resolutions") == [CLARIFY_PLAN, STOP], "clarify or stop")
    expect(env.resolve(escalated.packet or {}, CLARIFY_PLAN), ALLOW, R.PLAN_CLARIFIED)
    check(env.state(run).plan_accepted, "a clarified plan is accepted as human-confirmed")
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)
    other, _ = env.start()
    ambiguous = expect(env.plan(other, step, ambiguities=("target-not-specified",)), ESCALATE, R.LOW_CONFIDENCE)
    check((ambiguous.packet or {}).get("findings") == ["target-not-specified"], "ambiguities become findings")


def sc_policy_unavailable_fails_closed(pack: DomainPack) -> None:
    env = Env(pack)
    env.store.available = False
    refused_run, refused = env.start()
    expect(refused, DENY, R.POLICY_UNAVAILABLE)
    check(env.state(refused_run).status == REFUSED, "no policy, no run")
    env.store.available = True
    run, _ = env.start()
    step = read_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    env.store.available = False
    expect(env.gov.authorize(run, step), DENY, R.POLICY_UNAVAILABLE)
    st = env.state(run)
    check(st.status == SUSPENDED and st.tool_calls_used == 0, "suspended without executing")
    expect(env.gov.authorize(run, step), DENY, R.RUN_NOT_RUNNING)
    expect(env.gov.retry_after_suspension(run), DENY, R.POLICY_UNAVAILABLE)
    env.store.available = True
    expect(env.gov.retry_after_suspension(run), ALLOW, R.POLICY_RESTORED)
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)


def sc_kill_switch_stops_run(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = read_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    env.store.revoke("act")
    expect(env.gov.authorize(run, step), DENY, R.PROFILE_REVOKED)
    check(env.state(run).status == STOPPED, "revocation stops the run")
    expect(env.start()[1], DENY, R.PROFILE_REVOKED)
    observer, started = env.start(("Autonomy.Observe",))
    expect(started, ALLOW, R.PROFILE_ASSIGNED)
    check(env.state(observer).profile == "observe-only", "other profiles keep working")
    env.store.revoke("propose", version="2026.09.9")
    expect(env.start(("Autonomy.Propose",))[1], ALLOW, R.PROFILE_ASSIGNED)


def sc_resume_after_approval(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = irreversible_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    escalated = expect(env.gov.authorize(run, step), ESCALATE, R.APPROVAL_REQUIRED)
    before = env.state(run).budget_remaining(env.clock.now())["wall_clock_seconds"]
    env.clock.advance(3600)  # the approver takes an hour
    expect(env.resolve(escalated.packet or {}, APPROVE_STEP, note="reviewed"), ALLOW, R.APPROVAL_GRANTED)
    after = env.state(run).budget_remaining(env.clock.now())["wall_clock_seconds"]
    check(before == after, "time waiting on a human is not charged to the wall-clock budget")
    expect(env.gov.authorize(run, step), ALLOW, R.APPROVAL_GRANTED_RESUME)
    st = env.state(run)
    check(st.attempts_used == 1 and st.tool_calls_used == 1, "resume continues the same run without re-planning")
    check(not st.approval_grants, "approval grants are single use")
    expect(env.gov.record_verification(run, step.step_id, True), ALLOW, R.VERIFIER_PASSED)
    expect(env.gov.complete_run(run), ALLOW, R.RUN_COMPLETED)


def sc_approval_must_match_escalation(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start(("Autonomy.Act", "Autonomy.Approver"))
    step = irreversible_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    packet = expect(env.gov.authorize(run, step), ESCALATE, R.APPROVAL_REQUIRED).packet or {}
    attempts = (
        (R.APPROVAL_MISMATCH, APPROVE_STEP, APPROVER, {"subject_digest": write_step(pack).digest()}),
        (R.POLICY_VERSION_MISMATCH, APPROVE_STEP, APPROVER, {"policy_version": "2026.09.9"}),
        (R.SELF_APPROVAL, APPROVE_STEP, Principal(OPERATOR, ("Autonomy.Act", "Autonomy.Approver")), {}),
        (R.APPROVER_NOT_ELIGIBLE, APPROVE_STEP, Principal("user-viewer-02", ("Autonomy.Act",)), {}),
        (R.APPROVER_NOT_ELIGIBLE, APPROVE_STEP, Principal("user-approver-09", ("Autonomy.Approver",), "tenant-b"), {}),
        (R.RESOLUTION_NOT_ALLOWED, CONTINUE_IN_NEW_RUN, APPROVER, {}),
    )
    for reason, action, approver, overrides in attempts:
        expect(env.resolve(packet, action, approver, **overrides), DENY, reason)
        check(env.state(run).status == ESCALATED, f"{reason} leaves the escalation pending")
    expect(env.resolve(packet, APPROVE_STEP), ALLOW, R.APPROVAL_GRANTED)
    expect(env.resolve(packet, APPROVE_STEP), DENY, R.ESCALATION_NOT_PENDING)


def sc_continue_after_budget_in_new_run(pack: DomainPack) -> None:
    env = Env(pack, budgets=Budgets(tool_calls=1))
    run, _ = env.start()
    r1, r2, r3 = (read_step(pack, f"r{i}") for i in (1, 2, 3))
    expect(env.plan(run, r1, r2), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(run, r1), ALLOW, R.IN_ENVELOPE)
    packet = expect(env.gov.authorize(run, r2), ESCALATE, R.BUDGET_EXHAUSTED).packet or {}
    granted = expect(env.resolve(packet, CONTINUE_IN_NEW_RUN), ALLOW, R.CONTINUATION_GRANTED)
    child = granted.record["details"]["continuation_run_id"]
    parent_state, child_state = env.state(run), env.state(child)
    check(parent_state.status == CONTINUED, "parent is closed as continued")
    check(child_state.parent_run_id == run and child_state.continuation_index == 1, "child links to parent")
    checkpoint = child_state.checkpoint or {}
    check(checkpoint.get("completed_steps") == ["r1"] and checkpoint.get("remaining_steps") == ["r2"], "checkpoint")
    check(child_state.tool_calls_used == 0 and child_state.attempts_used == 0, "child gets fresh budgets from policy")
    expect(env.gov.authorize(child, r2), DENY, R.PLAN_REQUIRED)
    expect(env.plan(child, r1, r2), ALLOW, R.PLAN_ACCEPTED)
    expect(env.gov.authorize(child, r1), DENY, R.STEP_ALREADY_EXECUTED)
    expect(env.gov.authorize(child, r2), ALLOW, R.IN_ENVELOPE)
    expect(env.plan(child, r3), ALLOW, R.PLAN_ACCEPTED)
    last = expect(env.gov.authorize(child, r3), ESCALATE, R.BUDGET_EXHAUSTED).packet or {}
    check(last.get("allowed_resolutions") == [STOP], "continuations are bounded too")
    expect(env.resolve(last, CONTINUE_IN_NEW_RUN), DENY, R.RESOLUTION_NOT_ALLOWED)
    expect(env.resolve(last, STOP), DENY, R.STOPPED_BY_APPROVER)
    check(env.state(child).status == STOPPED, "stop is terminal")


def sc_audit_unavailable_fails_closed(pack: DomainPack) -> None:
    env = Env(pack)
    run, _ = env.start()
    step = read_step(pack)
    expect(env.plan(run, step), ALLOW, R.PLAN_ACCEPTED)
    env.sink.available = False
    denied = expect(env.gov.authorize(run, step), DENY, R.AUDIT_UNAVAILABLE)
    check(denied.record["details"]["intended_outcome"] == ALLOW, "the intended outcome is kept for diagnosis")
    st = env.state(run)
    check(st.status == SUSPENDED and st.tool_calls_used == 0, "no audit record, no action")
    expect(env.gov.retry_after_suspension(run), DENY, R.AUDIT_UNAVAILABLE)
    check(env.state(run).status == SUSPENDED, "still suspended while the sink is down")
    env.sink.available = True
    expect(env.gov.retry_after_suspension(run), ALLOW, R.AUDIT_RESTORED)
    expect(env.gov.authorize(run, step), ALLOW, R.IN_ENVELOPE)


def sc_budget_pinned_for_run(pack: DomainPack) -> None:
    env = Env(pack)
    run_a, _ = env.start()
    env.store.publish(build_policy(pack, version="2026.10.2", budgets=Budgets(tool_calls=1)))
    run_b, _ = env.start()
    a, b = env.state(run_a), env.state(run_b)
    check(a.policy_version == "2026.10.1" and a.budgets.tool_calls == 12, "a running run keeps its pinned policy")
    check(b.policy_version == "2026.10.2" and b.budgets.tool_calls == 1, "new runs pick up the new version")
    step = read_step(pack)
    expect(env.plan(run_a, step), ALLOW, R.PLAN_ACCEPTED)
    decision = expect(env.gov.authorize(run_a, step), ALLOW, R.IN_ENVELOPE)
    check(decision.record["policy"]["version"] == "2026.10.1", "records cite the pinned version")


SCENARIOS: dict[str, Callable[[DomainPack], None]] = {
    "allow-in-envelope-read": sc_allow_in_envelope_read,
    "server-policy-selects-profile": sc_server_policy_selects_profile,
    "observe-only-denies-write": sc_observe_only_denies_write,
    "propose-records-proposal": sc_propose_records_proposal,
    "propose-denies-irreversible": sc_propose_denies_irreversible,
    "tool-not-in-profile-denied": sc_tool_not_in_profile_denied,
    "data-boundary-denied": sc_data_boundary_denied,
    "plan-required-before-act": sc_plan_required_before_act,
    "unplanned-step-denied": sc_unplanned_step_denied,
    "arg-constraint-escalates": sc_arg_constraint_escalates,
    "irreversible-escalates-under-act": sc_irreversible_escalates_under_act,
    "budget-exhausted-escalates": sc_budget_exhausted_escalates,
    "token-wall-clock-and-cost-budgets": sc_token_wall_clock_and_cost_budgets,
    "attempts-budget-escalates": sc_attempts_budget_escalates,
    "verifier-failures-escalate": sc_verifier_failures_escalate,
    "low-confidence-escalates": sc_low_confidence_escalates,
    "policy-unavailable-fails-closed": sc_policy_unavailable_fails_closed,
    "kill-switch-stops-run": sc_kill_switch_stops_run,
    "resume-after-approval": sc_resume_after_approval,
    "approval-must-match-escalation": sc_approval_must_match_escalation,
    "continue-after-budget-in-new-run": sc_continue_after_budget_in_new_run,
    "audit-unavailable-fails-closed": sc_audit_unavailable_fails_closed,
    "budget-pinned-for-run": sc_budget_pinned_for_run,
}


def run_scenarios(names: list[str], domains: list[str]) -> int:
    failures = 0
    for name in names:
        for domain in domains:
            try:
                SCENARIOS[name](DOMAIN_PACKS[domain])
                print(f"PASS {name} [{domain}]")
            except ScenarioFailure as exc:
                failures += 1
                print(f"FAIL {name} [{domain}]: {exc}")
            except Exception as exc:  # report unexpected errors as failures, keep going
                failures += 1
                print(f"FAIL {name} [{domain}]: {type(exc).__name__}: {exc}")
    total = len(names) * len(domains)
    print(f"\n{total - failures}/{total} scenario runs passed ({len(names)} scenarios x {len(domains)} domain packs)")
    return failures


def run_demo(pack: DomainPack, as_json: bool) -> int:
    """One governed run: read, reversible write, then an irreversible step that needs approval."""
    env = Env(pack)
    gov = env.gov
    run, _ = env.start()
    read, write, final = read_step(pack), write_step(pack), irreversible_step(pack)
    env.plan(run, read, write, final)
    gov.authorize(run, read)
    gov.charge(run, tokens=1200)
    gov.record_verification(run, read.step_id, True)
    gov.authorize(run, write)
    gov.charge(run, tokens=900)
    gov.record_verification(run, write.step_id, True)
    escalated = gov.authorize(run, final)
    packet = escalated.packet or {}
    env.clock.advance(600)
    env.resolve(packet, APPROVE_STEP, note="change window confirmed")
    gov.authorize(run, final)
    gov.record_verification(run, final.step_id, True)
    gov.complete_run(run)
    snapshot = env.state(run).snapshot(env.clock.now())
    if as_json:
        print(json.dumps({"decisions": env.sink.records, "escalation": packet, "final_state": snapshot}, indent=2))
        return 0
    print(f"Domain pack: {pack.key} ({pack.organization}, workload '{pack.workload}')")
    print(f"Run {run}: profile '{snapshot['profile']}', policy {snapshot['policy']['version']}\n")
    print(f"{'decision':<10} {'outcome':<9} {'subject':<34} reasons")
    for record in env.sink.records:
        subject = record["subject"]
        label = (subject.get("tool") or subject.get("action") or subject.get("step_id")
                 or subject.get("workload") or (f"{subject['steps']} steps" if "steps" in subject else run))
        target = f"{subject.get('type')}:{label}"
        print(f"{record['decision_id']:<10} {record['outcome']:<9} {target:<34} {', '.join(record['reason_codes'])}")
    print(f"\nEscalation {packet.get('escalation_id')} offered: {', '.join(packet.get('allowed_resolutions', []))}")
    print(f"Final status: {snapshot['status']}; used: {snapshot['used']}")
    print(f"Remaining (illustrative budgets): {snapshot['budget_remaining']}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded autonomy governor (simulation).")
    parser.add_argument("--self-test", action="store_true", help="run every scenario against every domain pack")
    parser.add_argument("--list", action="store_true", help="list scenario names")
    parser.add_argument("--scenario", action="append", help="run one scenario (repeatable)")
    parser.add_argument("--domain", help="domain pack to use (default: all packs for tests, it-operations for demo)")
    parser.add_argument("--list-domains", action="store_true", help="list domain packs")
    parser.add_argument("--policy", action="store_true", help="print the generated policy document as JSON")
    parser.add_argument("--json", action="store_true", help="print demo output as JSON")
    args = parser.parse_args(argv)

    if args.domain and args.domain not in DOMAIN_PACKS:
        parser.error(f"unknown domain '{args.domain}'; choose from {', '.join(DOMAIN_PACKS)}")
    if args.list:
        print("\n".join(SCENARIOS))
        return 0
    if args.list_domains:
        for key, pack in DOMAIN_PACKS.items():
            print(f"{key:<24} {pack.organization:<18} regulated data example: {pack.regulated_example}")
        return 0
    if args.policy:
        print(json.dumps(build_policy(DOMAIN_PACKS[args.domain or DEFAULT_DOMAIN]).to_dict(), indent=2))
        return 0
    if args.self_test or args.scenario:
        names = args.scenario or list(SCENARIOS)
        unknown = [n for n in names if n not in SCENARIOS]
        if unknown:
            parser.error(f"unknown scenario(s): {', '.join(unknown)}; use --list")
        domains = [args.domain] if args.domain else list(DOMAIN_PACKS)
        return 1 if run_scenarios(names, domains) else 0
    return run_demo(DOMAIN_PACKS[args.domain or DEFAULT_DOMAIN], args.json)


if __name__ == "__main__":
    sys.exit(main())
