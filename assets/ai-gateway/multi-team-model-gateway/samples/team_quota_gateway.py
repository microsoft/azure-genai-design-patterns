#!/usr/bin/env python3
"""Offline simulation of a multi-team model gateway with per-team token budgets.

TEST ONLY. Standard library only (Python 3.10+). No network calls, no real keys,
no real clock: time is a logical clock that the scenarios advance by hand, so
every run is deterministic. Team names, keys and hosts are fictional. Budgets
and token counts are illustrative configuration; they say nothing about real
capacity, latency or cost.

What the simulation models (see ../docs/architecture.md)
  - The deployment shape from ../infra: one API Management product and one
    subscription per team, one Foundry project per team, and a project
    connection (category ApiManagement, auth ApiKey) carrying the team's
    gateway key. Members get Foundry User on their own project only.
  - The API-scope policy: reject calls with no product (all-access and
    API-scoped subscriptions), reject deployments that are not on the
    allow-list before any budget is spent, run the product policy, strip
    api-key / Authorization / x-team / ?api-key, and call the model with the
    gateway's managed identity.
  - The product-scope token limit: a token bucket keyed by the product name,
    prompt tokens estimated before the call, the real total reconciled after
    it, 429 with retry-after when the bucket cannot cover the estimate, and
    the x-remaining-tokens / x-tokens-consumed headers. An optional token
    quota returns 403 when a longer-period allowance is used up.
  - A per-team token metric (dimensions Team, Subscription ID, API ID,
    Deployment) that feeds a chargeback ledger, and gateway logs that record
    whether the backend was called, without keys or prompt text.
  - Foundry project rules: connection names unique across every project in
    the account, connections scoped to their project, and credentials
    returned only to members of that project.
  - The team client from ../src/team_client.py: read the connection, call the
    gateway, re-read the connection once after a 401 (key rotation).

Modes
  --self-test   named scenarios, PASS/FAIL, exit 1 on any failure
  --demo        narrated walkthrough: two teams, a burst, isolation, rotation,
                chargeback

Production stubs at the bottom raise NotImplementedError and point at the
deployable pieces in ../infra and ../src.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import sys
from dataclasses import dataclass, field
from typing import Callable, Protocol
from urllib.parse import parse_qs, urlsplit

# ---------------------------------------------------------------------------
# Fictional environment. None of these are real hosts or identifiers.
# ---------------------------------------------------------------------------

GATEWAY_URL = "https://apim-contoso-aigw.example.com"  # stands in for properties.gatewayUrl
API_PATH_PREFIX = "aigw"
GATEWAY_TARGET = f"{GATEWAY_URL}/{API_PATH_PREFIX}"  # what the project connection stores
MODEL_ENDPOINT = "https://aif-contoso.example.com"  # the shared model account; teams never see it
APIM_RESOURCE_ID = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-contoso-aigw"
    "/providers/Microsoft.ApiManagement/service/apim-contoso-aigw"
)
MI_RESOURCE = "https://cognitiveservices.azure.com"
API_ID = "foundry-openai"
API_VERSION = "2024-10-21"
DEFAULT_DEPLOYMENT = "chat"
WINDOW_SECONDS = 60
METRIC_NAMESPACE = "multi-team-model-gateway"
METRIC_NAMES = ("Prompt Tokens", "Completion Tokens", "Total Tokens")
MAX_METRIC_DIMENSIONS = 5
STRIPPED_HEADERS = ("api-key", "authorization", "x-team")
STRIPPED_QUERY = ("api-key",)
OPERATIONS = ("chat/completions", "embeddings")


# ---------------------------------------------------------------------------
# Wire objects
# ---------------------------------------------------------------------------

@dataclass
class Request:
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: dict = field(default_factory=dict)
    method: str = "POST"

    def __post_init__(self) -> None:
        self.headers = {k.lower(): v for k, v in self.headers.items()}


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: dict = field(default_factory=dict)

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())


def chat_url(target: str, deployment: str = DEFAULT_DEPLOYMENT, operation: str = "chat/completions",
             api_key_in_query: str | None = None) -> str:
    url = f"{target}/openai/deployments/{deployment}/{operation}?api-version={API_VERSION}"
    if api_key_in_query:
        url += f"&api-key={api_key_in_query}"
    return url


def chat_body(prompt: str, max_tokens: int = 40) -> dict:
    return {"messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens}


def classify_429(response: Response) -> str:
    """Same rule as src/tpm_burst_test.py: the gateway's own throttle has a
    top-level statusCode; a throttle passed through from the model deployment
    has the Azure OpenAI error envelope."""
    if response.status != 429:
        return "not-throttled"
    if "statusCode" in response.body:
        return "gateway-token-limit"
    if "error" in response.body:
        return "model-capacity"
    return "unknown"


# ---------------------------------------------------------------------------
# Seams (Protocols) and their in-memory fakes
# ---------------------------------------------------------------------------

class Clock(Protocol):
    def now(self) -> float: ...


class LogicalClock:
    """Logical seconds. They order events and drive refill; they are not wall time."""

    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


class PromptTokenEstimator(Protocol):
    def estimate(self, body: dict) -> int: ...


class CharCountEstimator:
    """About four characters per token plus a small per-message overhead.
    A real gateway uses a tokenizer; the point here is that it is an estimate."""

    def estimate(self, body: dict) -> int:
        total = 3
        for message in body.get("messages", []):
            total += 4 + math.ceil(len(str(message.get("content", ""))) / 4)
        if "input" in body:
            total += math.ceil(len(str(body["input"])) / 4)
        return total


class ManagedIdentity(Protocol):
    def get_token(self, resource: str) -> str: ...


class FakeManagedIdentity:
    """Stands in for the gateway's system-assigned identity."""

    def __init__(self) -> None:
        self.issued: list[str] = []

    def get_token(self, resource: str) -> str:
        self.issued.append(resource)
        return f"sim-mi-token|{resource}"


