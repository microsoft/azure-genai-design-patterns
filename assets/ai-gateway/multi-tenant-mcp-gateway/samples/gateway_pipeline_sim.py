#!/usr/bin/env python3
"""Multi-tenant MCP gateway pipeline simulator (standard library only, offline).

This sample simulates the per-call pipeline described in
``docs/architecture.md`` for a multi-tenant MCP gateway on Azure API
Management (APIM). It collapses two runtime pieces into one process so the
behavior can be tested without Azure:

* the APIM policy pipeline: token check, tenant from a token claim, tenant
  directory lookup, product entitlement, per-tenant rate limit, backend
  routing, and an audit record; and
* the MCP adapter behind APIM: ``tools/list`` filtered by the tenant's
  entitlements, and the same entitlement re-checked on every ``tools/call``.

TEST ONLY: ``decode_unsigned_jwt`` reads JWT claims WITHOUT verifying a
signature. The real gateway validates signature, issuer, audience, expiry,
and scopes with ``validate-jwt`` or ``validate-azure-ad-token``. Never reuse
this decoder outside a test harness.

All tenant names, hosts, tools, and limits are fictitious and illustrative.
The rate-limit values used in the self-test are tiny test values chosen to
trigger throttling quickly. They are not sizing guidance.

Usage::

    python gateway_pipeline_sim.py --self-test
    python gateway_pipeline_sim.py --demo
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

ISSUER = "https://login.contoso.com/"
AUDIENCE = "https://mcp.contoso.com/mcp"
RESOURCE_METADATA_URL = "https://mcp.contoso.com/.well-known/oauth-protected-resource/mcp"
TENANT_CLAIM = "tenant_id"
REQUIRED_SCOPE = "mcp.tools"
MCP_PROTOCOL_VERSION = "2025-06-18"
TRUSTED_TENANT_HEADER = "X-Contoso-Gateway-Tenant"
SPOOFABLE_HEADERS = ("x-tenant-id", "x-contoso-gateway-tenant", "x-forwarded-for")

# JSON-RPC error codes. -32602 is the standard "invalid params" code that MCP
# servers use for unknown tools; -32001 and -32029 are implementation-defined
# server errors (the -32000 to -32099 range) chosen for this sample.
RPC_INVALID_PARAMS = -32602
RPC_METHOD_NOT_FOUND = -32601
RPC_FORBIDDEN = -32001
RPC_THROTTLED = -32029


# --------------------------------------------------------------------------
# Tool catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    name: str
    product: str
    description: str
    required_scope: str | None = None

    def to_mcp(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        }


DEFAULT_CATALOG: tuple[Tool, ...] = (
    Tool("orders_get_order", "orders", "Get one sales order by number."),
    Tool("orders_search_orders", "orders", "Search sales orders by customer and status."),
    Tool("orders_create_order", "orders", "Create a sales order.", required_scope="orders.write"),
    Tool("field_service_list_work_orders", "field-service", "List open field-service work orders."),
    Tool("reporting_run_report", "reporting", "Run a saved report and return a summary."),
)


# --------------------------------------------------------------------------
# Tenant directory and audit seams
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class TenantConfig:
    tenant_key: str
    status: str
    products: frozenset[str]
    cell_backend: str
    credential_ref: str
    rate_capacity: int
    rate_refill_per_second: float


class TenantDirectory(Protocol):
    def get(self, tenant_key: str) -> TenantConfig | None:
        """Return the tenant's server-side configuration, or None if unknown."""


@dataclass(frozen=True)
class AuditRecord:
    ts: float
    correlation_id: str
    tenant: str | None
    subject: str | None
    client_id: str | None
    method: str | None
    tool: str | None
    outcome: str
    http_status: int
    backend: str | None = None
    spoofed_header_ignored: bool = False


class AuditSink(Protocol):
    def emit(self, record: AuditRecord) -> None:
        """Persist one audit record. Never include request or response bodies."""


class InMemoryTenantDirectory:
    def __init__(self, tenants: dict[str, TenantConfig]):
        self._tenants = dict(tenants)

    def get(self, tenant_key: str) -> TenantConfig | None:
        return self._tenants.get(tenant_key)


