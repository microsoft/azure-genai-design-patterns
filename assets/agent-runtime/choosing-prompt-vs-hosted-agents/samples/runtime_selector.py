#!/usr/bin/env python3
"""Rubric-based runtime selector for Microsoft Foundry agents.

Scores a workload profile against the decision criteria in
``docs/architecture.md`` and recommends one of three runtimes:

* ``prompt``  - a declarative prompt agent (model + instructions + platform tools)
* ``hosted``  - a hosted agent (your own container and code, run by Foundry)
* ``mixed``   - a prompt agent front door with a hosted capability behind a tool

The selector encodes ADR 0001: default to a prompt agent, and escalate to a
hosted (or mixed) design only when at least one *named control* is a hard
requirement. It is a review aid, not an oracle: always read the rationale and
warnings, and check preview capabilities against current Microsoft Learn docs.

Standard library only. No network calls. Python 3.10 or later.

Usage::

    python runtime_selector.py --self-test
    python runtime_selector.py --profile example-profiles.json
    python runtime_selector.py --profile my-workload.json --json
    python runtime_selector.py --list-criteria
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Protocol

RUNTIMES: tuple[str, ...] = ("prompt", "hosted", "mixed")

# Tie-break order: the runtime with the smaller operational footprint wins.
TIE_BREAK: dict[str, int] = {"prompt": 0, "mixed": 1, "hosted": 2}

# Starting scores. Prompt agents start ahead because they are the default.
BASE_SCORES: dict[str, int] = {"prompt": 2, "hosted": 0, "mixed": 0}

# If the best eligible runtime leads the runner-up by this many points or
# fewer, the selector flags the result as a close call for human review.
CLOSE_CALL_MARGIN = 2


# ---------------------------------------------------------------------------
# Domain model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkloadProfile:
    """Answers to the decision questions for one agent scenario.

    Every flag defaults to the answer that keeps the simplest runtime, so a
    profile only needs to set the flags that are true for the scenario.
    """

    name: str
    description: str = ""

    # Named controls: any one of these, as a hard requirement, can justify
    # hosting code (ADR 0001, decision 2).
    loop_must_be_code: bool = False
    in_process_libraries: bool = False
    custom_protocol: bool = False
    custom_durable_state: bool = False
    custom_telemetry: bool = False

    # Shape of the gap between a prompt agent and the requirement.
    gap_isolatable_behind_tool: bool = False
    deterministic_substeps: bool = False

    # Front door and experience.
    user_facing_front_door: bool = False
    front_door_must_be_platform_governed: bool = False
    heavy_backend_processing: bool = False
    latency_sensitive_first_turn: bool = False

    # Risk, identity, network, and governance.
    runs_untrusted_code: bool = False
    per_user_delegation: bool = False
    private_network_required: bool = False
    gateway_must_be_enforced: bool = False

    # Operating model.
    team_can_operate_containers: bool = True


NAMED_CONTROLS: tuple[str, ...] = (
    "loop_must_be_code",
    "in_process_libraries",
    "custom_protocol",
    "custom_durable_state",
    "custom_telemetry",
)

FRONT_DOOR_FLAGS: tuple[str, ...] = (
    "user_facing_front_door",
    "front_door_must_be_platform_governed",
)


@dataclass(frozen=True)
class Criterion:
    """One rubric row: weights applied when the profile flag matches ``when``."""

    flag: str
    prompt: int
    hosted: int
    mixed: int
    reason: str
    when: bool = True

    def weight(self, runtime: str) -> int:
        return int(getattr(self, runtime))


# The rubric mirrors the decision matrix in docs/architecture.md section 7.
# Weights are relative, not calibrated measurements. Tune them with your own
# architecture review history and keep the self-test scenarios in step.
RUBRIC: tuple[Criterion, ...] = (
    Criterion(
        "loop_must_be_code", -3, 4, 2,
        "The whole job needs a fixed step order, compensation, or approval checkpoints, so the loop belongs in code.",
    ),
    Criterion(
        "in_process_libraries", -3, 3, 2,
        "Proprietary, native, or licensed libraries must run inside the loop, which only your own process can do.",
    ),
    Criterion(
        "custom_protocol", -3, 3, 1,
        "Webhook, arbitrary JSON, real-time, or agent-to-agent peer protocols point to the hosted Invocations or A2A protocols.",
    ),
    Criterion(
        "custom_durable_state", -2, 2, 1,
        "Custom durable state or working files across turns need code that owns the store and its schema.",
    ),
    Criterion(
        "custom_telemetry", -1, 2, 1,
        "Custom spans, metrics, or attributes inside the loop are only possible in your own code.",
    ),
    Criterion(
        "gap_isolatable_behind_tool", 2, -1, 0,
        "The missing capability can sit behind an OpenAPI, MCP, function, or A2A tool, so the prompt agent can stay.",
    ),
    Criterion(
        "deterministic_substeps", 1, 0, 1,
        "A few steps must be deterministic; put them in a tool or workflow the agent calls, not in instructions.",
    ),
    Criterion(
        "user_facing_front_door", 1, 0, 1,
        "Interactive users benefit from a platform-managed conversational front door.",
    ),
    Criterion(
        "front_door_must_be_platform_governed", 2, -2, 3,
        "The user-facing layer must keep platform-managed guardrails, tools, and conversation storage.",
    ),
    Criterion(
        "heavy_backend_processing", -1, 2, 2,
        "Heavy processing behind the conversation favors code, ideally kept off the conversational hot path.",
    ),
    Criterion(
        "latency_sensitive_first_turn", 2, -2, 1,
        "First-turn responsiveness matters; hosted sessions must be provisioned or resumed before they answer.",
    ),
    Criterion(
        "gateway_must_be_enforced", 2, -2, 2,
        "Model calls must go through governed deployments or an approved gateway; hosted code can call anything unless network controls stop it.",
    ),
    Criterion(
        "private_network_required", 0, -1, -1,
        "Both runtimes support private projects, but hosted code adds outbound traffic you must restrict yourself.",
    ),
    Criterion(
        "runs_untrusted_code", 0, 0, 0,
        "Neutral for the runtime choice: untrusted code goes to a session pool in every runtime.",
    ),
    Criterion(
        "per_user_delegation", 0, 0, 0,
        "Neutral for the runtime choice: per-user access needs delegated tool connections or a server-side context store in every runtime.",
    ),
    Criterion(
        "team_can_operate_containers", 0, -4, -3,
        "The team has not confirmed it can operate a container supply chain (builds, patching, registry, egress, on-call).",
        when=False,
    ),
)


@dataclass
class Recommendation:
    """Result of evaluating one profile against the rubric."""

    profile_name: str
    runtime: str
    scores: dict[str, int]
    eligible: list[str]
    named_controls: list[str]
    rationale: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Profile parsing
# ---------------------------------------------------------------------------

_PROFILE_FIELDS = {f.name: f for f in fields(WorkloadProfile)}


def profile_from_mapping(data: dict[str, Any]) -> WorkloadProfile:
    """Build a profile from a JSON-style mapping, rejecting typos and bad types."""

    if not isinstance(data, dict):
        raise ValueError("A profile must be a JSON object.")
    unknown = sorted(set(data) - set(_PROFILE_FIELDS))
    if unknown:
        raise ValueError(f"Unknown profile field(s): {', '.join(unknown)}")
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("Profile field 'name' is required and must be a non-empty string.")
    for key, value in data.items():
        if key in ("name", "description"):
            if not isinstance(value, str):
                raise ValueError(f"Profile field '{key}' must be a string.")
            continue
        if not isinstance(value, bool):
            raise ValueError(f"Profile field '{key}' must be true or false.")
    return WorkloadProfile(**data)


def profiles_from_json(payload: Any) -> list[WorkloadProfile]:
    """Accept a single profile, a list of profiles, or {"profiles": [...]}."""

    if isinstance(payload, dict) and "profiles" in payload:
        payload = payload["profiles"]
    if isinstance(payload, dict):
        return [profile_from_mapping(payload)]
    if isinstance(payload, list):
        return [profile_from_mapping(item) for item in payload]
    raise ValueError("Expected a profile object, a list of profiles, or {\"profiles\": [...]}.")


# ---------------------------------------------------------------------------
# Adapter seams
# ---------------------------------------------------------------------------


class ProfileSource(Protocol):
    """Where workload profiles come from (files, a review tool, a backlog)."""

    def load(self) -> list[WorkloadProfile]:
        ...


class ResultSink(Protocol):
    """Where recommendations go (console, ADR repository, review tool)."""

    def emit(self, profile: WorkloadProfile, recommendation: Recommendation) -> None:
        ...


class InMemoryProfileSource:
    """Profiles held in memory. Used by the self-test and by unit tests."""

    def __init__(self, profiles: Iterable[WorkloadProfile]) -> None:
        self._profiles = list(profiles)

    def load(self) -> list[WorkloadProfile]:
        return list(self._profiles)


class JsonFileProfileSource:
    """Profiles read from a local JSON file."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> list[WorkloadProfile]:
        with self._path.open(encoding="utf-8") as handle:
            return profiles_from_json(json.load(handle))