@dataclass
class MetricRecord:
    namespace: str
    name: str
    value: int
    dimensions: dict[str, str]


class MetricSink(Protocol):
    def emit(self, record: MetricRecord) -> None: ...


class MemoryMetricSink:
    def __init__(self) -> None:
        self.records: list[MetricRecord] = []

    def emit(self, record: MetricRecord) -> None:
        self.records.append(record)


class ModelBackend(Protocol):
    def send(self, request: Request) -> Response: ...


class FakeModelBackend:
    """The shared model account. Key auth is disabled: only a managed identity
    token for the Cognitive Services resource is accepted. It counts prompt
    tokens with its own rule, which differs a little from the gateway's
    estimate, so the reconciliation step has something to do."""

    def __init__(self, deployments: tuple[str, ...] = (DEFAULT_DEPLOYMENT,),
                 completion_tokens: int = 30, capacity_tokens: int | None = None) -> None:
        self.deployments = set(deployments)
        self.completion_tokens = completion_tokens
        self.capacity_tokens = capacity_tokens
        self.received: list[Request] = []

    def send(self, request: Request) -> Response:
        self.received.append(copy.deepcopy(request))
        if "api-key" in request.headers:
            return Response(401, body={"error": {"code": "AuthenticationTypeDisabled",
                                                 "message": "Key based authentication is disabled."}})
        if request.headers.get("authorization") != f"Bearer sim-mi-token|{MI_RESOURCE}":
            return Response(401, body={"error": {"code": "401", "message": "Missing or invalid token."}})
        parts = urlsplit(request.url).path.split("/")
        deployment = parts[3] if len(parts) > 3 else ""
        if deployment not in self.deployments:
            return Response(404, body={"error": {"code": "DeploymentNotFound", "message": "No such deployment."}})
        prompt = 2 + sum(3 + math.ceil(len(str(m.get("content", ""))) / 4)
                         for m in request.body.get("messages", []))
        completion = min(int(request.body.get("max_tokens", self.completion_tokens)), self.completion_tokens)
        total = prompt + completion
        if self.capacity_tokens is not None:
            if total > self.capacity_tokens:
                return Response(429, headers={"retry-after": "10"},
                                body={"error": {"code": "429",
                                                "message": "The deployment is over its capacity. Retry later."}})
            self.capacity_tokens -= total
        return Response(200, body={
            "object": "chat.completion",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "simulated reply"}}],
            "usage": {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total},
        })


# ---------------------------------------------------------------------------
# API Management model: products, subscriptions, counters, policy pipeline
# ---------------------------------------------------------------------------

@dataclass
class GatewayContext:
    product: "Product | None"
    subscription: "Subscription"
    request: Request
    deployment: str


def product_name_counter_key(ctx: GatewayContext) -> str:
    """counter-key="@(context.Product.Name)" from policies/team-token-limit.xml."""
    assert ctx.product is not None
    return ctx.product.display_name


@dataclass
class Product:
    name: str
    display_name: str
    tokens_per_minute: int
    token_quota: int | None = None
    quota_period_seconds: int | None = None
    counter_key: Callable[[GatewayContext], str] = product_name_counter_key


@dataclass
class Subscription:
    name: str
    scope: str  # "product:<name>", "api:<api>" or "all-apis"
    primary_key: str
    secondary_key: str
    state: str = "active"

    @property
    def product_name(self) -> str | None:
        return self.scope.split(":", 1)[1] if self.scope.startswith("product:") else None


@dataclass
class TokenBucket:
    """v2-tier style token bucket: capacity is the per-minute budget and it
    refills continuously. The balance may go below zero when the real total
    exceeds the estimate; later calls wait until it recovers."""

    capacity: int
    balance: float
    updated_at: float

    @property
    def rate(self) -> float:
        return self.capacity / WINDOW_SECONDS

    def refill(self, now: float) -> None:
        self.balance = min(float(self.capacity), self.balance + (now - self.updated_at) * self.rate)
        self.updated_at = now

    def seconds_until(self, needed: int) -> int:
        deficit = needed - self.balance
        return 0 if deficit <= 0 else max(1, math.ceil(deficit / self.rate))


@dataclass
class QuotaCounter:
    used: int
    period_start: float


@dataclass
class GatewayLogEntry:
    """Shape of a GatewayLogs row as far as triage needs it. No keys, no bodies."""

    at: float
    product: str | None
    subscription: str | None
    deployment: str
    status: int
    backend_called: bool
    backend_status: int | None
    total_tokens: int | None


def json_error(status: int, message: str, headers: dict[str, str] | None = None) -> Response:
    """Gateway-generated error: top-level statusCode, like API Management's own responses."""
    return Response(status, headers=dict(headers or {}), body={"statusCode": status, "message": message})


def policy_error(status: int, code: str, message: str) -> Response:
    """return-response bodies from policies/api-gateway-policy.xml."""
    return Response(status, headers={"content-type": "application/json"},
                    body={"error": {"code": code, "message": message}})


class KeyFactory:
    def __init__(self, seed: str = "sim") -> None:
        self._seed = seed
        self._n = 0

    def new(self) -> str:
        self._n += 1
        return "simkey-" + hashlib.sha256(f"{self._seed}:{self._n}".encode()).hexdigest()[:32]