class InMemoryAuditSink:
    def __init__(self) -> None:
        self.records: list[AuditRecord] = []

    def emit(self, record: AuditRecord) -> None:
        self.records.append(record)


class ProductionTenantDirectory:
    """Gated production stub.

    A real implementation calls the ISV's entitlement API (or reads Azure App
    Configuration) with a managed identity, caches entries by tenant key plus a
    catalog or entitlement version, and fails closed when the source is
    unavailable and nothing is cached. It must never trust a tenant value that
    came from a client-controlled header.
    """

    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def get(self, tenant_key: str) -> TenantConfig | None:
        raise NotImplementedError(
            "ProductionTenantDirectory is a stub. Wire it to your entitlement API "
            "with a managed identity and a bounded cache before use."
        )


class EventHubAuditSink:
    """Gated production stub.

    A real implementation sends each record to Azure Event Hubs (partitioned
    by tenant) for SIEM and billing pipelines. In APIM this is the
    ``log-to-eventhub`` policy rather than application code.
    """

    def __init__(self, namespace: str, hub: str):
        self.namespace = namespace
        self.hub = hub

    def emit(self, record: AuditRecord) -> None:
        raise NotImplementedError(
            "EventHubAuditSink is a stub. Use APIM log-to-eventhub or the Event Hubs SDK "
            "with a managed identity before use."
        )


# --------------------------------------------------------------------------
# Token bucket (per tenant)
# --------------------------------------------------------------------------


class TokenBucket:
    def __init__(self, capacity: int, refill_per_second: float, clock: Callable[[], float]):
        if capacity <= 0 or refill_per_second <= 0:
            raise ValueError("capacity and refill_per_second must be positive")
        self.capacity = float(capacity)
        self.refill_per_second = refill_per_second
        self._clock = clock
        self._tokens = float(capacity)
        self._last = clock()

    def try_take(self) -> tuple[bool, int]:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_per_second)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True, 0
        deficit = 1.0 - self._tokens
        retry_after = int(deficit / self.refill_per_second) + 1
        return False, retry_after


# --------------------------------------------------------------------------
# HTTP-ish request and response
# --------------------------------------------------------------------------


@dataclass
class Request:
    headers: dict[str, str]
    body: dict[str, Any] | None
    method: str = "POST"
    path: str = "/mcp"


@dataclass
class Response:
    status: int
    body: dict[str, Any] | None = None
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class BackendCall:
    backend: str
    tool: str
    arguments: dict[str, Any]
    credential_ref: str
    headers: dict[str, str]


class FakeBackend:
    """Records what the gateway forwarded, so tests can assert on it."""

    def __init__(self) -> None:
        self.calls: list[BackendCall] = []

    def invoke(self, call: BackendCall) -> dict[str, Any]:
        self.calls.append(call)
        tenant = call.headers.get(TRUSTED_TENANT_HEADER, "?")
        return {
            "content": [{"type": "text", "text": f"{call.tool} ok for tenant {tenant}"}],
            "isError": False,
        }


# --------------------------------------------------------------------------
# JWT helpers (TEST ONLY)
# --------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_test_token(claims: dict[str, Any]) -> str:
    """Build an UNSIGNED JWT for tests. Never accept tokens like this in production."""
    header = {"alg": "none", "typ": "JWT"}
    return f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(claims).encode())}."