class InMemorySink:
    """Collects recommendations in memory. Used by the self-test."""

    def __init__(self) -> None:
        self.results: list[tuple[WorkloadProfile, Recommendation]] = []

    def emit(self, profile: WorkloadProfile, recommendation: Recommendation) -> None:
        self.results.append((profile, recommendation))


class StdoutSink:
    """Prints recommendations as readable text or as JSON lines."""

    def __init__(self, as_json: bool = False) -> None:
        self._as_json = as_json

    def emit(self, profile: WorkloadProfile, recommendation: Recommendation) -> None:
        if self._as_json:
            print(json.dumps(recommendation.to_dict(), sort_keys=True))
            return
        print(format_recommendation(profile, recommendation))


class AdrRepositorySink:
    """Production adapter stub: record each recommendation in an ADR repository.

    A production implementation would render the recommendation, the matrix
    rows it relied on, and the named control (if any) into an ADR draft, then
    open a pull request in the team's architecture repository or attach it to
    a work item in the review backlog. It would authenticate with a managed
    identity or a workload identity, never a personal token.

    It is intentionally not implemented here so the sample stays hermetic.
    """

    def __init__(self, repository_url: str) -> None:
        self.repository_url = repository_url

    def emit(self, profile: WorkloadProfile, recommendation: Recommendation) -> None:
        raise NotImplementedError(
            "AdrRepositorySink is a production seam. Implement it against your "
            "architecture repository or review tool."
        )