class ModelGateway:
    def __init__(self, clock: Clock, estimator: PromptTokenEstimator, backend: ModelBackend,
                 identity: ManagedIdentity, metrics: MetricSink,
                 allowed_deployments: tuple[str, ...] = (DEFAULT_DEPLOYMENT,),
                 emit_metrics: bool = True) -> None:
        self.clock = clock
        self.estimator = estimator
        self.backend = backend
        self.identity = identity
        self.metrics = metrics
        self.allowed_deployments = tuple(allowed_deployments)
        self.emit_metrics = emit_metrics
        self.products: dict[str, Product] = {}
        self.subscriptions: dict[str, Subscription] = {}
        self.counters: dict[str, TokenBucket] = {}
        self.quotas: dict[str, QuotaCounter] = {}
        self.logs: list[GatewayLogEntry] = []
        self._keys = KeyFactory()

    # --- management plane -------------------------------------------------

    def upsert_product(self, name: str, tokens_per_minute: int, token_quota: int | None = None,
                       quota_period_seconds: int | None = None) -> Product:
        product = self.products.get(name)
        if product is None:
            product = Product(name=name, display_name=name, tokens_per_minute=tokens_per_minute,
                              token_quota=token_quota, quota_period_seconds=quota_period_seconds)
            self.products[name] = product
        else:
            product.tokens_per_minute = tokens_per_minute
            product.token_quota = token_quota
            product.quota_period_seconds = quota_period_seconds
        return product

    def upsert_subscription(self, name: str, scope: str) -> Subscription:
        sub = self.subscriptions.get(name)
        if sub is None:
            sub = Subscription(name, scope, self._keys.new(), self._keys.new())
            self.subscriptions[name] = sub
        else:
            sub.scope = scope
        return sub

    def list_secrets(self, name: str) -> dict[str, str]:
        sub = self.subscriptions[name]
        return {"primaryKey": sub.primary_key, "secondaryKey": sub.secondary_key}

    def regenerate_key(self, name: str, which: str = "primary") -> None:
        sub = self.subscriptions[name]
        if which == "primary":
            sub.primary_key = self._keys.new()
        else:
            sub.secondary_key = self._keys.new()

    # --- data plane -------------------------------------------------------

    def _find_subscription(self, key: str) -> Subscription | None:
        for sub in self.subscriptions.values():
            if key in (sub.primary_key, sub.secondary_key):
                return sub
        return None

    def _log(self, ctx_product: str | None, sub: str | None, deployment: str, response: Response,
             backend_called: bool, backend_status: int | None, total: int | None) -> Response:
        self.logs.append(GatewayLogEntry(self.clock.now(), ctx_product, sub, deployment, response.status,
                                         backend_called, backend_status, total))
        return response

    def handle(self, request: Request) -> Response:
        parts = urlsplit(request.url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        prefix = f"/{API_PATH_PREFIX}/openai/deployments/"
        if f"{parts.scheme}://{parts.netloc}" != GATEWAY_URL or not parts.path.startswith(prefix):
            return json_error(404, "Resource not found")
        deployment, _, operation = parts.path[len(prefix):].partition("/")
        if operation not in OPERATIONS:
            return json_error(404, "Resource not found")

        key = request.headers.get("api-key") or query.get("api-key")
        if not key:
            return self._log(None, None, deployment, json_error(
                401, "Access denied due to missing subscription key."), False, None, None)
        sub = self._find_subscription(key)
        if sub is None or sub.state != "active":
            return self._log(None, None, deployment, json_error(
                401, "Access denied due to invalid subscription key. Use a key for an active subscription."),
                False, None, None)

        product = self.products.get(sub.product_name) if sub.product_name else None
        ctx = GatewayContext(product, sub, request, deployment)
        product_name = product.display_name if product else None

        # API scope, step 1: no product means no team budget.
        if product is None:
            return self._log(None, sub.name, deployment, policy_error(
                403, "team_product_required",
                "Use the gateway key distributed through your team's Foundry project connection."),
                False, None, None)
        # API scope, step 2: deployment allow-list, before base, so no budget is spent.
        if deployment not in self.allowed_deployments:
            return self._log(product_name, sub.name, deployment, policy_error(
                404, "deployment_not_allowed", "This model deployment is not exposed through the gateway."),
                False, None, None)

        # base -> product scope: token limit (and optional quota).
        now = self.clock.now()
        counter_key = product.counter_key(ctx)
        bucket = self.counters.get(counter_key)
        if bucket is None:
            bucket = TokenBucket(product.tokens_per_minute, float(product.tokens_per_minute), now)
            self.counters[counter_key] = bucket
        bucket.capacity = product.tokens_per_minute
        bucket.refill(now)
        estimate = self.estimator.estimate(request.body)
        if bucket.balance < estimate:
            wait = bucket.seconds_until(estimate)
            return self._log(product_name, sub.name, deployment, json_error(
                429, f"Token limit is exceeded. Try again in {wait} seconds.",
                {"retry-after": str(wait), "x-remaining-tokens": "0"}), False, None, None)
        quota = None
        if product.token_quota is not None and product.quota_period_seconds:
            quota = self.quotas.setdefault(counter_key, QuotaCounter(0, now))
            if now - quota.period_start >= product.quota_period_seconds:
                quota.used, quota.period_start = 0, now
            if quota.used + estimate > product.token_quota:
                remaining = math.ceil(quota.period_start + product.quota_period_seconds - now)
                return self._log(product_name, sub.name, deployment, json_error(
                    403, "Token quota is exceeded.", {"retry-after": str(max(1, remaining))}),
                    False, None, None)
        bucket.balance -= estimate

        # API scope, after base: strip caller credentials, use the managed identity, route.
        forwarded_headers = {k: v for k, v in request.headers.items() if k not in STRIPPED_HEADERS}
        forwarded_headers["authorization"] = f"Bearer {self.identity.get_token(MI_RESOURCE)}"
        forwarded_query = "&".join(f"{k}={v}" for k, v in query.items() if k not in STRIPPED_QUERY)
        backend_url = f"{MODEL_ENDPOINT}/openai/deployments/{deployment}/{operation}"
        if forwarded_query:
            backend_url += f"?{forwarded_query}"
        backend_response = self.backend.send(Request(backend_url, forwarded_headers, copy.deepcopy(request.body)))

        headers = dict(backend_response.headers)
        headers["x-remaining-tokens"] = str(max(0, math.floor(bucket.balance)))
        if backend_response.status != 200:
            # The estimate stays charged: no usage came back to reconcile against.
            return self._log(product_name, sub.name, deployment,
                             Response(backend_response.status, headers, backend_response.body),
                             True, backend_response.status, None)

        usage = backend_response.body.get("usage", {})
        total = int(usage.get("total_tokens", 0))
        bucket.balance += estimate - total
        if quota is not None:
            quota.used += total
        headers["x-tokens-consumed"] = str(total)
        if self.emit_metrics:
            dims = {"Team": product_name or "", "Subscription ID": sub.name, "API ID": API_ID,
                    "Deployment": deployment}
            for name, value in zip(METRIC_NAMES, (usage.get("prompt_tokens", 0),
                                                  usage.get("completion_tokens", 0), total)):
                self.metrics.emit(MetricRecord(METRIC_NAMESPACE, name, int(value), dict(dims)))
        return self._log(product_name, sub.name, deployment, Response(200, headers, backend_response.body),
                         True, 200, total)


# ---------------------------------------------------------------------------
# Foundry model: account, projects, connections, project-scoped RBAC
# ---------------------------------------------------------------------------

class AccessDenied(Exception):
    pass


class ConnectionNotFound(Exception):
    pass


class ConnectionNameConflict(Exception):
    pass


@dataclass
class Connection:
    name: str
    category: str
    target: str
    auth_type: str
    key: str | None
    is_shared_to_all: bool
    metadata: dict[str, str]


@dataclass
class Project:
    name: str
    foundry_users: set[str] = field(default_factory=set)
    connections: dict[str, Connection] = field(default_factory=dict)


class FoundryAccount:
    def __init__(self) -> None:
        self.projects: dict[str, Project] = {}

    def upsert_project(self, name: str) -> Project:
        return self.projects.setdefault(name, Project(name))

    def assign_foundry_user(self, project: str, principal: str) -> None:
        self.projects[project].foundry_users.add(principal)

    def upsert_connection(self, project: str, connection: Connection) -> None:
        for other in self.projects.values():
            if other.name != project and connection.name in other.connections:
                raise ConnectionNameConflict(
                    f"connection {connection.name!r} already exists in project {other.name!r}")
        self.projects[project].connections[connection.name] = connection

    def _authorize(self, principal: str, project: str) -> Project:
        proj = self.projects.get(project)
        if proj is None or principal not in proj.foundry_users:
            raise AccessDenied(f"{principal} has no role on project {project}")
        return proj

    def get_connection(self, principal: str, project: str, name: str, include_credentials: bool) -> Connection:
        proj = self._authorize(principal, project)
        conn = proj.connections.get(name)
        if conn is None:
            raise ConnectionNotFound(f"no connection {name!r} in project {project!r}")
        result = copy.deepcopy(conn)
        if not include_credentials:
            result.key = None
        return result

    def list_connections(self, principal: str, project: str) -> list[Connection]:
        proj = self._authorize(principal, project)
        listed = []
        for conn in proj.connections.values():
            item = copy.deepcopy(conn)
            item.key = None
            listed.append(item)
        return listed


# ---------------------------------------------------------------------------
# Deployment (mirrors infra/main.bicep and infra/modules/team.bicep)
# ---------------------------------------------------------------------------

@dataclass
class TeamConfig:
    name: str
    tokens_per_minute: int
    member_principal_ids: tuple[str, ...] = ()
    token_quota: int | None = None
    quota_period_seconds: int | None = None


@dataclass
class TeamOutputs:
    team: str
    product_name: str
    subscription_name: str
    project_name: str
    connection_name: str


def validate_config(teams: list[TeamConfig], foundry_user_role_id: str) -> list[str]:
    """The checks the Bicep parameters enforce, so a bad file fails before deployment."""
    errors = []
    if len(foundry_user_role_id) != 36:
        errors.append("foundryUserRoleDefinitionId must be a 36-character role definition id")
    if not teams:
        errors.append("teams must contain at least one team")
    seen = set()
    for team in teams:
        if not (2 <= len(team.name) <= 20) or not all(c.islower() or c.isdigit() or c == "-" for c in team.name):
            errors.append(f"team name {team.name!r} must be 2-20 lowercase letters, digits or hyphens")
        if team.tokens_per_minute < 1:
            errors.append(f"team {team.name!r}: tokensPerMinute must be at least 1")
        if team.name in seen:
            errors.append(f"team {team.name!r} is listed twice")
        seen.add(team.name)
    return errors


@dataclass
class Environment:
    clock: LogicalClock
    gateway: ModelGateway
    account: FoundryAccount
    backend: FakeModelBackend
    metrics: MemoryMetricSink
    identity: FakeManagedIdentity
    outputs: dict[str, TeamOutputs] = field(default_factory=dict)


def deploy_team(env: Environment, team: TeamConfig) -> TeamOutputs:
    product = env.gateway.upsert_product(f"team-{team.name}", team.tokens_per_minute,
                                         team.token_quota, team.quota_period_seconds)
    sub = env.gateway.upsert_subscription(f"sub-team-{team.name}", f"product:{product.name}")
    project = env.account.upsert_project(f"team-{team.name}")
    env.account.upsert_connection(project.name, Connection(
        name=f"ai-gateway-team-{team.name}", category="ApiManagement", target=GATEWAY_TARGET,
        auth_type="ApiKey", key=env.gateway.list_secrets(sub.name)["primaryKey"], is_shared_to_all=False,
        metadata={"ResourceId": APIM_RESOURCE_ID, "type": "azure_api_management"}))
    for principal in team.member_principal_ids:
        env.account.assign_foundry_user(project.name, principal)
    out = TeamOutputs(team.name, product.name, sub.name, project.name, f"ai-gateway-team-{team.name}")
    env.outputs[team.name] = out
    return out


def build_environment(teams: list[TeamConfig], completion_tokens: int = 30,
                      capacity_tokens: int | None = None,
                      allowed_deployments: tuple[str, ...] = (DEFAULT_DEPLOYMENT,)) -> Environment:
    errors = validate_config(teams, "0" * 8 + "-0000-0000-0000-" + "0" * 12)
    if errors:
        raise ValueError("; ".join(errors))
    clock = LogicalClock()
    backend = FakeModelBackend(deployments=(DEFAULT_DEPLOYMENT, "reasoning"),
                               completion_tokens=completion_tokens, capacity_tokens=capacity_tokens)
    metrics = MemoryMetricSink()
    identity = FakeManagedIdentity()
    gateway = ModelGateway(clock, CharCountEstimator(), backend, identity, metrics, allowed_deployments)
    env = Environment(clock, gateway, FoundryAccount(), backend, metrics, identity)
    for team in teams:
        deploy_team(env, team)
    return env


# ---------------------------------------------------------------------------
# Team client (mirrors src/team_client.py)
# ---------------------------------------------------------------------------

class TeamClient:
    def __init__(self, env: Environment, principal: str, team: str) -> None:
        self.env = env
        self.principal = principal
        self.team = team
        self.project = f"team-{team}"
        self.connection_name = f"ai-gateway-team-{team}"
        self._target: str | None = None
        self._key: str | None = None
        self.reads = 0

    def refresh(self) -> None:
        conn = self.env.account.get_connection(self.principal, self.project, self.connection_name,
                                               include_credentials=True)
        if not conn.key:
            raise RuntimeError("connection has no key")
        if not conn.target.startswith("https://"):
            raise RuntimeError("connection target must be https")
        self._target, self._key = conn.target, conn.key
        self.reads += 1

    def chat(self, prompt: str = "Summarize the release notes.", max_tokens: int = 40,
             deployment: str = DEFAULT_DEPLOYMENT, extra_headers: dict[str, str] | None = None) -> Response:
        if self._key is None:
            self.refresh()
        response = self._send(prompt, max_tokens, deployment, extra_headers)
        if response.status == 401:
            self.refresh()
            response = self._send(prompt, max_tokens, deployment, extra_headers)
        return response

    def _send(self, prompt: str, max_tokens: int, deployment: str,
              extra_headers: dict[str, str] | None) -> Response:
        assert self._target and self._key
        headers = {"api-key": self._key, "x-team": self.team, "content-type": "application/json"}
        headers.update(extra_headers or {})
        return self.env.gateway.handle(Request(chat_url(self._target, deployment), headers,
                                               chat_body(prompt, max_tokens)))


def chargeback(metrics: list[MetricRecord]) -> dict[str, dict[str, float]]:
    """Total tokens and share per team from the token metric (the Team dimension)."""
    totals: dict[str, int] = {}
    for record in metrics:
        if record.name == "Total Tokens":
            team = record.dimensions.get("Team", "")
            totals[team] = totals.get(team, 0) + record.value
    grand = sum(totals.values())
    return {team: {"tokens": tokens, "share": (tokens / grand) if grand else 0.0}
            for team, tokens in sorted(totals.items())}


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

ALICE = "principal-alice"  # member of team alpha
BOB = "principal-bob"  # member of team beta
OPS = "principal-platform-ops"  # member of both (runs the isolation test)

LONG_PROMPT = "Rewrite the attached product description for a younger audience. " * 6


def two_teams(alpha_tpm: int = 400, beta_tpm: int = 400, **kwargs) -> Environment:
    return build_environment([
        TeamConfig("alpha", alpha_tpm, (ALICE, OPS)),
        TeamConfig("beta", beta_tpm, (BOB, OPS)),
    ], **kwargs)


def burst_until_throttled(client: TeamClient, limit: int = 200) -> tuple[int, Response]:
    for i in range(1, limit + 1):
        response = client.chat()
        if response.status == 429:
            return i, response
    raise AssertionError("burst never throttled")


SCENARIOS: list[tuple[str, Callable[[], None]]] = []


def scenario(fn: Callable[[], None]) -> Callable[[], None]:
    SCENARIOS.append((fn.__name__, fn))
    return fn


@scenario
def happy_path_team_call_with_budget_headers() -> None:
    env = two_teams()
    r = TeamClient(env, ALICE, "alpha").chat()
    assert r.status == 200, r
    consumed = int(r.header("x-tokens-consumed"))
    assert consumed == r.body["usage"]["total_tokens"]
    assert 0 <= int(r.header("x-remaining-tokens")) < 400


@scenario
def backend_gets_managed_identity_token_only() -> None:
    env = two_teams()
    TeamClient(env, ALICE, "alpha").chat()
    sent = env.backend.received[-1]
    assert sent.headers["authorization"] == f"Bearer sim-mi-token|{MI_RESOURCE}"
    assert "api-key" not in sent.headers and "x-team" not in sent.headers
    assert env.identity.issued == [MI_RESOURCE]
    assert sent.url.startswith(MODEL_ENDPOINT)


@scenario
def caller_bearer_token_and_query_key_are_stripped() -> None:
    env = two_teams()
    key = env.gateway.list_secrets("sub-team-alpha")["primaryKey"]
    r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET, api_key_in_query=key),
                                   {"Authorization": "Bearer user-token-from-caller"}, chat_body("hi")))
    assert r.status == 200, r
    sent = env.backend.received[-1]
    assert "user-token-from-caller" not in json.dumps(sent.headers)
    assert key not in sent.url and "api-key" not in sent.url


