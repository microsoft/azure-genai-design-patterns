"""Entitlement-aware MCP tool catalog - runnable reference sample.

Teaching sample (Python 3.10+, standard library only, no network). It shows the
core of a multi-tenant MCP gateway (or any agent tool host) that:

* derives the caller's tenant from a *verified* token claim and an
  authoritative entitlement registry, never from client-supplied headers;
* filters ``tools/list`` per tenant, principal class (normalized app roles),
  entitlement version and catalog version, and caches the filtered view;
* removes entitlement-gated input-schema properties and applies tenant-neutral
  description variants, so each caller sees only what it may use;
* re-checks entitlement on every ``tools/call`` and denies with a uniform
  ``Unknown tool`` JSON-RPC error, so a denied call does not reveal whether an
  unentitled tool exists;
* fails closed when the registry is unavailable, with an explicit
  ``max_staleness`` policy for serving a bounded stale snapshot;
* paginates the *filtered* list with an opaque, HMAC-protected cursor bound to
  the tenant, the principal class and the filtered-view version;
* sends ``notifications/tools/list_changed`` only to sessions whose visible
  catalog changed;
* writes audit events without tool arguments, results or tokens.

The token validator and the registry are in-memory test doubles. The production
seams (``EntraTokenValidator`` and ``HttpEntitlementRegistryClient``) raise
``NotImplementedError``.

Usage::

    python entitled_tool_catalog.py --self-test
    python entitled_tool_catalog.py --self-test --domain fabrikam-health
    python entitled_tool_catalog.py --domain all
    python entitled_tool_catalog.py --demo --domain northwind-logistics

All names are fictitious (Contoso, Fabrikam, Northwind and their example
tenants). Clock values are abstract test ticks. The TTL and staleness values in
the Domain Packs are illustrative parameters, not recommendations.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import re
import secrets
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Mapping, Protocol, Sequence

# ---------------------------------------------------------------------------
# Wire constants
# ---------------------------------------------------------------------------

JSONRPC = "2.0"
PROTOCOL_VERSION = "2025-11-25"
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
# Implementation-defined server errors (JSON-RPC reserves -32000..-32099).
# These two are this design's convention. Document yours and keep them stable.
FORBIDDEN = -32001
ENTITLEMENT_UNAVAILABLE = -32003

TOOL_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
RESOURCE_METADATA_URL = "https://mcp.contoso.example/.well-known/oauth-protected-resource/mcp"
# Headers a client might send to "help" the gateway pick a tenant. They are
# never read for authorization; their presence is only recorded as a signal.
CLIENT_TENANT_HINT_HEADERS = ("x-tenant-id", "x-org-id", "x-customer-id", "x-account-id")


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> float: ...


class FakeClock:
    """Injectable test clock. Units are abstract ticks, not seconds."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, ticks: float) -> None:
        self._t += ticks


# ---------------------------------------------------------------------------
# Identity: only a TokenValidator can produce a VerifiedPrincipal
# ---------------------------------------------------------------------------


class Unauthenticated(Exception):
    """No credential, or the credential failed validation."""


_SEAL = object()


@dataclass(frozen=True)
class VerifiedPrincipal:
    directory_tenant_id: str  # Entra `tid` claim (a GUID in real tokens)
    object_id: str  # Entra `oid` claim
    roles: frozenset[str]  # app roles (`roles` claim)
    client_app_id: str  # `azp` / `appid`
    _seal: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._seal is not _SEAL:
            raise TypeError("VerifiedPrincipal can only be created by a TokenValidator")


class TokenValidator(Protocol):
    def validate(self, bearer: str | None) -> VerifiedPrincipal: ...


class FakeTokenValidator:
    """Test double. Tokens are random handles in a table, NOT JWTs."""

    def __init__(self, clock: Clock, lifetime: float) -> None:
        self._clock = clock
        self._lifetime = lifetime
        self._tokens: dict[str, dict[str, Any]] = {}

    def issue(self, directory_tenant_id: str, object_id: str, roles: frozenset[str],
              client_app_id: str = "client-app-demo") -> str:
        token = "test-token." + secrets.token_urlsafe(18)
        self._tokens[token] = {
            "tid": directory_tenant_id,
            "oid": object_id,
            "roles": frozenset(roles),
            "azp": client_app_id,
            "exp": self._clock.now() + self._lifetime,
        }
        return token

    def validate(self, bearer: str | None) -> VerifiedPrincipal:
        if not bearer:
            raise Unauthenticated("missing credential")
        claims = self._tokens.get(bearer)
        if claims is None:
            raise Unauthenticated("invalid credential")
        if self._clock.now() >= claims["exp"]:
            raise Unauthenticated("expired credential")
        return VerifiedPrincipal(claims["tid"], claims["oid"], claims["roles"], claims["azp"], _seal=_SEAL)


class EntraTokenValidator:
    """Production seam (not implemented in this offline sample).

    A real implementation validates a Microsoft Entra access token with a
    maintained library: signature against the issuer's published signing keys,
    issuer, audience (this gateway's MCP resource identifier), ``nbf``/``exp``,
    the allowed client applications (``azp``/``appid``), and then reads ``tid``,
    ``oid`` and app roles (``roles``) or delegated scopes (``scp``).
    """

    def validate(self, bearer: str | None) -> VerifiedPrincipal:
        raise NotImplementedError("Wire this to Microsoft Entra token validation in your gateway or MCP server.")


# ---------------------------------------------------------------------------
# Entitlement registry (authoritative source of tenant entitlements)
# ---------------------------------------------------------------------------


class RegistryUnavailable(Exception):
    """The registry could not be reached or returned an error."""


@dataclass(frozen=True)
class Entitlements:
    tenant_id: str  # internal tenant id, resolved by the registry
    version: int  # monotonically increasing per tenant
    modules: frozenset[str]  # licensed modules
    feature_flags: frozenset[str]
    description_variants: Mapping[str, str]  # tool name -> tenant-neutral variant id


class EntitlementRegistry(Protocol):
    def resolve(self, directory_tenant_id: str) -> Entitlements | None: ...

    def subscribe(self, callback: Callable[[str, int], None]) -> None: ...


@dataclass(frozen=True)
class TenantSeed:
    tenant_id: str
    directory_tenant_id: str
    display_name: str
    modules: frozenset[str]
    feature_flags: frozenset[str] = frozenset()
    description_variants: Mapping[str, str] = field(default_factory=dict)


class FakeEntitlementRegistry:
    """In-memory registry with version stamps, change events and a simulated outage."""

    def __init__(self) -> None:
        self._records: dict[str, dict[str, Any]] = {}  # by directory tenant id
        self._subscribers: list[Callable[[str, int], None]] = []
        self.calls = 0
        self.outage = False
        self.deliver_events = True

    def add_tenant(self, seed: TenantSeed, version: int = 1) -> None:
        self._records[seed.directory_tenant_id] = {
            "tenant_id": seed.tenant_id,
            "version": version,
            "modules": set(seed.modules),
            "feature_flags": set(seed.feature_flags),
            "description_variants": dict(seed.description_variants),
        }

    def resolve(self, directory_tenant_id: str) -> Entitlements | None:
        self.calls += 1
        if self.outage:
            raise RegistryUnavailable("simulated registry outage")
        rec = self._records.get(directory_tenant_id)
        if rec is None:
            return None
        return Entitlements(rec["tenant_id"], rec["version"], frozenset(rec["modules"]),
                            frozenset(rec["feature_flags"]), dict(rec["description_variants"]))

    def subscribe(self, callback: Callable[[str, int], None]) -> None:
        self._subscribers.append(callback)

    def _record_for(self, tenant_id: str) -> dict[str, Any]:
        for rec in self._records.values():
            if rec["tenant_id"] == tenant_id:
                return rec
        raise KeyError(tenant_id)

    def change(self, tenant_id: str, *, add_modules: Sequence[str] = (), remove_modules: Sequence[str] = (),
               add_flags: Sequence[str] = (), remove_flags: Sequence[str] = ()) -> int:
        """Apply an entitlement change, bump the version and publish a change event."""
        rec = self._record_for(tenant_id)
        rec["modules"].update(add_modules)
        rec["modules"].difference_update(remove_modules)
        rec["feature_flags"].update(add_flags)
        rec["feature_flags"].difference_update(remove_flags)
        rec["version"] += 1
        if self.deliver_events:
            for callback in self._subscribers:
                callback(tenant_id, rec["version"])
        return rec["version"]

    def set_version(self, tenant_id: str, version: int) -> None:
        self._record_for(tenant_id)["version"] = version

    def wire_response(self, directory_tenant_id: str) -> dict[str, Any]:
        """The JSON shape a registry API might return (see docs/architecture.md)."""
        rec = self._records[directory_tenant_id]
        return {
            "tenantId": rec["tenant_id"],
            "entitlementVersion": rec["version"],
            "modules": sorted(rec["modules"]),
            "featureFlags": sorted(rec["feature_flags"]),
            "descriptionVariants": dict(sorted(rec["description_variants"].items())),
        }