# ---------------------------------------------------------------------------
# Selection logic
# ---------------------------------------------------------------------------


def _criterion_applies(profile: WorkloadProfile, criterion: Criterion) -> bool:
    return bool(getattr(profile, criterion.flag)) is criterion.when


def evaluate(profile: WorkloadProfile) -> Recommendation:
    """Score a profile and apply the ADR 0001 eligibility rules."""

    scores = dict(BASE_SCORES)
    rationale: list[str] = []
    for criterion in RUBRIC:
        if not _criterion_applies(profile, criterion):
            continue
        for runtime in RUNTIMES:
            scores[runtime] += criterion.weight(runtime)
        rationale.append(f"[{criterion.flag}] {criterion.reason}")

    named = [flag for flag in NAMED_CONTROLS if getattr(profile, flag)]
    has_front_door = any(getattr(profile, flag) for flag in FRONT_DOOR_FLAGS)

    eligible = ["prompt"]
    if named:
        eligible.append("hosted")
        if has_front_door:
            eligible.append("mixed")

    def rank(runtime: str) -> tuple[int, int]:
        return (-scores[runtime], TIE_BREAK[runtime])

    best_overall = sorted(RUNTIMES, key=rank)[0]
    ordered = sorted(eligible, key=rank)
    runtime = ordered[0]

    warnings: list[str] = []

    if best_overall != runtime and best_overall not in eligible:
        if not named:
            warnings.append(
                f"'{best_overall}' scored highest, but no named control is present. Prefer a prompt "
                "agent with the capability behind a tool (anti-pattern: a hosted agent that only wraps a prompt)."
            )
        else:
            warnings.append(
                f"'{best_overall}' scored highest, but it needs a user-facing front door to be eligible."
            )
        rationale.append(
            f"Eligibility rule: '{best_overall}' is not eligible for this profile, so '{runtime}' is recommended."
        )

    if len(ordered) > 1 and scores[ordered[0]] - scores[ordered[1]] <= CLOSE_CALL_MARGIN:
        warnings.append(
            f"Close call between '{ordered[0]}' and '{ordered[1]}'. Review the decision matrix rows "
            "for your hard requirements before you commit."
        )

    uses_hosted = runtime in ("hosted", "mixed")

    if runtime == "prompt" and named:
        warnings.append(
            "Named control(s) present but a prompt agent is recommended: "
            + ", ".join(named)
            + ". Confirm each can be met behind a tool or in the caller; otherwise revisit the decision."
        )
    if runtime == "prompt" and profile.deterministic_substeps:
        warnings.append(
            "Keep deterministic steps in a tool or workflow the agent calls. Instructions are not a state machine."
        )
    if profile.runs_untrusted_code:
        warnings.append(
            "Run untrusted or model-generated code in a session pool with egress disabled and a separate "
            "identity (or the platform Code Interpreter tool for a prompt agent). Never run it in the agent's process."
        )
    if profile.per_user_delegation:
        warnings.append(
            "Per-user access: use MCP or Toolbox OAuth, channel on-behalf-of support, or a server-side context "
            "store. Never pass user tokens in prompts, tool parameters, or headers; do not assume the caller's "
            "token reaches hosted code."
        )
    if uses_hosted and profile.latency_sensitive_first_turn:
        warnings.append(
            "Hosted sessions scale to zero and must be provisioned or resumed. Reuse sessions, keep images lean, "
            "and keep the hosted hop off the hot path behind a prompt front door where possible."
        )
    if uses_hosted and profile.private_network_required:
        warnings.append(
            "Hosted agent outbound traffic is open unless you restrict it. Use a delegated subnet with a "
            "firewall allowlist that still permits identity and platform endpoints."
        )
    if uses_hosted and profile.gateway_must_be_enforced:
        warnings.append(
            "Hosted code can call any model endpoint. Enforce governed deployments or the approved gateway "
            "with egress rules and policy, not convention."
        )
    if uses_hosted and not profile.team_can_operate_containers:
        warnings.append(
            "The team has not confirmed it can operate a container supply chain. Build that capability first, "
            "or narrow the scope until a prompt agent fits."
        )
    if runtime == "mixed":
        warnings.append(
            "In a mixed design, let exactly one layer plan and the other execute, and propagate trace context "
            "across the tool boundary."
        )

    rationale.append(
        f"Recommended '{runtime}': highest score among eligible runtimes ({', '.join(eligible)})."
    )
    return Recommendation(
        profile_name=profile.name,
        runtime=runtime,
        scores=scores,
        eligible=eligible,
        named_controls=named,
        rationale=rationale,
        warnings=warnings,
    )