@scenario
def missing_key_rejected_401() -> None:
    env = two_teams()
    r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET), {}, chat_body("hi")))
    assert r.status == 401 and not env.backend.received


@scenario
def unknown_key_rejected_401() -> None:
    env = two_teams()
    r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET), {"api-key": "simkey-not-issued"}, chat_body("hi")))
    assert r.status == 401 and not env.backend.received


@scenario
def suspended_subscription_rejected() -> None:
    env = two_teams()
    client = TeamClient(env, ALICE, "alpha")
    assert client.chat().status == 200
    env.gateway.subscriptions["sub-team-alpha"].state = "suspended"
    assert client.chat().status == 401
    assert TeamClient(env, BOB, "beta").chat().status == 200


@scenario
def non_product_subscriptions_blocked_403() -> None:
    env = two_teams()
    for name, scope in (("master", "all-apis"), ("api-only", f"api:{API_ID}")):
        sub = env.gateway.upsert_subscription(name, scope)
        r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET), {"api-key": sub.primary_key}, chat_body("hi")))
        assert r.status == 403 and r.body["error"]["code"] == "team_product_required", (scope, r)
    assert not env.backend.received and not env.gateway.counters


@scenario
def disallowed_deployment_404_spends_no_budget() -> None:
    env = two_teams()
    client = TeamClient(env, ALICE, "alpha")
    client.refresh()
    r = client.chat(deployment="reasoning")
    assert r.status == 404 and r.body["error"]["code"] == "deployment_not_allowed"
    assert "team-alpha" not in env.gateway.counters, "a rejected deployment must not touch the budget"
    assert not env.backend.received