class HttpEntitlementRegistryClient:
    """Production seam (not implemented in this offline sample).

    A real client calls your tenant registry or licensing API with the gateway's
    managed identity, maps the verified directory tenant id to the internal
    tenant id, returns modules, feature flags and a version stamp, and
    subscribes to entitlement-change events (for example through a message
    broker or an event-routing service).
    """

    def resolve(self, directory_tenant_id: str) -> Entitlements | None:
        raise NotImplementedError("Call your tenant registry or licensing API with a managed identity.")

    def subscribe(self, callback: Callable[[str, int], None]) -> None:
        raise NotImplementedError("Subscribe to entitlement-change events from your registry.")


# ---------------------------------------------------------------------------
# Governed tool catalog
# ---------------------------------------------------------------------------


class CatalogLintError(ValueError):
    """A tool definition failed catalog hygiene checks and was not published."""


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    title: str
    description: str  # shared text: must never contain tenant data
    module: str  # licensed module that entitles the tool
    input_schema: Mapping[str, Any]
    roles: frozenset[str] = frozenset()  # any-of; empty means any principal of an entitled tenant
    feature_flag: str | None = None
    read_only: bool = True
    discoverable: bool = False  # only used by the opt-in "explicit" disclosure mode
    variants: Mapping[str, str] = field(default_factory=dict)  # neutral variant id -> description


class ToolCatalog:
    """Single governed catalog shared by all tenants. Every publish bumps the version."""

    def __init__(self, forbidden_terms: Sequence[str]) -> None:
        self.version = 0
        self._tools: dict[str, ToolDefinition] = {}
        self._forbidden = [t.lower() for t in forbidden_terms if t]

    def publish(self, tool: ToolDefinition) -> None:
        self._lint(tool)
        self._tools[tool.name] = tool
        self.version += 1

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return [self._tools[n] for n in sorted(self._tools)]

    @property
    def relevant_roles(self) -> frozenset[str]:
        roles: set[str] = set()
        for tool in self._tools.values():
            roles.update(tool.roles)
        return frozenset(roles)

    def _lint(self, tool: ToolDefinition) -> None:
        if not TOOL_NAME_RE.match(tool.name):
            raise CatalogLintError("tool name must be 1-128 characters of [A-Za-z0-9_.-]")
        if tool.name in self._tools and self._tools[tool.name].module != tool.module:
            raise CatalogLintError("a published tool name cannot move to another module; version the name instead")
        text = json.dumps([tool.title, tool.description, dict(tool.variants), tool.input_schema]).lower()
        if any(term in text for term in self._forbidden):
            raise CatalogLintError(f"tool {tool.name} contains a tenant-identifying term in shared metadata")


# ---------------------------------------------------------------------------
# Filtering: entitlement + role -> per-principal-class view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PrincipalClass:
    """Roles normalized to the ones the catalog references. Users with the same
    class share a cached view; the class never contains user identifiers."""

    label: str
    roles: frozenset[str]


def principal_class(roles: frozenset[str], relevant: frozenset[str]) -> PrincipalClass:
    kept = sorted(set(roles) & set(relevant))
    return PrincipalClass("+".join(kept) if kept else "base", frozenset(kept))


def denial_reason(tool: ToolDefinition, ent: Entitlements, roles: frozenset[str]) -> str | None:
    """None when the tool is entitled for this tenant and principal class."""
    if tool.module not in ent.modules:
        return "not_entitled"
    if tool.feature_flag and tool.feature_flag not in ent.feature_flags:
        return "not_entitled"
    if tool.roles and not (tool.roles & roles):
        return "role_required"
    return None


@dataclass(frozen=True)
class CatalogView:
    version: str  # e<entitlement version>.c<catalog version>, for audit
    digest: str  # content hash of the filtered view; cursors bind to it
    tools: tuple[Mapping[str, Any], ...]
    names: frozenset[str]


# A top-level input property may carry this annotation. The property is removed
# from the view of callers whose tenant lacks the flag, and the annotation itself
# is never emitted, so internal flag names do not leak.
ENTITLEMENT_FLAG_KEY = "x-entitlement-flag"


def minimize_schema(schema: Mapping[str, Any], flags: frozenset[str]) -> dict[str, Any]:
    out = json.loads(json.dumps(schema))
    props = out.get("properties")
    if isinstance(props, dict):
        for pname in list(props):
            prop = props[pname]
            flag = prop.pop(ENTITLEMENT_FLAG_KEY, None) if isinstance(prop, dict) else None
            if flag is not None and flag not in flags:
                del props[pname]
        if isinstance(out.get("required"), list):
            out["required"] = [r for r in out["required"] if r in props]
    return out


def build_view(catalog: ToolCatalog, ent: Entitlements, pclass: PrincipalClass) -> CatalogView:
    tools: list[dict[str, Any]] = []
    for tool in catalog.all():
        if denial_reason(tool, ent, pclass.roles) is not None:
            continue
        description = tool.description
        variant_id = ent.description_variants.get(tool.name)
        if variant_id and variant_id in tool.variants:
            description = tool.variants[variant_id]
        tools.append({
            "name": tool.name,
            "title": tool.title,
            "description": description,
            "inputSchema": minimize_schema(tool.input_schema, ent.feature_flags),
            "annotations": {"readOnlyHint": tool.read_only},
        })
    digest = hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()[:16]
    return CatalogView(f"e{ent.version}.c{catalog.version}", digest, tuple(tools),
                       frozenset(t["name"] for t in tools))


ViewKeyFn = Callable[[str, PrincipalClass, int, int], tuple]


def secure_view_key(tenant_id: str, pclass: PrincipalClass, ent_version: int, catalog_version: int) -> tuple:
    return ("view", tenant_id, pclass.label, ent_version, catalog_version)


def insecure_view_key_without_tenant(tenant_id: str, pclass: PrincipalClass, ent_version: int,
                                     catalog_version: int) -> tuple:
    """ANTI-PATTERN baseline used only by the self-test. Do not copy it."""
    return ("view", pclass.label, ent_version, catalog_version)


class ViewCache:
    """Filtered views are deterministic for their key, so they need no TTL.
    Freshness is governed by the entitlement snapshot that produced the key."""

    def __init__(self, max_entries: int) -> None:
        self._max = max_entries
        self._entries: dict[tuple, CatalogView] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: tuple) -> CatalogView | None:
        view = self._entries.get(key)
        if view is None:
            self.misses += 1
        else:
            self.hits += 1
        return view

    def put(self, key: tuple, view: CatalogView) -> None:
        if len(self._entries) >= self._max:
            self._entries.pop(next(iter(self._entries)))
        self._entries[key] = view

    def keys(self) -> list[tuple]:
        return list(self._entries)


# ---------------------------------------------------------------------------
# Entitlement snapshot cache: TTL, negative cache, bounded staleness, floors
# ---------------------------------------------------------------------------


class EntitlementUnavailable(Exception):
    """Fail closed: no usable entitlement snapshot."""


class TenantNotOnboarded(Exception):
    """The verified directory tenant is not registered."""


@dataclass(frozen=True)
class CachePolicy:
    entitlement_ttl: float  # fresh window for a snapshot
    negative_ttl: float  # how long "tenant not registered" is remembered
    max_staleness: float  # extra window to serve a stale snapshot during a registry outage; 0 = never
    stale_call_scope: str = "read-only"  # "none" | "read-only" | "all": calls allowed on a stale snapshot
    allow_explicit_disclosure: bool = True  # may the gateway opt in to explicit not-entitled errors?

    def __post_init__(self) -> None:
        if self.stale_call_scope not in ("none", "read-only", "all"):
            raise ValueError("stale_call_scope must be none, read-only or all")
        if min(self.entitlement_ttl, self.negative_ttl, self.max_staleness) < 0:
            raise ValueError("TTL and staleness parameters must be non-negative")


@dataclass(frozen=True)
class Resolution:
    ent: Entitlements
    freshness: str  # "hit" | "miss" | "stale"
    age: float


@dataclass
class _Snapshot:
    ent: Entitlements
    fetched_at: float