def decode_unsigned_jwt(token: str) -> dict[str, Any]:
    """TEST ONLY: decode JWT claims without verifying a signature."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("malformed token")
    claims = json.loads(_b64url_decode(parts[1]))
    if not isinstance(claims, dict):
        raise ValueError("claims must be a JSON object")
    return claims


# --------------------------------------------------------------------------
# Gateway pipeline
# --------------------------------------------------------------------------


def _rpc_error(rpc_id: Any, code: int, message: str, reason: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message, "data": {"reason": reason}}}


class Gateway:
    def __init__(
        self,
        directory: TenantDirectory,
        audit: AuditSink,
        backend: FakeBackend,
        clock: Callable[[], float],
        catalog: tuple[Tool, ...] = DEFAULT_CATALOG,
    ) -> None:
        self.directory = directory
        self.audit = audit
        self.backend = backend
        self.clock = clock
        self.catalog = {tool.name: tool for tool in catalog}
        self._buckets: dict[str, TokenBucket] = {}
        self._sequence = 0

    # Stage helpers ---------------------------------------------------------

    def _correlation_id(self, headers: dict[str, str]) -> str:
        incoming = headers.get("traceparent")
        if incoming:
            return incoming
        self._sequence += 1
        return f"00-{self._sequence:032x}-{self._sequence:016x}-01"

    def _authenticate(self, headers: dict[str, str]) -> tuple[dict[str, Any] | None, Response | None]:
        auth = headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None, Response(
                401,
                None,
                {"WWW-Authenticate": f'Bearer resource_metadata="{RESOURCE_METADATA_URL}"'},
            )
        try:
            claims = decode_unsigned_jwt(auth[7:].strip())
        except (ValueError, json.JSONDecodeError):
            return None, self._invalid_token("malformed token")
        if claims.get("iss") != ISSUER:
            return None, self._invalid_token("unexpected issuer")
        if claims.get("aud") != AUDIENCE:
            return None, self._invalid_token("token audience is not this MCP server")
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp <= self.clock():
            return None, self._invalid_token("token expired")
        scopes = str(claims.get("scp", "")).split()
        if REQUIRED_SCOPE not in scopes:
            return None, self._invalid_token("missing required scope")
        if not claims.get(TENANT_CLAIM):
            return None, self._invalid_token("missing tenant claim")
        return claims, None

    @staticmethod
    def _invalid_token(description: str) -> Response:
        return Response(
            401,
            None,
            {
                "WWW-Authenticate": (
                    f'Bearer error="invalid_token", error_description="{description}", '
                    f'resource_metadata="{RESOURCE_METADATA_URL}"'
                )
            },
        )

    def _bucket(self, tenant: TenantConfig) -> TokenBucket:
        bucket = self._buckets.get(tenant.tenant_key)
        if bucket is None:
            bucket = TokenBucket(tenant.rate_capacity, tenant.rate_refill_per_second, self.clock)
            self._buckets[tenant.tenant_key] = bucket
        return bucket

    def _entitled_tools(self, tenant: TenantConfig, scopes: set[str]) -> list[Tool]:
        return [
            tool
            for tool in self.catalog.values()
            if tool.product in tenant.products and (tool.required_scope is None or tool.required_scope in scopes)
        ]

    # Pipeline --------------------------------------------------------------

    def handle(self, request: Request) -> Response:
        headers = {k.lower(): v for k, v in request.headers.items()}
        correlation_id = self._correlation_id(headers)
        body = request.body if isinstance(request.body, dict) else {}
        rpc_id = body.get("id")
        rpc_method = body.get("method") if isinstance(body.get("method"), str) else None
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        tool_name = params.get("name") if rpc_method == "tools/call" else None

        def audit(outcome: str, status: int, **extra: Any) -> None:
            self.audit.emit(
                AuditRecord(
                    ts=self.clock(),
                    correlation_id=correlation_id,
                    method=rpc_method,
                    tool=tool_name if isinstance(tool_name, str) else None,
                    outcome=outcome,
                    http_status=status,
                    **extra,
                )
            )

        # 1-2. Authenticate and validate the token.
        claims, failure = self._authenticate(headers)
        if failure is not None:
            audit("unauthenticated", failure.status, tenant=None, subject=None, client_id=None)
            failure.headers["traceparent"] = correlation_id
            return failure
        assert claims is not None

        # 3. Tenant comes from the validated token claim, never from a header.
        tenant_key = str(claims[TENANT_CLAIM])
        claimed_header = headers.get("x-tenant-id")
        spoofed = claimed_header is not None and claimed_header != tenant_key
        subject = str(claims.get("sub", ""))
        client_id = str(claims.get("azp", "")) or None
        scopes = set(str(claims.get("scp", "")).split())
        who = {"tenant": tenant_key, "subject": subject, "client_id": client_id, "spoofed_header_ignored": spoofed}

        # 4. Tenant directory lookup (fail closed).
        tenant = self.directory.get(tenant_key)
        if tenant is None:
            audit("unknown_tenant", 403, **who)
            return Response(403, _rpc_error(rpc_id, RPC_FORBIDDEN, "Forbidden", "unknown_tenant"), {"traceparent": correlation_id})
        if tenant.status != "active":
            audit("tenant_suspended", 403, **who)
            return Response(403, _rpc_error(rpc_id, RPC_FORBIDDEN, "Forbidden", "tenant_not_active"), {"traceparent": correlation_id})

        # 5. Product entitlement for this MCP server (tenant has at least one product).
        if not any(tool.product in tenant.products for tool in self.catalog.values()):
            audit("not_entitled", 403, **who)
            return Response(403, _rpc_error(rpc_id, RPC_FORBIDDEN, "Forbidden", "not_entitled"), {"traceparent": correlation_id})

        # 6. Per-tenant rate limit keyed on the token-derived tenant.
        allowed, retry_after = self._bucket(tenant).try_take()
        if not allowed:
            audit("throttled", 429, **who)
            return Response(
                429,
                _rpc_error(rpc_id, RPC_THROTTLED, "Too many requests", "tenant_rate_limit"),
                {"Retry-After": str(retry_after), "traceparent": correlation_id},
            )

        # 7. MCP method dispatch (the adapter's job, behind APIM).
        out_headers = {"traceparent": correlation_id}
        if rpc_method == "initialize":
            audit("allowed", 200, **who)
            out_headers["Mcp-Session-Id"] = f"sess-{tenant_key}-{self._sequence:04d}"
            return Response(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": rpc_id,
                    "result": {
                        "protocolVersion": MCP_PROTOCOL_VERSION,
                        "capabilities": {"tools": {"listChanged": True}},
                        "serverInfo": {"name": "contoso-erp-mcp", "version": "1.0.0"},
                    },
                },
                out_headers,
            )

        if rpc_method == "notifications/initialized":
            audit("allowed", 202, **who)
            return Response(202, None, out_headers)

        if rpc_method == "tools/list":
            tools = [tool.to_mcp() for tool in self._entitled_tools(tenant, scopes)]
            audit("allowed", 200, **who)
            return Response(200, {"jsonrpc": "2.0", "id": rpc_id, "result": {"tools": tools}}, out_headers)

        if rpc_method == "tools/call":
            # Re-check entitlement on every call. Never rely on list filtering.
            entitled = {tool.name for tool in self._entitled_tools(tenant, scopes)}
            if not isinstance(tool_name, str) or tool_name not in entitled:
                # Same error for "does not exist" and "not entitled" so the
                # catalog cannot be enumerated across tenants.
                audit("denied_tool", 200, **who)
                return Response(
                    200,
                    _rpc_error(rpc_id, RPC_INVALID_PARAMS, f"Unknown tool: {tool_name}", "unknown_or_not_entitled"),
                    out_headers,
                )

            # 8. Backend routing and credential selection. The client's token is
            #    never forwarded; the gateway asserts tenant context itself.
            forwarded = {k: v for k, v in headers.items() if k not in SPOOFABLE_HEADERS and k != "authorization"}
            forwarded[TRUSTED_TENANT_HEADER] = tenant_key
            forwarded["traceparent"] = correlation_id
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            result = self.backend.invoke(
                BackendCall(tenant.cell_backend, tool_name, arguments, tenant.credential_ref, forwarded)
            )
            audit("allowed", 200, backend=tenant.cell_backend, **who)
            return Response(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result}, out_headers)

        audit("method_not_found", 200, **who)
        return Response(
            200,
            _rpc_error(rpc_id, RPC_METHOD_NOT_FOUND, "Method not found", "unsupported_method"),
            out_headers,
        )


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def default_tenants() -> dict[str, TenantConfig]:
    return {
        "contoso": TenantConfig(
            "contoso", "active", frozenset({"orders", "field-service", "reporting"}),
            "https://cell-a.erp.contoso.internal", "kv:tenant-contoso-backend", 50, 10.0,
        ),
        "fabrikam": TenantConfig(
            "fabrikam", "active", frozenset({"orders"}),
            "https://cell-b.erp.contoso.internal", "kv:tenant-fabrikam-backend", 50, 10.0,
        ),
        "northwind": TenantConfig(
            "northwind", "active", frozenset({"orders", "reporting"}),
            "https://cell-a.erp.contoso.internal", "kv:tenant-northwind-backend", 2, 0.5,
        ),
        "tailspin": TenantConfig(
            "tailspin", "suspended", frozenset({"orders"}),
            "https://cell-b.erp.contoso.internal", "kv:tenant-tailspin-backend", 50, 10.0,
        ),
    }


def build_gateway() -> tuple[Gateway, InMemoryAuditSink, FakeBackend, FakeClock]:
    clock = FakeClock()
    audit = InMemoryAuditSink()
    backend = FakeBackend()
    gateway = Gateway(InMemoryTenantDirectory(default_tenants()), audit, backend, clock)
    return gateway, audit, backend, clock


def token_for(tenant: str, clock: FakeClock, scopes: str = "mcp.tools", **overrides: Any) -> str:
    claims: dict[str, Any] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": f"user-{tenant}-001",
        "azp": "00000000-0000-0000-0000-000000000000",
        TENANT_CLAIM: tenant,
        "scp": scopes,
        "exp": int(clock() + 600),
    }
    claims.update(overrides)
    return make_test_token(claims)


def rpc(method: str, rpc_id: int = 1, **params: Any) -> dict[str, Any]:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params:
        message["params"] = params
    return message


def call_tool(name: str, rpc_id: int = 1, **arguments: Any) -> dict[str, Any]:
    return rpc("tools/call", rpc_id, name=name, arguments=arguments)


def bearer(token: str, **extra: str) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    headers.update(extra)
    return headers


# --------------------------------------------------------------------------
# Self-test
# --------------------------------------------------------------------------


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scenario_entitled_call() -> None:
    gw, audit, backend, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("contoso", clock)), call_tool("field_service_list_work_orders")))
    _check(resp.status == 200 and resp.body is not None and "result" in resp.body, f"expected result, got {resp}")
    _check(len(backend.calls) == 1, "backend should be called once")
    _check(backend.calls[0].backend == "https://cell-a.erp.contoso.internal", "routed to wrong cell")
    _check(backend.calls[0].credential_ref == "kv:tenant-contoso-backend", "wrong per-tenant credential")
    _check(audit.records[-1].outcome == "allowed" and audit.records[-1].tenant == "contoso", "audit mismatch")


def scenario_tools_list_filtered() -> None:
    gw, _, _, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("fabrikam", clock)), rpc("tools/list")))
    names = {tool["name"] for tool in resp.body["result"]["tools"]} if resp.body else set()
    _check(names == {"orders_get_order", "orders_search_orders"}, f"fabrikam saw {sorted(names)}")
    resp = gw.handle(Request(bearer(token_for("contoso", clock, "mcp.tools orders.write")), rpc("tools/list")))
    names = {tool["name"] for tool in resp.body["result"]["tools"]} if resp.body else set()
    _check(names == {t.name for t in DEFAULT_CATALOG}, f"contoso saw {sorted(names)}")


def scenario_non_entitled_tool_blocked_without_list() -> None:
    gw, audit, backend, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("fabrikam", clock)), call_tool("reporting_run_report")))
    _check(resp.body is not None and resp.body.get("error", {}).get("code") == RPC_INVALID_PARAMS, f"got {resp}")
    _check(not backend.calls, "backend must not be called for a non-entitled tool")
    _check(audit.records[-1].outcome == "denied_tool", "denial must be audited")
    unknown = gw.handle(Request(bearer(token_for("fabrikam", clock)), call_tool("does_not_exist")))
    _check(unknown.body["error"]["data"] == resp.body["error"]["data"], "unknown and non-entitled must look the same")


def scenario_write_scope_required() -> None:
    gw, _, backend, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("contoso", clock)), call_tool("orders_create_order")))
    _check(resp.body is not None and "error" in resp.body, "write tool without orders.write scope must fail")
    resp = gw.handle(Request(bearer(token_for("contoso", clock, "mcp.tools orders.write")), call_tool("orders_create_order")))
    _check(resp.body is not None and "result" in resp.body and len(backend.calls) == 1, "write tool with scope should pass")


def scenario_rate_limit_exceeded() -> None:
    gw, audit, _, clock = build_gateway()
    token = token_for("northwind", clock)
    statuses = [gw.handle(Request(bearer(token), call_tool("orders_get_order", i))).status for i in range(3)]
    _check(statuses == [200, 200, 429], f"expected [200, 200, 429], got {statuses}")
    throttled = gw.handle(Request(bearer(token), call_tool("orders_get_order", 9)))
    _check(throttled.status == 429 and "Retry-After" in throttled.headers, "429 must carry Retry-After")
    _check(audit.records[-1].outcome == "throttled", "throttle must be audited")
    other = gw.handle(Request(bearer(token_for("contoso", clock)), call_tool("orders_get_order")))
    _check(other.status == 200, "one tenant's throttling must not affect another tenant")
    clock.advance(int(throttled.headers["Retry-After"]))
    recovered = gw.handle(Request(bearer(token), call_tool("orders_get_order", 10)))
    _check(recovered.status == 200, "bucket should refill after Retry-After")


def scenario_unknown_tenant() -> None:
    gw, audit, backend, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("unknown-co", clock)), rpc("tools/list")))
    _check(resp.status == 403 and resp.body["error"]["data"]["reason"] == "unknown_tenant", f"got {resp}")
    _check(not backend.calls and audit.records[-1].outcome == "unknown_tenant", "unknown tenant must fail closed")


def scenario_suspended_tenant() -> None:
    gw, _, _, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("tailspin", clock)), rpc("tools/list")))
    _check(resp.status == 403 and resp.body["error"]["data"]["reason"] == "tenant_not_active", f"got {resp}")


def scenario_spoofed_tenant_header_ignored() -> None:
    gw, audit, backend, clock = build_gateway()
    headers = bearer(token_for("fabrikam", clock), **{"X-Tenant-Id": "contoso", "X-Forwarded-For": "203.0.113.10"})
    listed = gw.handle(Request(headers, rpc("tools/list")))
    names = {tool["name"] for tool in listed.body["result"]["tools"]}
    _check("reporting_run_report" not in names, "spoofed header must not widen the tool list")
    resp = gw.handle(Request(headers, call_tool("orders_get_order")))
    _check(resp.status == 200 and backend.calls, "call should succeed as fabrikam")
    sent = backend.calls[-1]
    _check(sent.backend == "https://cell-b.erp.contoso.internal", "must route by token tenant, not header")
    _check(sent.headers.get(TRUSTED_TENANT_HEADER) == "fabrikam", "gateway must assert the token tenant")
    _check("x-tenant-id" not in sent.headers and "x-forwarded-for" not in sent.headers, "spoofable headers must be stripped")
    _check(audit.records[-1].tenant == "fabrikam" and audit.records[-1].spoofed_header_ignored, "spoof must be audited")


def scenario_client_token_not_forwarded() -> None:
    gw, _, backend, clock = build_gateway()
    gw.handle(Request(bearer(token_for("contoso", clock)), call_tool("orders_get_order")))
    _check(backend.calls and "authorization" not in backend.calls[-1].headers, "client token must not pass through")


def scenario_missing_token() -> None:
    gw, audit, _, _ = build_gateway()
    resp = gw.handle(Request({"Content-Type": "application/json"}, rpc("initialize")))
    _check(resp.status == 401, f"expected 401, got {resp.status}")
    _check("resource_metadata=" in resp.headers.get("WWW-Authenticate", ""), "401 must point to resource metadata")
    _check(audit.records[-1].outcome == "unauthenticated", "401 must be audited")


def scenario_wrong_audience() -> None:
    gw, _, _, clock = build_gateway()
    token = token_for("contoso", clock, aud="https://other-api.contoso.com")
    resp = gw.handle(Request(bearer(token), rpc("tools/list")))
    _check(resp.status == 401 and 'error="invalid_token"' in resp.headers["WWW-Authenticate"], f"got {resp}")


def scenario_initialize_handshake() -> None:
    gw, _, _, clock = build_gateway()
    resp = gw.handle(Request(bearer(token_for("contoso", clock)), rpc("initialize", protocolVersion=MCP_PROTOCOL_VERSION)))
    _check(resp.status == 200 and "Mcp-Session-Id" in resp.headers, "initialize should return a session id")
    _check(resp.body["result"]["capabilities"]["tools"]["listChanged"] is True, "should advertise list_changed")


def scenario_production_adapters_gated() -> None:
    for action in (
        lambda: ProductionTenantDirectory("https://entitlements.contoso.internal").get("contoso"),
        lambda: EventHubAuditSink("contoso-audit", "mcp-audit").emit(
            AuditRecord(0.0, "x", None, None, None, None, None, "test", 0)
        ),
    ):
        try:
            action()
        except NotImplementedError:
            continue
        raise AssertionError("production stubs must raise NotImplementedError")


SCENARIOS: tuple[tuple[str, Callable[[], None]], ...] = (
    ("entitled-tools-call", scenario_entitled_call),
    ("tools-list-filtered-by-entitlement", scenario_tools_list_filtered),
    ("non-entitled-tool-blocked-on-tools-call-without-list", scenario_non_entitled_tool_blocked_without_list),
    ("write-tool-requires-user-scope", scenario_write_scope_required),
    ("rate-limit-exceeded-returns-429", scenario_rate_limit_exceeded),
    ("unknown-tenant-returns-403", scenario_unknown_tenant),
    ("suspended-tenant-returns-403", scenario_suspended_tenant),
    ("spoofed-tenant-header-ignored", scenario_spoofed_tenant_header_ignored),
    ("client-token-not-forwarded", scenario_client_token_not_forwarded),
    ("missing-token-returns-401-with-resource-metadata", scenario_missing_token),
    ("wrong-audience-returns-401", scenario_wrong_audience),
    ("initialize-handshake", scenario_initialize_handshake),
    ("production-adapters-are-gated", scenario_production_adapters_gated),
)


def run_self_test() -> int:
    failures = 0
    for name, scenario in SCENARIOS:
        try:
            scenario()
        except Exception as exc:  # noqa: BLE001 - report every failure
            failures += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"PASS {name}")
    total = len(SCENARIOS)
    print(f"{total - failures}/{total} scenarios passed")
    return 0 if failures == 0 else 1


# --------------------------------------------------------------------------
# Demo
# --------------------------------------------------------------------------


def run_demo() -> int:
    gw, audit, _, clock = build_gateway()
    steps = (
        ("fabrikam lists tools", Request(bearer(token_for("fabrikam", clock)), rpc("tools/list"))),
        ("fabrikam calls an entitled tool", Request(bearer(token_for("fabrikam", clock)), call_tool("orders_get_order", 2, order="SO-1001"))),
        ("fabrikam skips the list and calls a reporting tool", Request(bearer(token_for("fabrikam", clock)), call_tool("reporting_run_report", 3))),
        ("fabrikam sends X-Tenant-Id: contoso", Request(bearer(token_for("fabrikam", clock), **{"X-Tenant-Id": "contoso"}), rpc("tools/list", 4))),
        ("unknown tenant", Request(bearer(token_for("unknown-co", clock)), rpc("tools/list", 5))),
        ("no token", Request({}, rpc("initialize", 6))),
    )
    for title, request in steps:
        resp = gw.handle(request)
        print(f"== {title}")
        print(f"   HTTP {resp.status} {json.dumps(resp.headers)}")
        if resp.body is not None:
            print("   " + json.dumps(resp.body))
    print("== audit records (no bodies)")
    for record in audit.records:
        print("   " + json.dumps(record.__dict__))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Simulate a multi-tenant MCP gateway pipeline (offline, test only).")
    parser.add_argument("--self-test", action="store_true", help="run the offline scenarios and exit 0 or 1")
    parser.add_argument("--demo", action="store_true", help="print a short narrated run of the pipeline")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.demo:
        return run_demo()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