@scenario
def oversized_prompt_blocked_before_backend() -> None:
    env = two_teams(alpha_tpm=60)
    r = TeamClient(env, ALICE, "alpha").chat(prompt=LONG_PROMPT)
    assert r.status == 429 and classify_429(r) == "gateway-token-limit"
    assert int(r.header("retry-after")) >= 1
    assert not env.backend.received


@scenario
def burst_team_gets_429_with_retry_after() -> None:
    env = two_teams()
    calls, r = burst_until_throttled(TeamClient(env, ALICE, "alpha"))
    assert calls > 1
    assert r.header("retry-after") and int(r.header("retry-after")) >= 1
    assert r.header("x-remaining-tokens") == "0"
    assert "Token limit is exceeded" in r.body["message"]


@scenario
def gateway_throttle_never_reaches_backend() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    burst_until_throttled(alpha)
    before = len(env.backend.received)
    assert alpha.chat().status == 429
    assert len(env.backend.received) == before
    last = env.gateway.logs[-1]
    assert last.status == 429 and not last.backend_called


@scenario
def control_team_unaffected_by_burst() -> None:
    env = two_teams()
    burst_until_throttled(TeamClient(env, OPS, "alpha"))
    r = TeamClient(env, OPS, "beta").chat()
    assert r.status == 200, "beta must keep its own budget while alpha is throttled"