class EntitlementCache:
    def __init__(self, registry: EntitlementRegistry, clock: Clock, policy: CachePolicy) -> None:
        self._registry = registry
        self._clock = clock
        self._policy = policy
        self._positive: dict[str, _Snapshot] = {}  # by directory tenant id
        self._negative: dict[str, float] = {}  # directory tenant id -> cached at
        self._floor: dict[str, int] = {}  # tenant id -> minimum acceptable version

    def _usable(self, snap: _Snapshot) -> bool:
        return snap.ent.version >= self._floor.get(snap.ent.tenant_id, 0)

    def resolve(self, directory_tenant_id: str) -> Resolution:
        now = self._clock.now()
        snap = self._positive.get(directory_tenant_id)
        if snap and self._usable(snap) and now - snap.fetched_at < self._policy.entitlement_ttl:
            return Resolution(snap.ent, "hit", now - snap.fetched_at)
        cached_at = self._negative.get(directory_tenant_id)
        if cached_at is not None and now - cached_at < self._policy.negative_ttl:
            raise TenantNotOnboarded(directory_tenant_id)
        try:
            ent = self._registry.resolve(directory_tenant_id)
        except RegistryUnavailable as exc:
            limit = self._policy.entitlement_ttl + self._policy.max_staleness
            if snap and self._usable(snap) and now - snap.fetched_at < limit:
                return Resolution(snap.ent, "stale", now - snap.fetched_at)
            raise EntitlementUnavailable("registry unavailable and no usable snapshot") from exc
        if ent is None:
            self._positive.pop(directory_tenant_id, None)
            self._negative[directory_tenant_id] = now
            raise TenantNotOnboarded(directory_tenant_id)
        if ent.version < self._floor.get(ent.tenant_id, 0):
            # A lagging replica returned a version older than a change event we already saw.
            raise EntitlementUnavailable("registry returned an outdated entitlement version")
        self._negative.pop(directory_tenant_id, None)
        self._positive[directory_tenant_id] = _Snapshot(ent, now)
        return Resolution(ent, "miss", 0.0)

    def invalidate(self, tenant_id: str, min_version: int) -> None:
        """Called on an entitlement-change event. Older snapshots become unusable,
        even inside the staleness window."""
        self._floor[tenant_id] = max(self._floor.get(tenant_id, 0), min_version)


# ---------------------------------------------------------------------------
# Opaque cursors bound to the filtered view
# ---------------------------------------------------------------------------