def run(source: ProfileSource, sink: ResultSink) -> list[Recommendation]:
    """Evaluate every profile from ``source`` and send results to ``sink``."""

    results: list[Recommendation] = []
    for profile in source.load():
        recommendation = evaluate(profile)
        sink.emit(profile, recommendation)
        results.append(recommendation)
    return results


def format_recommendation(profile: WorkloadProfile, rec: Recommendation) -> str:
    score_text = " ".join(f"{name}={rec.scores[name]}" for name in RUNTIMES)
    lines = [
        f"Workload: {profile.name}",
        f"Recommendation: {rec.runtime.upper()}",
        f"Scores: {score_text} (eligible: {', '.join(rec.eligible)})",
        f"Named controls: {', '.join(rec.named_controls) if rec.named_controls else 'none'}",
        "Rationale:",
    ]
    lines.extend(f"  - {item}" for item in rec.rationale)
    lines.append("Warnings:" if rec.warnings else "Warnings: none")
    lines.extend(f"  - {item}" for item in rec.warnings)
    return "\n".join(lines) + "\n"


def format_rubric() -> str:
    lines = [f"Base scores: {BASE_SCORES}", "", "flag | applies when | prompt | hosted | mixed | reason"]
    for c in RUBRIC:
        lines.append(f"{c.flag} | {str(c.when).lower()} | {c.prompt:+d} | {c.hosted:+d} | {c.mixed:+d} | {c.reason}")
    lines.append("")
    lines.append("Named controls (needed for hosted or mixed): " + ", ".join(NAMED_CONTROLS))
    lines.append("Front-door flags (needed for mixed): " + ", ".join(FRONT_DOOR_FLAGS))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scenario:
    profile: WorkloadProfile
    expected_runtime: str
    expected_warning_fragments: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        WorkloadProfile(
            name="faq-rag-assistant",
            description="Employee policy Q&A grounded on approved documents, used in Teams and on the intranet.",
            user_facing_front_door=True,
            latency_sensitive_first_turn=True,
            gateway_must_be_enforced=True,
        ),
        expected_runtime="prompt",
    ),
    Scenario(
        WorkloadProfile(
            name="proprietary-multistep-workflow",
            description="Back-office onboarding pipeline with fixed steps, in-house scoring libraries, and audit spans.",
            loop_must_be_code=True,
            in_process_libraries=True,
            custom_durable_state=True,
            custom_telemetry=True,
            private_network_required=True,
        ),
        expected_runtime="hosted",
        expected_warning_fragments=("outbound traffic is open",),
    ),
    Scenario(
        WorkloadProfile(
            name="m365-front-door-heavy-backend",
            description="Advisor assistant in Microsoft 365 that calls proprietary portfolio analytics.",
            in_process_libraries=True,
            user_facing_front_door=True,
            front_door_must_be_platform_governed=True,
            heavy_backend_processing=True,
            latency_sensitive_first_turn=True,
            gateway_must_be_enforced=True,
            per_user_delegation=True,
        ),
        expected_runtime="mixed",
        expected_warning_fragments=("exactly one layer plan", "Per-user access"),
    ),
    Scenario(
        WorkloadProfile(
            name="untrusted-code-analysis",
            description="Data analysis agent that runs user-supplied scripts with custom libraries and working files.",
            in_process_libraries=True,
            custom_durable_state=True,
            custom_telemetry=True,
            runs_untrusted_code=True,
        ),
        expected_runtime="hosted",
        expected_warning_fragments=("session pool",),
    ),
    Scenario(
        WorkloadProfile(
            name="webhook-custom-protocol",
            description="Returns triage driven by commerce webhooks with custom JSON payloads and idempotency state.",
            custom_protocol=True,
            custom_durable_state=True,
            custom_telemetry=True,
            private_network_required=True,
        ),
        expected_runtime="hosted",
    ),
    Scenario(
        WorkloadProfile(
            name="heavy-processing-without-named-control",
            description="Batch summarization where the heavy work is an existing API, with no need for custom code in the loop.",
            heavy_backend_processing=True,
        ),
        expected_runtime="prompt",
        expected_warning_fragments=("no named control",),
    ),
)