@scenario
def spoofed_x_team_header_does_not_move_usage() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    for _ in range(3):
        alpha.chat(extra_headers={"x-team": "beta"})
    teams = {r.dimensions["Team"] for r in env.metrics.records}
    assert teams == {"team-alpha"}, teams
    assert "team-beta" not in env.gateway.counters


@scenario
def anti_pattern_header_counter_key_is_spoofable() -> None:
    env = two_teams()
    env.gateway.products["team-alpha"].counter_key = lambda ctx: ctx.request.headers.get("x-team", "")
    alpha = TeamClient(env, ALICE, "alpha")
    for name in ("alpha", "x1", "x2", "x3", "x4", "x5", "x6", "x7", "x8", "x9"):
        assert alpha.chat(extra_headers={"x-team": name}).status == 200
    assert len(env.gateway.counters) >= 10, "every spoofed header value got a fresh budget"


@scenario
def retry_after_is_honoured() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    _, r = burst_until_throttled(alpha)
    env.clock.advance(int(r.header("retry-after")))
    assert alpha.chat().status == 200


@scenario
def budget_refills_gradually_not_all_at_once() -> None:
    env = two_teams(alpha_tpm=300)
    alpha = TeamClient(env, ALICE, "alpha")
    burst_until_throttled(alpha)
    env.clock.advance(1)
    bucket = env.gateway.counters["team-alpha"]
    bucket.refill(env.clock.now())
    assert bucket.balance < 300 / 2
    env.clock.advance(WINDOW_SECONDS)
    bucket.refill(env.clock.now())
    assert bucket.balance == 300


@scenario
def actual_usage_reconciled_after_response() -> None:
    env = two_teams(alpha_tpm=1000)
    r = TeamClient(env, ALICE, "alpha").chat(max_tokens=40)
    estimate = CharCountEstimator().estimate(chat_body("Summarize the release notes.", 40))
    total = int(r.header("x-tokens-consumed"))
    assert total != estimate, "the scenario needs the estimate and the real count to differ"
    assert math.isclose(env.gateway.counters["team-alpha"].balance, 1000 - total)


@scenario
def underestimate_overdraws_then_blocks() -> None:
    env = two_teams(alpha_tpm=60, completion_tokens=40)
    alpha = TeamClient(env, ALICE, "alpha")
    first = alpha.chat(prompt="Hi", max_tokens=40)
    assert first.status == 200
    bucket = env.gateway.counters["team-alpha"]
    assert bucket.balance < 60
    second = alpha.chat(prompt="Hi", max_tokens=40)
    assert second.status == 200 and bucket.balance < 0, "the real total can overdraw the bucket"
    assert alpha.chat(prompt="Hi").status == 429


@scenario
def token_quota_returns_403_when_period_used() -> None:
    env = build_environment([TeamConfig("alpha", 10_000, (ALICE,), token_quota=150, quota_period_seconds=3600)])
    alpha = TeamClient(env, ALICE, "alpha")
    statuses = [alpha.chat().status for _ in range(6)]
    assert 200 in statuses and statuses[-1] == 403, statuses
    env.clock.advance(3600)
    assert alpha.chat().status == 200


@scenario
def model_capacity_429_is_distinguishable() -> None:
    env = two_teams(capacity_tokens=100)
    alpha = TeamClient(env, ALICE, "alpha")
    statuses = [alpha.chat() for _ in range(4)]
    capacity = [r for r in statuses if r.status == 429]
    assert capacity and classify_429(capacity[0]) == "model-capacity"
    assert env.gateway.logs[-1].backend_called and env.gateway.logs[-1].backend_status == 429
    env2 = two_teams()
    _, gateway_429 = burst_until_throttled(TeamClient(env2, ALICE, "alpha"))
    assert classify_429(gateway_429) == "gateway-token-limit"