class CursorInvalid(Exception):
    pass


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class CursorCodec:
    """Cursor = base64url(payload) "." base64url(HMAC). The payload carries only
    a binding digest and an offset, so nothing readable leaks to the client.
    The key here is ephemeral; a multi-instance gateway shares a managed,
    rotated key instead."""

    def __init__(self, key: bytes | None = None) -> None:
        self._key = key or secrets.token_bytes(32)

    @staticmethod
    def _binding(tenant_id: str, pclass: PrincipalClass, view_digest: str) -> str:
        return hashlib.sha256(f"{tenant_id}|{pclass.label}|{view_digest}".encode()).hexdigest()[:24]

    def encode(self, tenant_id: str, pclass: PrincipalClass, view_digest: str, offset: int) -> str:
        payload = json.dumps({"b": self._binding(tenant_id, pclass, view_digest), "o": offset},
                             separators=(",", ":")).encode()
        mac = hmac.new(self._key, payload, hashlib.sha256).digest()[:16]
        return f"{_b64e(payload)}.{_b64e(mac)}"

    def decode(self, cursor: Any, tenant_id: str, pclass: PrincipalClass, view_digest: str) -> int:
        if not isinstance(cursor, str) or cursor.count(".") != 1:
            raise CursorInvalid("malformed")
        try:
            payload_text, mac_text = cursor.split(".")
            payload, mac = _b64d(payload_text), _b64d(mac_text)
        except (ValueError, TypeError) as exc:
            raise CursorInvalid("malformed") from exc
        expected = hmac.new(self._key, payload, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(mac, expected):
            raise CursorInvalid("signature")
        data = json.loads(payload)
        if data.get("b") != self._binding(tenant_id, pclass, view_digest):
            raise CursorInvalid("binding")
        offset = data.get("o")
        if not isinstance(offset, int) or offset < 0:
            raise CursorInvalid("offset")
        return offset


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def safe_tool_name(name: Any) -> str:
    """Record a caller-supplied tool name only if it is a valid name; otherwise a hash."""
    if isinstance(name, str) and TOOL_NAME_RE.match(name):
        return name
    return "invalid:" + hashlib.sha256(repr(name).encode()).hexdigest()[:12]


class AuditLog:
    """One record per decision. Never stores tool arguments, results or tokens."""

    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append({"t": self._clock.now(), "event": event, **fields})

    def of(self, event: str) -> list[dict[str, Any]]:
        return [e for e in self.events if e["event"] == event]

    def lines(self) -> list[str]:
        return [json.dumps(e, sort_keys=True) for e in self.events]


# ---------------------------------------------------------------------------
# Backend seam
# ---------------------------------------------------------------------------


class ToolBackend(Protocol):
    def invoke(self, tool: ToolDefinition, tenant_id: str, principal: VerifiedPrincipal,
               arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class FakeToolBackend:
    """Records which tool ran for which tenant. Never sees the caller's token."""

    def __init__(self) -> None:
        self.invocations: list[tuple[str, str]] = []

    def invoke(self, tool: ToolDefinition, tenant_id: str, principal: VerifiedPrincipal,
               arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.invocations.append((tool.name, tenant_id))
        return {"status": "ok", "tool": tool.name}


class ForwardingToolBackend:
    """Production seam (not implemented in this offline sample).

    A real backend calls the domain API with a credential minted for that API
    (managed identity, or on-behalf-of when the API needs the user), passes the
    resolved tenant id from this gateway, and lets the domain API apply its own
    record-level authorization. Never forward the caller's MCP access token.
    """

    def invoke(self, tool: ToolDefinition, tenant_id: str, principal: VerifiedPrincipal,
               arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        raise NotImplementedError("Call the domain API with a backend credential, not the caller's token.")


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass
class GatewayResponse:
    status: int
    body: dict[str, Any] | None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Session:
    session_id: str
    directory_tenant_id: str
    object_id: str
    tenant_id: str
    roles: frozenset[str]
    listed_digest: str | None = None  # digest of the view this session last listed
    list_changed_pending: bool = False
    outbox: list[dict[str, Any]] = field(default_factory=list)  # server-to-client notifications


def _result(msg_id: Any, result: Mapping[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC, "id": msg_id, "error": err}


def _unknown_tool(msg_id: Any, name: Any) -> dict[str, Any]:
    """Same error for "does not exist" and "exists but not for you"."""
    if isinstance(name, str) and TOOL_NAME_RE.match(name):
        return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
    return _error(msg_id, INVALID_PARAMS, "Unknown tool")


_JSON_TYPES: dict[str, Any] = {"string": str, "boolean": bool, "integer": int, "number": (int, float),
                               "object": dict, "array": list}


def _arguments_match(schema: Mapping[str, Any], args: Any) -> bool:
    """Minimal top-level check against the caller's minimized schema.
    Use a full JSON Schema validator in production."""
    if not isinstance(args, dict):
        return False
    props = schema.get("properties", {})
    if schema.get("additionalProperties") is False and not set(args) <= set(props):
        return False
    if any(r not in args for r in schema.get("required", [])):
        return False
    for key, value in args.items():
        expected = props.get(key, {}).get("type")
        py_type = _JSON_TYPES.get(expected)
        if py_type is None:
            continue
        if not isinstance(value, py_type) or (expected in ("integer", "number") and isinstance(value, bool)):
            return False
    return True


class EntitledToolGateway:
    """MCP request handler: authenticate, resolve entitlements, filter, enforce, audit."""

    def __init__(self, *, validator: TokenValidator, registry: EntitlementRegistry, catalog: ToolCatalog,
                 policy: CachePolicy, clock: Clock, audit: AuditLog, backend: ToolBackend,
                 view_key: ViewKeyFn = secure_view_key, page_size: int = 50, disclosure: str = "uniform",
                 cursor_codec: CursorCodec | None = None, view_cache_entries: int = 256) -> None:
        if disclosure not in ("uniform", "explicit"):
            raise ValueError("disclosure must be uniform or explicit")
        if disclosure == "explicit" and not policy.allow_explicit_disclosure:
            raise ValueError("this policy does not allow explicit not-entitled errors")
        if page_size < 1:
            raise ValueError("page_size must be positive")
        self._validator = validator
        self._catalog = catalog
        self._policy = policy
        self._audit = audit
        self._backend = backend
        self._view_key = view_key
        self._page_size = page_size
        self._disclosure = disclosure
        self._cursors = cursor_codec or CursorCodec()
        self.entitlements = EntitlementCache(registry, clock, policy)
        self.views = ViewCache(view_cache_entries)
        self.sessions: dict[str, Session] = {}
        registry.subscribe(self._on_entitlement_change)

    # -- request entry point ------------------------------------------------

    def handle(self, headers: Mapping[str, str], message: Any) -> GatewayResponse:
        h = {k.lower(): v for k, v in headers.items()}
        auth = h.get("authorization", "")
        bearer = auth[7:].strip() if auth[:7].lower() == "bearer " else None
        try:
            principal = self._validator.validate(bearer)
        except Unauthenticated as exc:
            self._audit.emit("auth_failed", reason=str(exc))
            return GatewayResponse(401, None, {"www-authenticate": f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'})

        hints = sorted(n for n in CLIENT_TENANT_HINT_HEADERS if n in h)
        if hints:
            # Names only: the values are untrusted and may carry another tenant's identifiers.
            self._audit.emit("client_tenant_hint_ignored", headers=hints, subject=principal.object_id,
                             directory_tenant_id=principal.directory_tenant_id)

        if not isinstance(message, dict) or message.get("jsonrpc") != JSONRPC or not isinstance(message.get("method"), str):
            msg_id = message.get("id") if isinstance(message, dict) else None
            return GatewayResponse(400, _error(msg_id, INVALID_REQUEST, "Invalid Request"))
        method: str = message["method"]
        msg_id = message.get("id")
        params = message.get("params", {})
        if not isinstance(params, dict):
            return GatewayResponse(200, _error(msg_id, INVALID_PARAMS, "Invalid params"))

        session: Session | None = None
        sid = h.get("mcp-session-id")
        if method != "initialize" and sid is not None:
            session = self.sessions.get(sid)
            owner = (principal.directory_tenant_id, principal.object_id)
            if session is None or (session.directory_tenant_id, session.object_id) != owner:
                self._audit.emit("session_rejected", subject=principal.object_id,
                                 directory_tenant_id=principal.directory_tenant_id,
                                 reason="unknown" if session is None else "principal_mismatch")
                return GatewayResponse(404, _error(msg_id, INVALID_REQUEST, "Session not found"))

        if method.startswith("notifications/"):
            return GatewayResponse(202, None)
        if method == "ping":
            return GatewayResponse(200, _result(msg_id, {}))

        try:
            res = self.entitlements.resolve(principal.directory_tenant_id)
        except TenantNotOnboarded:
            self._audit.emit("tenant_not_onboarded", subject=principal.object_id,
                             directory_tenant_id=principal.directory_tenant_id)
            return GatewayResponse(403, _error(msg_id, FORBIDDEN, "Forbidden"))
        except EntitlementUnavailable as exc:
            self._audit.emit("entitlements_unavailable", subject=principal.object_id,
                             directory_tenant_id=principal.directory_tenant_id, method=method, reason=str(exc))
            return GatewayResponse(503, _error(msg_id, ENTITLEMENT_UNAVAILABLE, "Entitlements temporarily unavailable"))

        pclass = principal_class(principal.roles, self._catalog.relevant_roles)
        if method == "initialize":
            return self._initialize(msg_id, principal, res)
        if method == "tools/list":
            return self._tools_list(msg_id, params, principal, res, pclass, session)
        if method == "tools/call":
            return self._tools_call(msg_id, params, principal, res, pclass)
        return GatewayResponse(200, _error(msg_id, METHOD_NOT_FOUND, "Method not found"))

    # -- methods --------------------------------------------------------------

    def _initialize(self, msg_id: Any, principal: VerifiedPrincipal, res: Resolution) -> GatewayResponse:
        sid = secrets.token_urlsafe(18)
        self.sessions[sid] = Session(sid, principal.directory_tenant_id, principal.object_id,
                                     res.ent.tenant_id, principal.roles)
        self._audit.emit("session_started", tenant_id=res.ent.tenant_id, subject=principal.object_id)
        result = {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": "contoso-entitled-tool-gateway", "version": "0.1.0"},
        }
        return GatewayResponse(200, _result(msg_id, result), {"mcp-session-id": sid})

    def _view(self, ent: Entitlements, pclass: PrincipalClass) -> CatalogView:
        key = self._view_key(ent.tenant_id, pclass, ent.version, self._catalog.version)
        view = self.views.get(key)
        if view is None:
            view = build_view(self._catalog, ent, pclass)
            self.views.put(key, view)
        return view

    def _tools_list(self, msg_id: Any, params: Mapping[str, Any], principal: VerifiedPrincipal,
                    res: Resolution, pclass: PrincipalClass, session: Session | None) -> GatewayResponse:
        view = self._view(res.ent, pclass)
        offset = 0
        if params.get("cursor") is not None:
            try:
                offset = self._cursors.decode(params["cursor"], res.ent.tenant_id, pclass, view.digest)
                if offset > len(view.tools):
                    raise CursorInvalid("offset")
            except CursorInvalid as exc:
                self._audit.emit("cursor_rejected", tenant_id=res.ent.tenant_id, principal_class=pclass.label,
                                 reason=str(exc), subject=principal.object_id)
                return GatewayResponse(200, _error(msg_id, INVALID_PARAMS, "Invalid cursor"))
        page = [json.loads(json.dumps(t)) for t in view.tools[offset:offset + self._page_size]]
        result: dict[str, Any] = {"tools": page}
        if offset + self._page_size < len(view.tools):
            result["nextCursor"] = self._cursors.encode(res.ent.tenant_id, pclass, view.digest,
                                                        offset + self._page_size)
        if session is not None:
            session.roles = principal.roles
            session.listed_digest = view.digest
            session.list_changed_pending = False
        self._audit.emit("list_filtered", tenant_id=res.ent.tenant_id, principal_class=pclass.label,
                         view_version=view.version, visible=len(view.tools),
                         hidden=len(self._catalog.all()) - len(view.tools), freshness=res.freshness,
                         subject=principal.object_id)
        return GatewayResponse(200, _result(msg_id, result))

    def _tools_call(self, msg_id: Any, params: Mapping[str, Any], principal: VerifiedPrincipal,
                    res: Resolution, pclass: PrincipalClass) -> GatewayResponse:
        name = params.get("name")
        args = params.get("arguments", {})
        tool = self._catalog.get(name) if isinstance(name, str) else None
        # The same decision function as tools/list, evaluated again here. The call path
        # never trusts the cached view, the session or the client's earlier list.
        reason = "unknown" if tool is None else denial_reason(tool, res.ent, pclass.roles)
        audit_fields = {
            "tenant_id": res.ent.tenant_id, "principal_class": pclass.label, "tool": safe_tool_name(name),
            "entitlement_version": res.ent.version, "catalog_version": self._catalog.version,
            "freshness": res.freshness, "subject": principal.object_id,
        }
        if reason is not None:
            self._audit.emit("call_denied", reason=reason, **audit_fields)
            if self._disclosure == "explicit" and tool is not None and tool.discoverable:
                return GatewayResponse(200, _error(msg_id, FORBIDDEN, "Tool not available for this caller",
                                                   {"reason": reason}))
            return GatewayResponse(200, _unknown_tool(msg_id, name))
        assert tool is not None
        if res.freshness == "stale" and not self._stale_call_allowed(tool):
            self._audit.emit("call_denied", reason="stale_entitlements", **audit_fields)
            return GatewayResponse(503, _error(msg_id, ENTITLEMENT_UNAVAILABLE, "Entitlements temporarily unavailable"))
        if not _arguments_match(minimize_schema(tool.input_schema, res.ent.feature_flags), args):
            # Entitlement is checked first, so this message is only reachable for visible tools.
            self._audit.emit("call_rejected", reason="invalid_arguments", **audit_fields)
            content = [{"type": "text", "text": "Arguments do not match this tool's input schema."}]
            return GatewayResponse(200, _result(msg_id, {"content": content, "isError": True}))
        self._audit.emit("call_allowed", **audit_fields)
        output = self._backend.invoke(tool, res.ent.tenant_id, principal, args)
        content = [{"type": "text", "text": json.dumps(output, sort_keys=True)}]
        return GatewayResponse(200, _result(msg_id, {"content": content, "isError": False}))

    def _stale_call_allowed(self, tool: ToolDefinition) -> bool:
        scope = self._policy.stale_call_scope
        return scope == "all" or (scope == "read-only" and tool.read_only)

    # -- change propagation ---------------------------------------------------

    def _on_entitlement_change(self, tenant_id: str, version: int) -> None:
        self.entitlements.invalidate(tenant_id, version)
        self._audit.emit("entitlement_changed", tenant_id=tenant_id, entitlement_version=version)
        self._refresh_sessions(lambda s: s.tenant_id == tenant_id)

    def publish_tool(self, tool: ToolDefinition) -> None:
        self._catalog.publish(tool)
        self._audit.emit("catalog_published", tool=tool.name, catalog_version=self._catalog.version)
        self._refresh_sessions(lambda s: True)

    def _refresh_sessions(self, match: Callable[[Session], bool]) -> None:
        """Notify only sessions that listed and whose visible view actually changed."""
        for session in self.sessions.values():
            if not match(session) or session.listed_digest is None or session.list_changed_pending:
                continue
            pclass = principal_class(session.roles, self._catalog.relevant_roles)
            try:
                digest: str | None = self._view(self.entitlements.resolve(session.directory_tenant_id).ent,
                                                pclass).digest
            except (EntitlementUnavailable, TenantNotOnboarded):
                digest = None  # cannot tell: notify, and let the next tools/list decide
            if digest != session.listed_digest:
                session.outbox.append({"jsonrpc": JSONRPC, "method": "notifications/tools/list_changed"})
                session.list_changed_pending = True
                self._audit.emit("list_changed_sent", tenant_id=session.tenant_id, subject=session.object_id)


# ---------------------------------------------------------------------------
# Domain Packs: same mechanics, different industries and policies
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainPack:
    key: str
    industry: str
    operator: str
    full: TenantSeed  # broadly entitled tenant
    limited: TenantSeed  # tenant licensed for one module only
    tools: tuple[ToolDefinition, ...]
    late_tool: ToolDefinition  # published after sessions exist
    policy: CachePolicy
    hidden_tool: str  # visible to full, hidden from limited (module not licensed)
    undiscoverable_hidden: str | None  # hidden from limited and never disclosed
    read_tool: str
    write_tool: str  # role-gated
    write_role: str
    gated_property: tuple[str, str]  # (tool, property) removed without a feature flag
    sample_args: Mapping[str, Mapping[str, Any]]

    def module_of(self, tool_name: str) -> str:
        return next(t.module for t in self.tools if t.name == tool_name)


def _schema(props: Mapping[str, Any], required: Sequence[str]) -> dict[str, Any]:
    return {"type": "object", "properties": dict(props), "required": list(required), "additionalProperties": False}


_STR = {"type": "string"}

MANUFACTURING = DomainPack(
    key="contoso-manufacturing",
    industry="Manufacturing",
    operator="Contoso (multi-plant production software)",
    full=TenantSeed("t-adventure-works", "00000000-0000-0000-0000-000000000a01", "Adventure Works",
                    frozenset({"production", "quality", "maintenance"}),
                    frozenset({"cost-analytics", "predictive-maintenance"}),
                    {"production.get_work_order": "multi-site"}),
    limited=TenantSeed("t-tailspin-toys", "00000000-0000-0000-0000-000000000a02", "Tailspin Toys",
                       frozenset({"production"})),
    tools=(
        ToolDefinition(
            "production.get_work_order", "Get work order",
            "Return the status, quantities and due date of a production work order.", "production",
            _schema({"work_order_id": _STR,
                     "include_cost_breakdown": {"type": "boolean", ENTITLEMENT_FLAG_KEY: "cost-analytics"}},
                    ["work_order_id"]),
            variants={"multi-site": "Return the status, quantities, due date and producing site of a "
                                    "production work order."}),
        ToolDefinition(
            "production.update_work_order_status", "Update work order status",
            "Set the status of a production work order, for example released or on hold.", "production",
            _schema({"work_order_id": _STR, "status": {"type": "string", "enum": ["released", "on-hold"]}},
                    ["work_order_id", "status"]),
            roles=frozenset({"Planner"}), read_only=False),
        ToolDefinition(
            "quality.list_nonconformances", "List nonconformances",
            "List open quality nonconformance reports for a part number.", "quality",
            _schema({"part_number": _STR}, ["part_number"]), discoverable=True),
        ToolDefinition(
            "maintenance.schedule_inspection", "Schedule inspection",
            "Schedule a predictive-maintenance inspection for an asset.", "maintenance",
            _schema({"asset_id": _STR, "window": _STR}, ["asset_id", "window"]),
            roles=frozenset({"MaintenanceLead"}), feature_flag="predictive-maintenance", read_only=False),
    ),
    late_tool=ToolDefinition(
        "maintenance.get_asset_health", "Get asset health",
        "Return the latest recorded health indicators for an asset.", "maintenance",
        _schema({"asset_id": _STR}, ["asset_id"])),
    policy=CachePolicy(entitlement_ttl=30, negative_ttl=10, max_staleness=60, stale_call_scope="read-only",
                       allow_explicit_disclosure=True),
    hidden_tool="quality.list_nonconformances",
    undiscoverable_hidden="maintenance.schedule_inspection",
    read_tool="production.get_work_order",
    write_tool="production.update_work_order_status",
    write_role="Planner",
    gated_property=("production.get_work_order", "include_cost_breakdown"),
    sample_args={
        "production.get_work_order": {"work_order_id": "WO-10042"},
        "production.update_work_order_status": {"work_order_id": "WO-10042", "status": "on-hold"},
        "quality.list_nonconformances": {"part_number": "PN-7731"},
        "maintenance.schedule_inspection": {"asset_id": "AS-0091", "window": "next-shift"},
        "maintenance.get_asset_health": {"asset_id": "AS-0091"},
    },
)

HEALTH = DomainPack(
    key="fabrikam-health",
    industry="Healthcare",
    operator="Fabrikam (clinic operations platform)",
    full=TenantSeed("t-lamna-healthcare", "00000000-0000-0000-0000-000000000b01", "Lamna Healthcare",
                    frozenset({"scheduling", "clinical-summary", "billing"}), frozenset({"telehealth"}),
                    {"scheduling.find_open_slots": "telehealth"}),
    limited=TenantSeed("t-relecloud-clinics", "00000000-0000-0000-0000-000000000b02", "Relecloud Clinics",
                       frozenset({"scheduling"})),
    tools=(
        ToolDefinition(
            "scheduling.find_open_slots", "Find open slots",
            "Find open appointment slots at a clinic on a date.", "scheduling",
            _schema({"clinic_id": _STR, "date": _STR,
                     "include_virtual": {"type": "boolean", ENTITLEMENT_FLAG_KEY: "telehealth"}},
                    ["clinic_id", "date"]),
            variants={"telehealth": "Find open appointment slots at a clinic on a date, including virtual "
                                    "visit slots."}),
        ToolDefinition(
            "scheduling.book_appointment", "Book appointment",
            "Book an open slot for a patient reference.", "scheduling",
            _schema({"slot_id": _STR, "patient_ref": _STR}, ["slot_id", "patient_ref"]),
            roles=frozenset({"Scheduler"}), read_only=False),
        ToolDefinition(
            "clinical.get_visit_summary", "Get visit summary",
            "Return the structured summary of a completed visit.", "clinical-summary",
            _schema({"visit_id": _STR}, ["visit_id"]), roles=frozenset({"Clinician"})),
        ToolDefinition(
            "billing.prepare_claim_draft", "Prepare claim draft",
            "Prepare a draft claim for a completed visit for human review.", "billing",
            _schema({"visit_id": _STR}, ["visit_id"]), roles=frozenset({"BillingSpecialist"}),
            read_only=False),
    ),
    late_tool=ToolDefinition(
        "billing.get_remittance_status", "Get remittance status",
        "Return the remittance status of a submitted claim.", "billing",
        _schema({"claim_id": _STR}, ["claim_id"])),
    policy=CachePolicy(entitlement_ttl=10, negative_ttl=10, max_staleness=0, stale_call_scope="none",
                       allow_explicit_disclosure=False),
    hidden_tool="clinical.get_visit_summary",
    undiscoverable_hidden=None,
    read_tool="scheduling.find_open_slots",
    write_tool="scheduling.book_appointment",
    write_role="Scheduler",
    gated_property=("scheduling.find_open_slots", "include_virtual"),
    sample_args={
        "scheduling.find_open_slots": {"clinic_id": "CL-204", "date": "2030-01-15"},
        "scheduling.book_appointment": {"slot_id": "SL-88213", "patient_ref": "PR-55120"},
        "clinical.get_visit_summary": {"visit_id": "VS-30917"},
        "billing.prepare_claim_draft": {"visit_id": "VS-30917"},
        "billing.get_remittance_status": {"claim_id": "CLM-4410"},
    },
)

LOGISTICS = DomainPack(
    key="northwind-logistics",
    industry="Logistics",
    operator="Northwind Traders (freight platform)",
    full=TenantSeed("t-wide-world-importers", "00000000-0000-0000-0000-000000000c01", "Wide World Importers",
                    frozenset({"shipments", "customs", "fleet"}), frozenset({"carbon-report", "hazmat"}),
                    {"shipments.track": "multi-carrier"}),
    limited=TenantSeed("t-wingtip-toys", "00000000-0000-0000-0000-000000000c02", "Wingtip Toys",
                       frozenset({"shipments"})),
    tools=(
        ToolDefinition(
            "shipments.track", "Track shipment",
            "Return the current milestone and estimated arrival of a shipment.", "shipments",
            _schema({"shipment_id": _STR,
                     "include_hazmat_detail": {"type": "boolean", ENTITLEMENT_FLAG_KEY: "hazmat"}},
                    ["shipment_id"]),
            variants={"multi-carrier": "Return the current milestone, carrier and estimated arrival of a "
                                       "shipment across carriers."}),
        ToolDefinition(
            "shipments.create_booking", "Create booking",
            "Create a freight booking between two locations.", "shipments",
            _schema({"origin": _STR, "destination": _STR, "pallets": {"type": "integer"}},
                    ["origin", "destination", "pallets"]),
            roles=frozenset({"Dispatcher"}), read_only=False),
        ToolDefinition(
            "customs.get_declaration_status", "Get declaration status",
            "Return the clearance status of a customs declaration.", "customs",
            _schema({"declaration_id": _STR}, ["declaration_id"]), discoverable=True),
        ToolDefinition(
            "fleet.get_emissions_report", "Get emissions report",
            "Return the fleet emissions report for a period.", "fleet",
            _schema({"period": _STR}, ["period"]), roles=frozenset({"FleetManager"}),
            feature_flag="carbon-report"),
    ),
    late_tool=ToolDefinition(
        "fleet.list_vehicles", "List vehicles",
        "List vehicles in the fleet with their current status.", "fleet", _schema({}, [])),
    policy=CachePolicy(entitlement_ttl=30, negative_ttl=30, max_staleness=120, stale_call_scope="read-only",
                       allow_explicit_disclosure=True),
    hidden_tool="customs.get_declaration_status",
    undiscoverable_hidden="fleet.get_emissions_report",
    read_tool="shipments.track",
    write_tool="shipments.create_booking",
    write_role="Dispatcher",
    gated_property=("shipments.track", "include_hazmat_detail"),
    sample_args={
        "shipments.track": {"shipment_id": "SH-77120"},
        "shipments.create_booking": {"origin": "DEPOT-NORTH", "destination": "DEPOT-SOUTH", "pallets": 12},
        "customs.get_declaration_status": {"declaration_id": "CD-90311"},
        "fleet.get_emissions_report": {"period": "2030-Q1"},
        "fleet.list_vehicles": {},
    },
)

PACKS: dict[str, DomainPack] = {p.key: p for p in (MANUFACTURING, HEALTH, LOGISTICS)}


# ---------------------------------------------------------------------------
# Self-test harness
# ---------------------------------------------------------------------------


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


class Env:
    """One gateway with fresh fakes for a Domain Pack."""

    def __init__(self, pack: DomainPack, *, view_key: ViewKeyFn = secure_view_key, disclosure: str = "uniform",
                 page_size: int = 50) -> None:
        self.pack = pack
        self.clock = FakeClock()
        self.validator = FakeTokenValidator(self.clock, lifetime=1000)
        self.registry = FakeEntitlementRegistry()
        self.registry.add_tenant(pack.full)
        self.registry.add_tenant(pack.limited)
        terms = [v for s in (pack.full, pack.limited) for v in (s.display_name, s.tenant_id, s.directory_tenant_id)]
        self.catalog = ToolCatalog(terms)
        for tool in pack.tools:
            self.catalog.publish(tool)
        self.audit = AuditLog(self.clock)
        self.backend = FakeToolBackend()
        self.gateway = EntitledToolGateway(
            validator=self.validator, registry=self.registry, catalog=self.catalog, policy=pack.policy,
            clock=self.clock, audit=self.audit, backend=self.backend, view_key=view_key, page_size=page_size,
            disclosure=disclosure)
        self._next_id = 0

    def token(self, seed: TenantSeed, roles: frozenset[str] | set[str] | None = None, oid: str = "user-1") -> str:
        granted = self.catalog.relevant_roles if roles is None else frozenset(roles)
        return self.validator.issue(seed.directory_tenant_id, oid, granted)

    def rpc(self, token: str | None, method: str, params: Mapping[str, Any] | None = None, *,
            session: str | None = None, headers: Mapping[str, str] | None = None) -> GatewayResponse:
        self._next_id += 1
        h: dict[str, str] = {"Authorization": f"Bearer {token}"} if token else {}
        if session:
            h["Mcp-Session-Id"] = session
        h.update(headers or {})
        message: dict[str, Any] = {"jsonrpc": JSONRPC, "id": self._next_id, "method": method}
        if params is not None:
            message["params"] = dict(params)
        return self.gateway.handle(h, message)

    def list_names(self, token: str, **kw: Any) -> list[str]:
        names: list[str] = []
        cursor = None
        while True:
            resp = self.rpc(token, "tools/list", {"cursor": cursor} if cursor else {}, **kw)
            check(resp.status == 200 and resp.body is not None and "result" in resp.body,
                  f"tools/list failed: {resp.status} {resp.body}")
            names += [t["name"] for t in resp.body["result"]["tools"]]
            cursor = resp.body["result"].get("nextCursor")
            if not cursor:
                return names

    def list_tools(self, token: str) -> list[dict[str, Any]]:
        resp = self.rpc(token, "tools/list", {})
        check(resp.status == 200 and resp.body is not None and "result" in resp.body, "tools/list failed")
        return resp.body["result"]["tools"]

    def call(self, token: str, name: Any, args: Mapping[str, Any] | None = None, **kw: Any) -> GatewayResponse:
        arguments = dict(self.pack.sample_args.get(name, {})) if args is None else dict(args)
        return self.rpc(token, "tools/call", {"name": name, "arguments": arguments}, **kw)

    def open_session(self, token: str) -> str:
        resp = self.rpc(token, "initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                              "clientInfo": {"name": "self-test", "version": "0"}})
        check(resp.status == 200 and "mcp-session-id" in resp.headers, f"initialize failed: {resp.status}")
        return resp.headers["mcp-session-id"]


def expected_names(catalog: ToolCatalog, seed: TenantSeed, roles: frozenset[str]) -> set[str]:
    """Independent oracle computed from the seed, not from the gateway or the registry."""
    return {t.name for t in catalog.all()
            if t.module in seed.modules
            and (not t.feature_flag or t.feature_flag in seed.feature_flags)
            and (not t.roles or t.roles & roles)}


def is_allowed(resp: GatewayResponse) -> bool:
    return (resp.status == 200 and resp.body is not None and "result" in resp.body
            and resp.body["result"].get("isError") is False)


def is_tool_error(resp: GatewayResponse) -> bool:
    return (resp.status == 200 and resp.body is not None and "result" in resp.body
            and resp.body["result"].get("isError") is True)


def error_of(resp: GatewayResponse) -> dict[str, Any]:
    check(resp.body is not None and "error" in resp.body, f"expected a JSON-RPC error, got {resp.status} {resp.body}")
    return resp.body["error"]


def is_unknown_tool(resp: GatewayResponse, name: str) -> bool:
    if resp.status != 200 or resp.body is None or "error" not in resp.body:
        return False
    err = resp.body["error"]
    return err["code"] == INVALID_PARAMS and err["message"] == f"Unknown tool: {name}" and "data" not in err


def s01_list_filtered(pack: DomainPack) -> None:
    env = Env(pack)
    seen: dict[tuple[str, str], list[str]] = {}
    for seed in (pack.full, pack.limited):
        for label, roles in (("all", None), ("base", frozenset())):
            tok = env.token(seed, roles)
            granted = env.catalog.relevant_roles if roles is None else roles
            names = env.list_names(tok)
            check(sorted(names) == sorted(expected_names(env.catalog, seed, granted)),
                  f"{seed.tenant_id}/{label}: unexpected list {names}")
            seen[(seed.tenant_id, label)] = names
    full_all, limited_all = seen[(pack.full.tenant_id, "all")], seen[(pack.limited.tenant_id, "all")]
    check(pack.hidden_tool in full_all and pack.hidden_tool not in limited_all, "hidden tool placement wrong")
    check(set(limited_all) < set(full_all), "limited tenant should see a strict subset")
    check(len(env.audit.of("list_filtered")) == 4, "every list should be audited")


def s02_hidden_call_indistinguishable(pack: DomainPack) -> None:
    env = Env(pack)
    lim = env.token(pack.limited)
    ghost = pack.hidden_tool.split(".")[0] + ".nonexistent_tool"
    before = len(env.backend.invocations)
    responses = [
        (env.call(lim, pack.hidden_tool), pack.hidden_tool),
        (env.call(lim, ghost, pack.sample_args[pack.hidden_tool]), ghost),
        (env.call(lim, pack.hidden_tool, {"zzz_not_a_param": 1}), pack.hidden_tool),
    ]
    shapes = set()
    for resp, name in responses:
        check(is_unknown_tool(resp, name), f"{name}: expected uniform Unknown tool, got {resp.body}")
        err = resp.body["error"]
        shapes.add((resp.status, err["code"], err["message"].replace(name, "<tool>"), tuple(sorted(err))))
    check(len(shapes) == 1, f"hidden and nonexistent tools must be indistinguishable: {shapes}")
    check(len(env.backend.invocations) == before, "a denied call must not reach the backend")
    reasons = [e["reason"] for e in env.audit.of("call_denied")]
    check(reasons == ["not_entitled", "unknown", "not_entitled"], f"audit must keep the real reason: {reasons}")


def s03_role_gated(pack: DomainPack) -> None:
    env = Env(pack)
    no_role = env.token(pack.full, roles=frozenset())
    check(pack.write_tool not in env.list_names(no_role), "role-gated tool listed without the role")
    check(is_unknown_tool(env.call(no_role, pack.write_tool), pack.write_tool), "role-gated call not denied")
    check(env.audit.of("call_denied")[-1]["reason"] == "role_required", "audit reason should be role_required")
    with_role = env.token(pack.full, roles={pack.write_role}, oid="user-2")
    check(pack.write_tool in env.list_names(with_role), "role holder should see the tool")
    check(is_allowed(env.call(with_role, pack.write_tool)), "role holder call should succeed")
    check(env.backend.invocations == [(pack.write_tool, pack.full.tenant_id)], "backend saw the wrong call")


def s04_spoofed_context_ignored(pack: DomainPack) -> None:
    env = Env(pack)
    lim = env.token(pack.limited)
    spoof = {"X-Tenant-Id": pack.full.tenant_id, "X-Org-Id": pack.full.directory_tenant_id}
    names = env.list_names(lim, headers=spoof)
    check(sorted(names) == sorted(expected_names(env.catalog, pack.limited, env.catalog.relevant_roles)),
          "tenant hint headers changed the visible catalog")
    hint = env.audit.of("client_tenant_hint_ignored")[-1]
    check(hint["headers"] == ["x-org-id", "x-tenant-id"], "hint audit should record header names")
    smuggled = {**pack.sample_args[pack.read_tool], "tenant_id": pack.full.tenant_id}
    check(is_tool_error(env.call(lim, pack.read_tool, smuggled)), "tenant_id argument must be rejected")
    check(env.backend.invocations == [], "smuggled call must not reach the backend")
    full = env.token(pack.full)
    sid = env.open_session(full)
    check(env.rpc(lim, "tools/list", session=sid).status == 404, "cross-tenant session reuse must fail")
    other_user = env.token(pack.full, oid="user-9")
    check(env.rpc(other_user, "tools/list", session=sid).status == 404, "cross-user session reuse must fail")
    check(env.rpc(full, "tools/list", session=sid).status == 200, "owner should use its session")
    check(len(env.audit.of("session_rejected")) == 2, "session rejections should be audited")


def s05_cache_keyed_by_entitlement_set(pack: DomainPack) -> None:
    env = Env(pack)
    roles = env.catalog.relevant_roles
    first = env.list_names(env.token(pack.full, roles, oid="user-1"))
    hits, misses = env.gateway.views.hits, env.gateway.views.misses
    second = env.list_names(env.token(pack.full, set(roles) | {"Reader"}, oid="user-2"))
    check(first == second, "same entitlement set should give the same view")
    check(env.gateway.views.hits == hits + 1 and env.gateway.views.misses == misses,
          "second user with the same principal class should hit the view cache")
    check(all("user-" not in repr(k) for k in env.gateway.views.keys()),
          "view cache keys must not contain user identifiers")
    bad = Env(pack, view_key=insecure_view_key_without_tenant)
    bad.list_names(bad.token(pack.full))
    lim_bad = bad.token(pack.limited, oid="user-2")
    check(pack.hidden_tool in bad.list_names(lim_bad), "baseline should demonstrate the cross-tenant leak")
    check(is_unknown_tool(bad.call(lim_bad, pack.hidden_tool), pack.hidden_tool),
          "call-time enforcement must still deny the leaked tool")
    good = Env(pack)
    good.list_names(good.token(pack.full))
    check(pack.hidden_tool not in good.list_names(good.token(pack.limited, oid="user-2")),
          "tenant-scoped key must not leak")


def s06_schema_minimized(pack: DomainPack) -> None:
    env = Env(pack)
    full, lim = env.token(pack.full), env.token(pack.limited, oid="user-2")
    tool_name, prop = pack.gated_property
    full_tools, lim_tools = env.list_tools(full), env.list_tools(lim)
    f = next(t for t in full_tools if t["name"] == tool_name)
    li = next(t for t in lim_tools if t["name"] == tool_name)
    check(prop in f["inputSchema"]["properties"], "entitled tenant should see the gated property")
    check(prop not in li["inputSchema"]["properties"], "gated property leaked to an unentitled tenant")
    check(ENTITLEMENT_FLAG_KEY not in json.dumps([full_tools, lim_tools]), "internal flag annotation leaked")
    base = env.catalog.get(tool_name)
    assert base is not None
    check(f["description"] == base.variants[pack.full.description_variants[tool_name]], "variant not applied")
    check(li["description"] == base.description, "limited tenant should see the shared description")
    args = {**pack.sample_args[tool_name], prop: True}
    check(is_tool_error(env.call(lim, tool_name, args)), "gated property must be rejected at call time")
    check(env.backend.invocations == [], "rejected call must not reach the backend")
    check(is_allowed(env.call(full, tool_name, args)), "entitled tenant may use the gated property")


def s07_cursor_bound(pack: DomainPack) -> None:
    env = Env(pack, page_size=2)
    full, lim = env.token(pack.full), env.token(pack.limited, oid="user-2")
    names = env.list_names(full)
    check(len(names) > 2 and sorted(names) == sorted(expected_names(env.catalog, pack.full, env.catalog.relevant_roles)),
          "pagination must cover exactly the filtered view")
    first = env.rpc(full, "tools/list", {})
    cursor = first.body["result"]["nextCursor"]

    def rejected(tok: str, cur: str, reason: str) -> None:
        err = error_of(env.rpc(tok, "tools/list", {"cursor": cur}))
        check(err["code"] == INVALID_PARAMS and err["message"] == "Invalid cursor", f"cursor accepted: {err}")
        check(env.audit.of("cursor_rejected")[-1]["reason"] == reason, f"expected cursor reason {reason}")

    rejected(lim, cursor, "binding")
    payload, mac = cursor.split(".")
    rejected(full, f"{payload}.{'A' if mac[0] != 'A' else 'B'}{mac[1:]}", "signature")
    rejected(full, "not-a-cursor", "malformed")
    second = env.rpc(full, "tools/list", {"cursor": cursor})
    check(second.body is not None and "result" in second.body, "valid cursor should work")
    env.registry.change(pack.full.tenant_id, remove_modules=[pack.module_of(pack.hidden_tool)])
    rejected(full, cursor, "binding")


def s08_change_notifies_affected(pack: DomainPack) -> None:
    env = Env(pack)
    full = env.token(pack.full)
    lim = env.token(pack.limited, oid="user-2")
    lim_idle = env.token(pack.limited, oid="user-3")
    s_full, s_lim, s_idle = env.open_session(full), env.open_session(lim), env.open_session(lim_idle)
    env.list_names(full, session=s_full)
    env.list_names(lim, session=s_lim)
    sessions = env.gateway.sessions

    def counts() -> tuple[int, int, int]:
        return len(sessions[s_full].outbox), len(sessions[s_lim].outbox), len(sessions[s_idle].outbox)

    env.registry.change(pack.limited.tenant_id, add_modules=[pack.module_of(pack.hidden_tool)])
    check(counts() == (0, 1, 0), f"upgrade should notify only the listed limited session: {counts()}")
    check(sessions[s_lim].outbox[0] == {"jsonrpc": JSONRPC, "method": "notifications/tools/list_changed"},
          "wrong notification shape")
    check(pack.hidden_tool in env.list_names(lim, session=s_lim), "upgrade should reveal the tool")
    env.gateway.publish_tool(pack.late_tool)
    check(counts() == (1, 1, 0), f"publish should notify only sessions whose view changed: {counts()}")
    env.registry.change(pack.full.tenant_id, remove_modules=[pack.module_of(pack.hidden_tool)])
    check(counts() == (1, 1, 0), "a pending session should not be notified twice")
    names = env.list_names(full, session=s_full)
    check(pack.late_tool.name in names and pack.hidden_tool not in names, "re-list should show the new view")
    check(not sessions[s_full].list_changed_pending, "re-list should clear the pending flag")


def s09_revocation_next_call(pack: DomainPack) -> None:
    env = Env(pack)
    tok = env.token(pack.full)
    module = pack.module_of(pack.hidden_tool)
    check(is_allowed(env.call(tok, pack.hidden_tool)), "entitled call should succeed")
    calls = len(env.backend.invocations)
    env.registry.change(pack.full.tenant_id, remove_modules=[module])
    check(is_unknown_tool(env.call(tok, pack.hidden_tool), pack.hidden_tool), "revocation should bite at once")
    check(len(env.backend.invocations) == calls, "revoked call must not reach the backend")
    env.registry.change(pack.full.tenant_id, add_modules=[module])
    check(is_allowed(env.call(tok, pack.hidden_tool)), "restored entitlement should work")
    env.registry.deliver_events = False
    env.registry.change(pack.full.tenant_id, remove_modules=[module])
    check(is_allowed(env.call(tok, pack.hidden_tool)), "missed event: exposure lasts until the TTL")
    env.clock.advance(pack.policy.entitlement_ttl)
    check(is_unknown_tool(env.call(tok, pack.hidden_tool), pack.hidden_tool), "TTL must bound a missed event")


def s10_registry_outage(pack: DomainPack) -> None:
    env = Env(pack)
    tok = env.token(pack.full)
    env.registry.outage = True
    resp = env.rpc(tok, "tools/list", {})
    check(resp.status == 503 and error_of(resp)["code"] == ENTITLEMENT_UNAVAILABLE, "cold outage must fail closed")
    env.registry.outage = False
    env.list_names(tok)
    env.registry.outage = True
    env.clock.advance(pack.policy.entitlement_ttl)
    if pack.policy.max_staleness > 0:
        check(env.rpc(tok, "tools/list", {}).status == 200, "list should use the bounded stale snapshot")
        check(env.audit.of("list_filtered")[-1]["freshness"] == "stale", "stale use should be audited")
        read_ok = pack.policy.stale_call_scope in ("read-only", "all")
        check(is_allowed(env.call(tok, pack.read_tool)) == read_ok, "stale read policy not applied")
        write = env.call(tok, pack.write_tool)
        if pack.policy.stale_call_scope != "all":
            check(write.status == 503, "write on a stale snapshot must fail closed")
            check(env.audit.of("call_denied")[-1]["reason"] == "stale_entitlements", "stale denial not audited")
        env.clock.advance(pack.policy.max_staleness)
    check(env.rpc(tok, "tools/list", {}).status == 503, "beyond max_staleness the gateway must fail closed")
    env.registry.outage = False
    check(env.rpc(tok, "tools/list", {}).status == 200, "recovery after the outage")
    new_version = env.registry.change(pack.full.tenant_id, add_flags=["pilot-preview"])
    env.registry.set_version(pack.full.tenant_id, new_version - 1)
    check(env.rpc(tok, "tools/list", {}).status == 503, "a lagging replica must not roll back entitlements")
    env.registry.set_version(pack.full.tenant_id, new_version)
    check(env.rpc(tok, "tools/list", {}).status == 200, "recovery after the replica catches up")


def s11_identity_fail_closed(pack: DomainPack) -> None:
    env = Env(pack)
    for bearer in (None, "test-token.forged"):
        resp = env.rpc(bearer, "tools/list", {})
        check(resp.status == 401 and 'resource_metadata="' in resp.headers.get("www-authenticate", ""),
              "unauthenticated request must get 401 with resource metadata")
    expiring = env.token(pack.full)
    env.clock.advance(1000)
    check(env.rpc(expiring, "tools/list", {}).status == 401, "expired token must be rejected")
    check(len(env.audit.of("auth_failed")) == 3, "auth failures should be audited")
    stranger = TenantSeed("t-unregistered", "00000000-0000-0000-0000-0000000000ff", "Unregistered", frozenset())
    tok = env.token(stranger)
    before = env.registry.calls
    for _ in range(2):
        resp = env.rpc(tok, "tools/list", {})
        check(resp.status == 403 and error_of(resp)["code"] == FORBIDDEN, "unregistered tenant must get 403")
    check(env.registry.calls == before + 1, "negative cache should absorb repeated unknown-tenant calls")


def s12_catalog_lint(pack: DomainPack) -> None:
    env = Env(pack)
    base = env.catalog.get(pack.read_tool)
    assert base is not None
    version = env.catalog.version
    bad_tools = [
        replace(base, name=base.name + "_tuned", description=f"{base.description} Tuned for {pack.full.display_name}."),
        replace(base, name=base.name + "_pinned", input_schema=_schema(
            {"tenant": {"type": "string", "const": pack.limited.directory_tenant_id}}, [])),
        replace(base, name="bad name!"),
        replace(base, module="unlicensed-module"),
    ]
    for tool in bad_tools:
        try:
            env.gateway.publish_tool(tool)
        except CatalogLintError:
            continue
        raise AssertionError(f"lint accepted {tool.name}")
    check(env.catalog.version == version, "rejected tools must not bump the catalog version")
    check(env.audit.of("catalog_published") == [], "rejected tools must not be announced")


def s13_explicit_disclosure_opt_in(pack: DomainPack) -> None:
    uniform = Env(pack)
    lim = uniform.token(pack.limited)
    check(is_unknown_tool(uniform.call(lim, pack.hidden_tool), pack.hidden_tool), "default must be uniform")
    if not pack.policy.allow_explicit_disclosure:
        try:
            Env(pack, disclosure="explicit")
        except ValueError:
            return
        raise AssertionError("policy forbids explicit disclosure but the gateway accepted it")
    env = Env(pack, disclosure="explicit")
    lim = env.token(pack.limited)
    err = error_of(env.call(lim, pack.hidden_tool))
    check(err["code"] == FORBIDDEN and err.get("data") == {"reason": "not_entitled"},
          "discoverable tool should get an explicit error")
    if pack.undiscoverable_hidden:
        check(is_unknown_tool(env.call(lim, pack.undiscoverable_hidden), pack.undiscoverable_hidden),
              "undiscoverable tool must stay uniform")
    ghost = pack.hidden_tool.split(".")[0] + ".nonexistent_tool"
    check(is_unknown_tool(env.call(lim, ghost, {}), ghost), "nonexistent tool must stay uniform")


def s14_audit_decisions_not_content(pack: DomainPack) -> None:
    env = Env(pack)
    full, lim = env.token(pack.full), env.token(pack.limited, oid="user-2")
    for tool in pack.tools:
        env.call(full, tool.name)
    env.call(lim, pack.hidden_tool)
    env.call(full, "../../admin tool", {})
    env.rpc(lim, "tools/list", {}, headers={"X-Tenant-Id": "sentinel-hint-value-7f3"})
    text = "\n".join(env.audit.lines())
    values = [v for args in pack.sample_args.values() for v in args.values() if isinstance(v, str)]
    check(all(v not in text for v in values), "audit must not contain tool argument values")
    check(full not in text and lim not in text, "audit must not contain tokens")
    check("sentinel-hint-value-7f3" not in text and "x-tenant-id" in text, "hint headers: names only")
    check(any(e["tool"].startswith("invalid:") for e in env.audit.of("call_denied")), "invalid names should be hashed")
    required = {"tenant_id", "principal_class", "tool", "entitlement_version", "catalog_version", "freshness"}
    decisions = env.audit.of("call_allowed") + env.audit.of("call_denied")
    check(decisions and all(required <= set(e) for e in decisions), "call decisions need the decision context")


SCENARIOS: list[tuple[str, Callable[[DomainPack], None]]] = [
    ("list-filtered-per-tenant-and-role", s01_list_filtered),
    ("hidden-tool-call-indistinguishable", s02_hidden_call_indistinguishable),
    ("role-gated-call-enforced", s03_role_gated),
    ("spoofed-context-ignored", s04_spoofed_context_ignored),
    ("cache-keyed-by-entitlement-set", s05_cache_keyed_by_entitlement_set),
    ("schema-minimized-per-entitlement", s06_schema_minimized),
    ("cursor-bound-to-filtered-view", s07_cursor_bound),
    ("change-notifies-only-affected-sessions", s08_change_notifies_affected),
    ("revocation-bites-next-call", s09_revocation_next_call),
    ("registry-outage-bounded-staleness", s10_registry_outage),
    ("identity-and-onboarding-fail-closed", s11_identity_fail_closed),
    ("catalog-lint-blocks-tenant-data", s12_catalog_lint),
    ("explicit-disclosure-is-opt-in", s13_explicit_disclosure_opt_in),
    ("audit-records-decisions-not-content", s14_audit_decisions_not_content),
]


def run_self_test(packs: Sequence[DomainPack]) -> int:
    passed = total = 0
    for pack in packs:
        for name, scenario in SCENARIOS:
            total += 1
            try:
                scenario(pack)
            except Exception as exc:  # report every scenario, then fail the run
                print(f"FAIL [{pack.key}] {name}: {type(exc).__name__}: {exc}")
                continue
            passed += 1
            print(f"PASS [{pack.key}] {name}")
    print(f"{passed}/{total} scenarios passed")
    return 0 if passed == total else 1


def run_demo(pack: DomainPack) -> None:
    env = Env(pack)
    print(f"== {pack.key}: {pack.industry}, operator {pack.operator} ==")
    for seed in (pack.full, pack.limited):
        print(f"{seed.display_name} [{seed.tenant_id}] sees: {', '.join(env.list_names(env.token(seed)))}")
    lim = env.token(pack.limited, oid="user-2")
    for name in (pack.hidden_tool, pack.hidden_tool.split(".")[0] + ".nonexistent_tool"):
        resp = env.call(lim, name, {})
        print(f"limited caller calls {name}: HTTP {resp.status} {json.dumps(error_of(resp))}")
    sid = env.open_session(lim)
    env.list_names(lim, session=sid)
    version = env.registry.change(pack.limited.tenant_id, add_modules=[pack.module_of(pack.hidden_tool)])
    print(f"entitlement change to v{version}; session outbox: {json.dumps(env.gateway.sessions[sid].outbox)}")
    print(f"registry wire response: {json.dumps(env.registry.wire_response(pack.limited.directory_tenant_id))}")
    print("last audit events:")
    for line in env.audit.lines()[-4:]:
        print(f"  {line}")
    print()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Entitlement-aware MCP tool catalog reference sample.")
    parser.add_argument("--self-test", action="store_true", help="run the scenario suite (default)")
    parser.add_argument("--demo", action="store_true", help="print a short walkthrough")
    parser.add_argument("--domain", choices=[*PACKS, "all"], default="all", help="Domain Pack to use")
    args = parser.parse_args(argv)
    packs = list(PACKS.values()) if args.domain == "all" else [PACKS[args.domain]]
    if args.demo:
        for pack in packs:
            run_demo(pack)
        if not args.self_test:
            return 0
    return run_self_test(packs)


if __name__ == "__main__":
    sys.exit(main())