def _check(condition: bool, label: str, detail: str, failures: list[str]) -> None:
    if condition:
        print(f"PASS {label}")
    else:
        print(f"FAIL {label}: {detail}")
        failures.append(label)


def self_test() -> int:
    failures: list[str] = []

    sink = InMemorySink()
    run(InMemoryProfileSource(s.profile for s in SCENARIOS), sink)
    by_name = {rec.profile_name: rec for _, rec in sink.results}

    for scenario in SCENARIOS:
        rec = by_name[scenario.profile.name]
        missing = [
            fragment
            for fragment in scenario.expected_warning_fragments
            if not any(fragment.lower() in warning.lower() for warning in rec.warnings)
        ]
        ok = rec.runtime == scenario.expected_runtime and not missing
        detail = (
            f"expected {scenario.expected_runtime}, got {rec.runtime}; "
            f"scores={rec.scores}; missing warnings={missing}"
        )
        _check(ok, f"{scenario.profile.name} -> {rec.runtime}", detail, failures)

    # Adapter and validation checks.
    try:
        profile_from_mapping({"name": "typo", "loop_must_be_kode": True})
        _check(False, "profile-rejects-unknown-field", "no error raised", failures)
    except ValueError:
        _check(True, "profile-rejects-unknown-field", "", failures)

    try:
        profile_from_mapping({"name": "bad-type", "custom_protocol": "yes"})
        _check(False, "profile-rejects-non-boolean", "no error raised", failures)
    except ValueError:
        _check(True, "profile-rejects-non-boolean", "", failures)

    parsed = profiles_from_json({"profiles": [{"name": "a"}, {"name": "b", "custom_protocol": True}]})
    bare_list = profiles_from_json([{"name": "c"}])
    single = profiles_from_json({"name": "d", "team_can_operate_containers": False})
    _check(
        [p.name for p in parsed] == ["a", "b"]
        and parsed[1].custom_protocol
        and [p.name for p in bare_list] == ["c"]
        and len(single) == 1
        and not single[0].team_can_operate_containers,
        "json-envelope-parses",
        f"parsed={parsed}; bare_list={bare_list}; single={single}",
        failures,
    )

    try:
        AdrRepositorySink("https://example.com/architecture").emit(SCENARIOS[0].profile, by_name["faq-rag-assistant"])
        _check(False, "production-sink-is-gated", "stub did not raise", failures)
    except NotImplementedError:
        _check(True, "production-sink-is-gated", "", failures)

    total = len(SCENARIOS) + 4
    print(f"{total - len(failures)}/{total} checks passed")
    return 0 if not failures else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Recommend a Foundry agent runtime (prompt, hosted, or mixed) for a workload profile."
    )
    parser.add_argument("--profile", type=Path, help="JSON file with one profile, a list, or {\"profiles\": [...]}.")
    parser.add_argument("--json", action="store_true", help="Emit one JSON object per profile.")
    parser.add_argument("--self-test", action="store_true", help="Run the offline reference scenarios.")
    parser.add_argument("--list-criteria", action="store_true", help="Print the rubric and exit.")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.list_criteria:
        print(format_rubric())
        return 0
    if args.profile:
        try:
            run(JsonFileProfileSource(args.profile), StdoutSink(as_json=args.json))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