@scenario
def connection_names_unique_across_projects() -> None:
    env = two_teams()
    shared = Connection("ai-gateway", "ApiManagement", GATEWAY_TARGET, "ApiKey", "simkey-x", False, {})
    env.account.upsert_connection("team-alpha", shared)
    try:
        env.account.upsert_connection("team-beta", copy.deepcopy(shared))
    except ConnectionNameConflict:
        pass
    else:
        raise AssertionError("a second project reused a connection name")
    names = [c for p in env.account.projects.values() for c in p.connections]
    assert "ai-gateway-team-alpha" in names and "ai-gateway-team-beta" in names


@scenario
def connection_is_scoped_to_its_project() -> None:
    env = two_teams()
    try:
        env.account.get_connection(OPS, "team-beta", "ai-gateway-team-alpha", include_credentials=True)
    except ConnectionNotFound:
        pass
    else:
        raise AssertionError("alpha's connection must not be visible from beta's project")


@scenario
def member_cannot_read_other_team_connection() -> None:
    env = two_teams()
    try:
        TeamClient(env, ALICE, "beta").refresh()
    except AccessDenied:
        pass
    else:
        raise AssertionError("alice is not a member of beta")


@scenario
def listing_connections_never_returns_keys() -> None:
    env = two_teams()
    listed = env.account.list_connections(ALICE, "team-alpha")
    assert listed and all(c.key is None for c in listed)
    assert env.account.get_connection(ALICE, "team-alpha", "ai-gateway-team-alpha", False).key is None


@scenario
def connection_carries_gateway_not_model_endpoint() -> None:
    env = two_teams()
    conn = env.account.get_connection(ALICE, "team-alpha", "ai-gateway-team-alpha", include_credentials=True)
    assert conn.target == GATEWAY_TARGET and MODEL_ENDPOINT not in conn.target
    assert conn.category == "ApiManagement" and conn.auth_type == "ApiKey" and not conn.is_shared_to_all
    assert conn.metadata == {"ResourceId": APIM_RESOURCE_ID, "type": "azure_api_management"}
    assert conn.key == env.gateway.list_secrets("sub-team-alpha")["primaryKey"]


@scenario
def model_account_rejects_key_auth() -> None:
    env = two_teams()
    key = env.gateway.list_secrets("sub-team-alpha")["primaryKey"]
    direct = env.backend.send(Request(f"{MODEL_ENDPOINT}/openai/deployments/chat/chat/completions",
                                      {"api-key": key}, chat_body("hi")))
    assert direct.status == 401 and direct.body["error"]["code"] == "AuthenticationTypeDisabled"


@scenario
def leaked_key_is_billed_to_its_owner_team() -> None:
    env = two_teams()
    beta_key = env.gateway.list_secrets("sub-team-beta")["primaryKey"]
    r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET), {"api-key": beta_key, "x-team": "alpha"},
                                   chat_body("hi")))
    assert r.status == 200
    assert {m.dimensions["Team"] for m in env.metrics.records} == {"team-beta"}
    assert "team-alpha" not in env.gateway.counters


@scenario
def key_rotation_revokes_old_key_and_client_recovers() -> None:
    env = two_teams()
    beta = TeamClient(env, BOB, "beta")
    assert beta.chat().status == 200
    old_key = env.gateway.list_secrets("sub-team-beta")["primaryKey"]
    env.gateway.regenerate_key("sub-team-beta", "primary")
    r = env.gateway.handle(Request(chat_url(GATEWAY_TARGET), {"api-key": old_key}, chat_body("hi")))
    assert r.status == 401
    deploy_team(env, TeamConfig("beta", 400, (BOB, OPS)))  # redeploy re-reads listSecrets()
    assert beta.chat().status == 200 and beta.reads == 2


@scenario
def responses_and_logs_never_contain_keys_or_prompts() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    secret_prompt = "Customer 4412 asked about the refund for order 7731."
    r = alpha.chat(prompt=secret_prompt)
    keys = [v for s in env.gateway.subscriptions.values() for v in (s.primary_key, s.secondary_key)]
    blob = json.dumps([r.headers, r.body, [vars(entry) for entry in env.gateway.logs],
                       [vars(m) for m in env.metrics.records]])
    assert not any(k in blob for k in keys)
    assert secret_prompt not in blob


@scenario
def chargeback_ledger_matches_consumed_headers() -> None:
    env = two_teams(alpha_tpm=5000, beta_tpm=5000)
    alpha, beta = TeamClient(env, ALICE, "alpha"), TeamClient(env, BOB, "beta")
    consumed = {"team-alpha": 0, "team-beta": 0}
    for _ in range(3):
        consumed["team-alpha"] += int(alpha.chat().header("x-tokens-consumed"))
    consumed["team-beta"] += int(beta.chat(prompt="Short one.").header("x-tokens-consumed"))
    report = chargeback(env.metrics.records)
    assert {t: v["tokens"] for t, v in report.items()} == consumed
    assert math.isclose(sum(v["share"] for v in report.values()), 1.0)
    assert report["team-alpha"]["share"] > report["team-beta"]["share"]


@scenario
def metric_dimensions_low_cardinality() -> None:
    env = two_teams()
    TeamClient(env, ALICE, "alpha").chat()
    TeamClient(env, BOB, "beta").chat()
    dims = {k for m in env.metrics.records for k in m.dimensions}
    assert len(dims) <= MAX_METRIC_DIMENSIONS
    assert dims == {"Team", "Subscription ID", "API ID", "Deployment"}
    assert {m.name for m in env.metrics.records} == set(METRIC_NAMES)


@scenario
def renaming_product_starts_a_fresh_counter() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    burst_until_throttled(alpha)
    env.gateway.products["team-alpha"].display_name = "Team Alpha"
    assert alpha.chat().status == 200, "a renamed product gets a new, full counter (see ADR 0001)"
    assert {"team-alpha", "Team Alpha"} <= set(env.gateway.counters)


@scenario
def onboarding_a_team_leaves_existing_budgets_alone() -> None:
    env = two_teams()
    alpha = TeamClient(env, ALICE, "alpha")
    alpha.chat()
    before = env.gateway.counters["team-alpha"].balance
    deploy_team(env, TeamConfig("gamma", 400, ("principal-carol",)))
    assert env.gateway.counters["team-alpha"].balance == before
    assert TeamClient(env, "principal-carol", "gamma").chat().status == 200
    assert "ai-gateway-team-gamma" in env.account.projects["team-gamma"].connections


@scenario
def config_fails_closed() -> None:
    bad = validate_config([TeamConfig("alpha", 0), TeamConfig("Beta Team", 100), TeamConfig("alpha", 100)], "")
    assert any("tokensPerMinute" in e for e in bad)
    assert any("lowercase" in e for e in bad)
    assert any("listed twice" in e for e in bad)
    assert any("role definition" in e for e in bad)
    try:
        build_environment([TeamConfig("alpha", 0)])
    except ValueError:
        pass
    else:
        raise AssertionError("a zero budget must not deploy")


@scenario
def production_stubs_raise() -> None:
    stubs = [
        lambda: ApimTokenLimitPolicy().check("team-alpha", 1),
        lambda: FoundryProjectConnections().get("team-alpha", "ai-gateway-team-alpha"),
        lambda: GatewayManagedIdentity().get_token(MI_RESOURCE),
        lambda: FoundryModelBackend().send(Request(GATEWAY_TARGET)),
        lambda: AppInsightsTokenMetricSink().emit(MetricRecord(METRIC_NAMESPACE, "Total Tokens", 1, {})),
    ]
    for stub in stubs:
        try:
            stub()
        except NotImplementedError:
            continue
        raise AssertionError("production stub did not raise")


def run_self_test() -> int:
    failures = 0
    for name, fn in SCENARIOS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report every failure and keep going
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    passed = len(SCENARIOS) - failures
    print(f"{passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def run_demo() -> int:
    env = two_teams(alpha_tpm=400, beta_tpm=400)
    alpha, beta = TeamClient(env, OPS, "alpha"), TeamClient(env, OPS, "beta")
    print("Deployed: products team-alpha and team-beta, one subscription each, projects team-alpha and")
    print("team-beta, connections ai-gateway-team-alpha and ai-gateway-team-beta. Budgets are illustrative.\n")

    print("1) Team alpha calls through its project connection.")
    r = alpha.chat()
    print(f"   status={r.status} x-tokens-consumed={r.header('x-tokens-consumed')} "
          f"x-remaining-tokens={r.header('x-remaining-tokens')}")
    sent = env.backend.received[-1]
    print(f"   backend saw: authorization={sent.headers['authorization'].split('|')[0]}|..., "
          f"api-key present={'api-key' in sent.headers}, x-team present={'x-team' in sent.headers}\n")

    print("2) Team alpha bursts until the gateway throttles it.")
    calls, r_alpha = burst_until_throttled(alpha)
    print(f"   throttled on call {calls}: status={r_alpha.status} retry-after={r_alpha.header('retry-after')} "
          f"kind={classify_429(r_alpha)}\n")

    print("3) Team beta calls while alpha is throttled.")
    r = beta.chat()
    print(f"   status={r.status}: beta draws on its own budget\n")

    print("4) Alpha waits for retry-after (logical seconds) and is served again.")
    env.clock.advance(int(r_alpha.header("retry-after")))
    print(f"   status={alpha.chat().status}\n")

    print("5) Beta's key is rotated; the next deployment updates the connection; the client re-reads it.")
    env.gateway.regenerate_key("sub-team-beta")
    deploy_team(env, TeamConfig("beta", 400, (BOB, OPS)))
    print(f"   status={beta.chat().status} connection reads={beta.reads}\n")

    print("6) Chargeback from the per-team token metric (shares only).")
    for team, row in chargeback(env.metrics.records).items():
        print(f"   {team:<10} share={row['share']:.0%}")
    return 0


# ---------------------------------------------------------------------------
# Production stubs: where the simulation stops and the deployable pieces begin
# ---------------------------------------------------------------------------

class ApimTokenLimitPolicy:
    """The per-team budget lives in API Management, not in application code."""

    def check(self, counter_key: str, estimate: int) -> None:
        raise NotImplementedError("Configure azure-openai-token-limit; see infra/policies/team-token-limit.xml")


class FoundryProjectConnections:
    """Read the team's gateway connection from its Foundry project."""

    def get(self, project: str, name: str) -> Connection:
        raise NotImplementedError("Use AIProjectClient.connections.get(..., include_credentials=True); "
                                  "see src/team_client.py")


class GatewayManagedIdentity:
    """The gateway's system-assigned identity calling the model account."""

    def get_token(self, resource: str) -> str:
        raise NotImplementedError("Use the authentication-managed-identity policy; "
                                  "see infra/policies/api-gateway-policy.xml")


class FoundryModelBackend:
    """The shared model deployment behind the gateway."""

    def send(self, request: Request) -> Response:
        raise NotImplementedError("Deployed by infra/modules/foundry-account.bicep; reached only through the gateway")


class AppInsightsTokenMetricSink:
    """Per-team token metric in Application Insights."""

    def emit(self, record: MetricRecord) -> None:
        raise NotImplementedError("Use azure-openai-emit-token-metric; see infra/policies/token-metric-fragment.xml")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true", help="run named scenarios and exit non-zero on failure")
    group.add_argument("--demo", action="store_true", help="narrated walkthrough")
    args = parser.parse_args(argv)
    return run_self_test() if args.self_test else run_demo()


if __name__ == "__main__":
    sys.exit(main())
