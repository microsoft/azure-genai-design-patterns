#!/usr/bin/env python3
"""MCP OAuth front door simulator (standard library only).

Simulates the gateway "front door" described in ../docs/architecture.md:

* a 401 challenge whose WWW-Authenticate header carries resource_metadata
* RFC 9728 protected resource metadata (PRM) for each MCP server
* an RFC 8414 authorization server metadata shim derived from an OIDC document
* an optional registration shim (RFC 7591 request shape) that hands out a
  shared, pre-registered public client ID from a governed allow-list
* authorize and token facades that map the RFC 8707 resource parameter to an
  IdP scope (api://{app-id}/{scope}) and pass PKCE values through untouched
* access token validation at the MCP endpoint (issuer, audience, tenant,
  scope, lifetime), then a hand-off that never forwards the client token

Every external system is an in-memory fake behind an adapter seam. The fake
identity provider is *modeled* on a scope-based enterprise IdP. It is not a
specification of any product: verify real behavior against current docs.

TEST ONLY: access tokens are HMAC-SHA256 (HS256) JWTs signed with a random
in-memory key generated on every run. Real deployments validate asymmetric
signatures against the keys the IdP publishes.

Usage:
    python oauth_front_door_sim.py --self-test   # scenarios, exit 0 or 1
    python oauth_front_door_sim.py --emit        # print the wire documents
    python oauth_front_door_sim.py --demo        # narrated end-to-end flow
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
from typing import Any, Callable, Protocol
from urllib.parse import parse_qsl, quote, unquote_plus, urlsplit

# ---------------------------------------------------------------------------
# Placeholder values. All hosts, tenants and IDs are fictitious.
# ---------------------------------------------------------------------------

GATEWAY_HOST = "mcp.contoso.example"
GATEWAY_BASE = f"https://{GATEWAY_HOST}"
FACADE_ISSUER = f"{GATEWAY_BASE}/auth"
DOCS_BASE = "https://docs.contoso.example/mcp"

IDP_HOST = "login.example.com"
TENANT_ID = "00000000-0000-0000-0000-000000000000"
IDP_AUTHORITY = f"https://{IDP_HOST}/{TENANT_ID}"
IDP_ISSUER = f"{IDP_AUTHORITY}/v2.0"
ROGUE_TENANT_ID = "55555555-5555-5555-5555-555555555555"

ORDERS_APP_ID = "11111111-1111-1111-1111-111111111111"
SHARED_PUBLIC_CLIENT_ID = "22222222-2222-2222-2222-222222222222"
KNOWLEDGE_APP_ID = "33333333-3333-3333-3333-333333333333"
USER_OBJECT_ID = "44444444-4444-4444-4444-444444444444"
UNKNOWN_CLIENT_ID = "99999999-9999-9999-9999-999999999999"

MCP_SCOPE = "mcp.tools"
LOOPBACK_REDIRECT_REGEX = r"^http://(127\.0\.0\.1|localhost)(:[0-9]+)?/callback$"
PKCE_CHALLENGE_REGEX = r"^[A-Za-z0-9_-]{43}$"
PKCE_VERIFIER_REGEX = r"^[A-Za-z0-9._~-]{43,128}$"

# Public PKCE test vector from RFC 7636, appendix B.
RFC7636_VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
RFC7636_CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"

EXAMPLE_REDIRECT_URI = "http://127.0.0.1:53682/callback"
EXAMPLE_STATE = "af0ifjsldkj"
FIXED_NOW = 1792454400
ACCESS_TOKEN_LIFETIME = 3600  # example value for the simulation only

STANDARD_SCOPES = ("openid", "profile", "email", "offline_access")


# ---------------------------------------------------------------------------
# Minimal HTTP model
# ---------------------------------------------------------------------------


def get_header(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower():
            return value
    return None


def without_header(headers: dict[str, str], name: str) -> dict[str, str]:
    return {k: v for k, v in headers.items() if k.lower() != name.lower()}


def first_values(query: str) -> dict[str, str]:
    """Parse a query string; the first occurrence of a parameter wins."""
    values: dict[str, str] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        values.setdefault(key, value)
    return values


def encode_pairs(pairs: list[tuple[str, str]]) -> str:
    """Percent-encode like Uri.EscapeDataString (space becomes %20)."""
    return "&".join(f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in pairs)


def parse_form(body: str) -> dict[str, str]:
    """Parse a form body the way the token facade policy does.

    A repeated key keeps its first position but takes the last value.
    """
    form: dict[str, str] = {}
    for pair in body.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        form[unquote_plus(key)] = unquote_plus(value)
    return form


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_challenge(verifier: str) -> str:
    """PKCE S256: BASE64URL(SHA256(ASCII(code_verifier)))."""
    return b64url(hashlib.sha256(verifier.encode("ascii")).digest())


@dataclass
class Request:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    client_ip: str = "203.0.113.10"

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc.lower()

    @property
    def path(self) -> str:
        return urlsplit(self.url).path or "/"

    @property
    def query(self) -> dict[str, str]:
        return first_values(urlsplit(self.url).query)


@dataclass
class Response:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""

    def header(self, name: str) -> str | None:
        return get_header(self.headers, name)

    def json(self) -> Any:
        return json.loads(self.body)


def json_response(status: int, payload: Any, headers: dict[str, str] | None = None) -> Response:
    merged = {"Content-Type": "application/json"}
    merged.update(headers or {})
    return Response(status, merged, json.dumps(payload, separators=(",", ":")))


class FakeClock:
    def __init__(self, now: int = FIXED_NOW) -> None:
        self.now = now

    def time(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


Handler = Callable[[Request], Response]


class Network:
    """Routes requests to in-memory hosts. Unknown hosts are unreachable."""

    def __init__(self) -> None:
        self._hosts: dict[str, Handler] = {}

    def register(self, host: str, handler: Handler) -> None:
        self._hosts[host.lower()] = handler

    def unregister(self, host: str) -> None:
        self._hosts.pop(host.lower(), None)

    def send(self, request: Request) -> Response:
        handler = self._hosts.get(request.host)
        if handler is None:
            raise ConnectionError(f"no route to host {request.host}")
        return handler(request)


# ---------------------------------------------------------------------------
# Configuration: one source for PRM, the metadata shim, facades and validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class McpServer:
    path: str
    app_id: str
    scope: str
    name: str
    docs_slug: str

    @property
    def idp_scope(self) -> str:
        return f"api://{self.app_id}/{self.scope}"


DEFAULT_SERVERS = (
    McpServer("/orders/mcp", ORDERS_APP_ID, MCP_SCOPE, "Contoso Orders MCP server", "orders"),
    McpServer("/knowledge/mcp", KNOWLEDGE_APP_ID, MCP_SCOPE, "Contoso Knowledge MCP server", "knowledge"),
)


@dataclass(frozen=True)
class FrontDoorConfig:
    variant: str
    gateway_base: str = GATEWAY_BASE
    facade_issuer: str = FACADE_ISSUER
    docs_base: str = DOCS_BASE
    idp_authority: str = IDP_AUTHORITY
    idp_issuer: str = IDP_ISSUER
    tenant_id: str = TENANT_ID
    servers: tuple[McpServer, ...] = DEFAULT_SERVERS
    redirect_regex: str = LOOPBACK_REDIRECT_REGEX
    clock_skew_seconds: int = 0
    metadata_shim_enabled: bool = False
    facades_enabled: bool = False
    registration_enabled: bool = False
    registration_rate_calls: int = 5  # test value, not guidance
    registration_rate_period_seconds: int = 60  # test value, not guidance
    metadata_cache_control: str = "public, max-age=3600"  # example value

    def resource_for(self, server: McpServer) -> str:
        return self.gateway_base + server.path

    def prm_url_for(self, server: McpServer) -> str:
        return f"{self.gateway_base}/.well-known/oauth-protected-resource{server.path}"

    def server_for_path(self, path: str) -> McpServer | None:
        wanted = path.rstrip("/").lower()
        for server in self.servers:
            if server.path.lower() == wanted:
                return server
        return None

    def client_scope_for(self, server: McpServer) -> str:
        """Short names when the facades map them; otherwise the IdP scope."""
        return server.scope if self.facades_enabled else server.idp_scope

    def resource_scope_map(self) -> dict[str, str]:
        return {self.resource_for(s): s.idp_scope for s in self.servers}

    def default_scope(self) -> str:
        return self.servers[0].idp_scope

    def authorization_server(self) -> str:
        return self.facade_issuer if self.metadata_shim_enabled else self.idp_issuer


VARIANT_FLAGS: dict[str, dict[str, bool]] = {
    "A": {"metadata_shim_enabled": False, "facades_enabled": False, "registration_enabled": False},
    "B": {"metadata_shim_enabled": True, "facades_enabled": False, "registration_enabled": False},
    "C": {"metadata_shim_enabled": True, "facades_enabled": True, "registration_enabled": False},
    "C+R": {"metadata_shim_enabled": True, "facades_enabled": True, "registration_enabled": True},
}


def config_for_variant(variant: str, **overrides: Any) -> FrontDoorConfig:
    if variant not in VARIANT_FLAGS:
        raise ValueError(f"unknown variant {variant!r}; Variant D is a separate broker app")
    settings: dict[str, Any] = dict(VARIANT_FLAGS[variant])
    settings.update(overrides)
    return FrontDoorConfig(variant=variant, **settings)


# ---------------------------------------------------------------------------
# Adapter seams
# ---------------------------------------------------------------------------


class IdentityProvider(Protocol):
    issuer: str

    def openid_configuration(self) -> dict[str, Any]: ...

    def signing_keys(self) -> dict[str, bytes]: ...


class ClientRegistry(Protocol):
    def allowed_client_ids(self) -> set[str]: ...

    def shared_public_client_id(self) -> str: ...

    def record_registration(self, client_name: str, redirect_uris: list[str], caller_ip: str) -> None: ...


class InMemoryClientRegistry:
    """Governed allow-list of pre-registered public clients."""

    def __init__(self, client_ids: set[str] | None = None, shared: str = SHARED_PUBLIC_CLIENT_ID) -> None:
        self._client_ids = set(client_ids or {shared})
        self._shared = shared
        self.registrations: list[dict[str, Any]] = []

    def allowed_client_ids(self) -> set[str]:
        return set(self._client_ids)

    def shared_public_client_id(self) -> str:
        return self._shared

    def record_registration(self, client_name: str, redirect_uris: list[str], caller_ip: str) -> None:
        self.registrations.append(
            {"client_name": client_name, "redirect_uris": list(redirect_uris), "caller_ip": caller_ip}
        )


class EntraIdentityProvider:
    """Production adapter stub. Gated: it never touches the network by default.

    A real implementation would fetch the tenant's OIDC document and signing
    keys over HTTPS, cache them, and refresh on key rotation.
    """

    def __init__(self, authority: str, *, enable_network: bool = False) -> None:
        if not enable_network:
            raise RuntimeError(
                "EntraIdentityProvider is a gated stub; pass enable_network=True "
                "and implement discovery and key retrieval for your tenant"
            )
        self.authority = authority
        self.issuer = f"{authority}/v2.0"

    def openid_configuration(self) -> dict[str, Any]:
        raise NotImplementedError("fetch {authority}/v2.0/.well-known/openid-configuration")

    def signing_keys(self) -> dict[str, bytes]:
        raise NotImplementedError("fetch and cache the keys from jwks_uri")


# ---------------------------------------------------------------------------
# Test-only JWT helpers
# ---------------------------------------------------------------------------


def sign_jwt(claims: dict[str, Any], kid: str, key: bytes) -> str:
    """TEST ONLY: HS256 with an in-memory key. Real IdPs sign asymmetrically."""
    header = {"alg": "HS256", "typ": "JWT", "kid": kid}
    signing_input = (
        b64url(json.dumps(header, separators=(",", ":")).encode("utf-8"))
        + "."
        + b64url(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
    )
    signature = hmac.new(key, signing_input.encode("ascii"), hashlib.sha256).digest()
    return signing_input + "." + b64url(signature)


def decode_jwt_unverified(token: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Decode header and payload for display or inspection. Never trust the result."""
    header_b64, payload_b64, _ = token.split(".")
    return json.loads(b64url_decode(header_b64)), json.loads(b64url_decode(payload_b64))


# ---------------------------------------------------------------------------
# Fake identity provider (modeled on a scope-based enterprise IdP; verify)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApiApp:
    app_id: str
    scopes: tuple[str, ...] = (MCP_SCOPE,)
    identifier_uris: tuple[str, ...] = ()

    def uris(self) -> tuple[str, ...]:
        return self.identifier_uris or (f"api://{self.app_id}",)


@dataclass(frozen=True)
class PublicClientApp:
    client_id: str
    redirect_uris: tuple[str, ...]


DEFAULT_APIS = (ApiApp(ORDERS_APP_ID), ApiApp(KNOWLEDGE_APP_ID))
DEFAULT_CLIENTS = (
    PublicClientApp(SHARED_PUBLIC_CLIENT_ID, ("http://127.0.0.1/callback", "http://localhost/callback")),
)


@dataclass
class _CodeGrant:
    client_id: str
    redirect_uri: str
    code_challenge: str
    api: ApiApp
    scopes: tuple[str, ...]
    offline: bool


@dataclass
class _RefreshGrant:
    client_id: str
    api: ApiApp
    scopes: tuple[str, ...]


def redirect_uri_matches(registered: str, requested: str) -> bool:
    """Exact match, except that a loopback port is ignored (modeled; verify)."""
    if registered == requested:
        return True
    reg, req = urlsplit(registered), urlsplit(requested)
    return (
        reg.scheme == req.scheme == "http"
        and reg.hostname in ("127.0.0.1", "localhost")
        and reg.hostname == req.hostname
        and reg.path == req.path
        and not req.query
        and not req.fragment
    )


class FakeIdentityProvider:
    """Entra-like behavior, modeled for the simulation. Verify against current docs.

    * Discovery: OIDC metadata only, at the issuer path plus
      /.well-known/openid-configuration. No code_challenge_methods_supported,
      no registration_endpoint.
    * Audience comes from the scope (api://{app-id}/{scope}). A resource
      parameter must name the same API, or the request fails.
    * Public clients must use PKCE with S256. Codes are single-use.
    """

    def __init__(
        self,
        clock: FakeClock,
        *,
        host: str = IDP_HOST,
        tenant_id: str = TENANT_ID,
        apis: tuple[ApiApp, ...] = DEFAULT_APIS,
        clients: tuple[PublicClientApp, ...] = DEFAULT_CLIENTS,
    ) -> None:
        self.clock = clock
        self.host = host
        self.tenant_id = tenant_id
        self.authority = f"https://{host}/{tenant_id}"
        self.issuer = f"{self.authority}/v2.0"
        self.apis = tuple(apis)
        self.clients = {c.client_id: c for c in clients}
        self._kid = "test-" + secrets.token_hex(4)
        self._key = secrets.token_bytes(32)
        self._codes: dict[str, _CodeGrant] = {}
        self._refresh: dict[str, _RefreshGrant] = {}
        self.authorize_requests: list[dict[str, str]] = []
        self.token_requests: list[Request] = []

    # -- IdentityProvider seam -------------------------------------------
    def openid_configuration(self) -> dict[str, Any]:
        return {
            "issuer": self.issuer,
            "authorization_endpoint": f"{self.authority}/oauth2/v2.0/authorize",
            "token_endpoint": f"{self.authority}/oauth2/v2.0/token",
            "jwks_uri": f"{self.authority}/discovery/v2.0/keys",
            "response_types_supported": ["code", "id_token", "code id_token", "id_token token"],
            "response_modes_supported": ["query", "fragment", "form_post"],
            "scopes_supported": list(STANDARD_SCOPES),
            "subject_types_supported": ["pairwise"],
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": [
                "client_secret_post",
                "private_key_jwt",
                "client_secret_basic",
            ],
        }

    def signing_keys(self) -> dict[str, bytes]:
        return {self._kid: self._key}

    # -- HTTP surface ----------------------------------------------------
    def handle(self, request: Request) -> Response:
        base = f"/{self.tenant_id}"
        if request.method == "GET" and request.path == f"{base}/v2.0/.well-known/openid-configuration":
            return json_response(200, self.openid_configuration())
        if request.method == "GET" and request.path == f"{base}/oauth2/v2.0/authorize":
            return self._authorize(request)
        if request.method == "POST" and request.path == f"{base}/oauth2/v2.0/token":
            return self._token(request)
        return json_response(404, {"error": "not_found"})

    def _api_for_scope(self, token: str) -> tuple[ApiApp | None, str]:
        for api in self.apis:
            for uri in api.uris():
                prefix = uri.rstrip("/") + "/"
                if token.startswith(prefix) and token[len(prefix):] in api.scopes:
                    return api, token[len(prefix):]
        return None, ""

    def _resolve_scopes(self, scope_param: str) -> tuple[ApiApp | None, tuple[str, ...], tuple[str, ...], str]:
        """Return (api, api scope names, standard scopes, error)."""
        hits: list[tuple[ApiApp, str]] = []
        standard: list[str] = []
        for token in scope_param.split():
            if token in STANDARD_SCOPES:
                standard.append(token)
                continue
            api, name = self._api_for_scope(token)
            if api is None:
                return None, (), tuple(standard), "invalid_scope"
            hits.append((api, name))
        if len({api.app_id for api, _ in hits}) != 1:
            return None, (), tuple(standard), "invalid_scope"
        return hits[0][0], tuple(name for _, name in hits), tuple(standard), ""

    @staticmethod
    def _redirect(uri: str, pairs: list[tuple[str, str]]) -> Response:
        query = encode_pairs([(k, v) for k, v in pairs if v])
        return Response(302, {"Location": uri + ("&" if "?" in uri else "?") + query})

    def _authorize(self, request: Request) -> Response:
        q = request.query
        self.authorize_requests.append(dict(q))
        client = self.clients.get(q.get("client_id", ""))
        redirect_uri = q.get("redirect_uri", "")
        if client is None:
            return Response(400, {"Content-Type": "text/plain"}, "Sign-in error: the application was not found.")
        if not any(redirect_uri_matches(r, redirect_uri) for r in client.redirect_uris):
            return Response(400, {"Content-Type": "text/plain"}, "Sign-in error: the redirect URI does not match.")
        state = q.get("state", "")

        def fail(error: str, description: str) -> Response:
            return self._redirect(redirect_uri, [("error", error), ("error_description", description), ("state", state)])

        if q.get("response_type") != "code":
            return fail("unsupported_response_type", "Only the code response type is supported.")
        api, names, standard, error = self._resolve_scopes(q.get("scope", ""))
        if error or api is None:
            return fail("invalid_scope", "The requested scope is not valid for this application.")
        resource = q.get("resource", "")
        if resource and resource not in api.uris():
            return fail("invalid_target", "The resource parameter does not match the requested scopes.")
        challenge = q.get("code_challenge", "")
        if q.get("code_challenge_method") != "S256" or not re.fullmatch(PKCE_CHALLENGE_REGEX, challenge):
            return fail("invalid_request", "PKCE with S256 is required for this client.")
        code = secrets.token_urlsafe(24)
        self._codes[code] = _CodeGrant(
            client.client_id, redirect_uri, challenge, api, names, "offline_access" in standard
        )
        return self._redirect(redirect_uri, [("code", code), ("state", state)])

    @staticmethod
    def _error(error: str, description: str) -> Response:
        return json_response(
            400,
            {"error": error, "error_description": description},
            {"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    def _token(self, request: Request) -> Response:
        self.token_requests.append(request)
        form = parse_form(request.body)
        client = self.clients.get(form.get("client_id", ""))
        if client is None:
            return self._error("invalid_client", "The client application was not found.")
        grant = form.get("grant_type", "")
        if grant == "authorization_code":
            record = self._codes.pop(form.get("code", ""), None)  # single use, even on failure
            if record is None:
                return self._error("invalid_grant", "The authorization code is invalid or was already redeemed.")
            if record.client_id != client.client_id or record.redirect_uri != form.get("redirect_uri", ""):
                return self._error("invalid_grant", "The code was issued to another client or redirect URI.")
            verifier = form.get("code_verifier", "")
            if not re.fullmatch(PKCE_VERIFIER_REGEX, verifier) or not hmac.compare_digest(
                make_challenge(verifier), record.code_challenge
            ):
                return self._error("invalid_grant", "The code_verifier does not match the code_challenge.")
            api, names = record.api, record.scopes
            if form.get("scope"):
                scope_api, _, _, error = self._resolve_scopes(form["scope"])
                if error or scope_api is None or scope_api.app_id != api.app_id:
                    return self._error("invalid_grant", "The requested scope does not match the authorization.")
            resource = form.get("resource", "")
            if resource and resource not in api.uris():
                return self._error("invalid_target", "The resource parameter does not match the requested scopes.")
            return self._issue(client.client_id, api, names, record.offline)
        if grant == "refresh_token":
            record_rt = self._refresh.get(form.get("refresh_token", ""))
            if record_rt is None or record_rt.client_id != client.client_id:
                return self._error("invalid_grant", "The refresh token is invalid.")
            api, names = record_rt.api, record_rt.scopes
            if form.get("scope"):
                scope_api, scope_names, _, error = self._resolve_scopes(form["scope"])
                if error or scope_api is None:
                    return self._error("invalid_scope", "The requested scope is not valid for this application.")
                api, names = scope_api, scope_names
            resource = form.get("resource", "")
            if resource and resource not in api.uris():
                return self._error("invalid_target", "The resource parameter does not match the requested scopes.")
            return self._issue(client.client_id, api, names, True)
        return self._error("unsupported_grant_type", "The grant type is not supported.")

    def _issue(self, client_id: str, api: ApiApp, names: tuple[str, ...], offline: bool) -> Response:
        body: dict[str, Any] = {
            "token_type": "Bearer",
            "scope": " ".join(f"{api.uris()[0]}/{name}" for name in names),
            "expires_in": ACCESS_TOKEN_LIFETIME,
            "access_token": self.mint_access_token(aud=api.app_id, azp=client_id, scp=" ".join(names)),
        }
        if offline:
            refresh_token = secrets.token_urlsafe(32)
            self._refresh[refresh_token] = _RefreshGrant(client_id, api, names)
            body["refresh_token"] = refresh_token
        return json_response(200, body, {"Cache-Control": "no-store", "Pragma": "no-cache"})

    def mint_access_token(
        self,
        *,
        aud: str = ORDERS_APP_ID,
        azp: str = SHARED_PUBLIC_CLIENT_ID,
        scp: str = MCP_SCOPE,
        oid: str = USER_OBJECT_ID,
        issuer: str | None = None,
        tid: str | None = None,
        kid: str | None = None,
        key: bytes | None = None,
        lifetime: int = ACCESS_TOKEN_LIFETIME,
    ) -> str:
        """Test helper. Overrides let scenarios forge wrong-issuer or wrong-key tokens."""
        now = self.clock.time()
        claims = {
            "aud": aud,
            "iss": issuer or self.issuer,
            "tid": tid or self.tenant_id,
            "azp": azp,
            "scp": scp,
            "oid": oid,
            "sub": b64url(hmac.new(self._key, f"{oid}|{azp}".encode("utf-8"), hashlib.sha256).digest()[:16]),
            "ver": "2.0",
            "iat": now,
            "nbf": now,
            "exp": now + lifetime,
        }
        return sign_jwt(claims, kid or self._kid, key or self._key)


# ---------------------------------------------------------------------------
# Front door: document builders (pure functions, reused by --emit)
# ---------------------------------------------------------------------------


def build_prm(config: FrontDoorConfig, server: McpServer) -> dict[str, Any]:
    """RFC 9728 protected resource metadata for one MCP server."""
    return {
        "resource": config.resource_for(server),
        "authorization_servers": [config.authorization_server()],
        "scopes_supported": [config.client_scope_for(server)],
        "bearer_methods_supported": ["header"],
        "resource_name": server.name,
        "resource_documentation": f"{config.docs_base}/{server.docs_slug}",
    }


def derive_as_metadata(oidc: dict[str, Any], config: FrontDoorConfig) -> dict[str, Any]:
    """RFC 8414 shim derived from the IdP's OIDC document.

    The shim names the facade issuer, advertises S256 (which the IdP enforces
    for public clients but may not advertise), and points at the facades when
    they are enabled. It deliberately omits jwks_uri: clients never validate
    access tokens, and the shim issuer signs nothing.
    """
    if oidc.get("issuer") != config.idp_issuer:
        raise ValueError("OIDC issuer does not match the configured IdP issuer")
    if config.facades_enabled:
        authorization_endpoint = f"{config.facade_issuer}/authorize"
        token_endpoint = f"{config.facade_issuer}/token"
    else:
        authorization_endpoint = oidc["authorization_endpoint"]
        token_endpoint = oidc["token_endpoint"]
    doc: dict[str, Any] = {
        "issuer": config.facade_issuer,
        "authorization_endpoint": authorization_endpoint,
        "token_endpoint": token_endpoint,
    }
    if config.registration_enabled:
        doc["registration_endpoint"] = f"{config.facade_issuer}/register"
    scopes: list[str] = []
    for server in config.servers:
        scope = config.client_scope_for(server)
        if scope not in scopes:
            scopes.append(scope)
    scopes.append("offline_access")
    doc.update(
        {
            "response_types_supported": ["code"],
            "response_modes_supported": ["query"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
            "scopes_supported": scopes,
            "service_documentation": f"{config.docs_base}/auth",
        }
    )
    return doc


def map_resource_to_scope(config: FrontDoorConfig, resource: str, scope: str) -> str | None:
    """RFC 8707 resource -> IdP scope. Mirrors the facade policies.

    A non-empty resource must be in the map. Otherwise the first scope token
    that is already a mapped IdP scope wins, then the configured default.
    """
    mapping = config.resource_scope_map()
    if resource:
        return mapping.get(resource)
    values = set(mapping.values())
    for token in scope.split(" "):
        if token in values:
            return token
    return config.default_scope()


def validate_access_token(
    token: str, server: McpServer, config: FrontDoorConfig, keys: dict[str, bytes], now: int
) -> tuple[dict[str, Any] | None, str]:
    """Return (claims, "") or (None, "invalid_token" | "insufficient_scope")."""
    parts = token.split(".")
    if len(parts) != 3:
        return None, "invalid_token"
    try:
        header, claims = decode_jwt_unverified(token)
    except (ValueError, TypeError):
        return None, "invalid_token"
    if not isinstance(header, dict) or not isinstance(claims, dict):
        return None, "invalid_token"
    key = keys.get(str(header.get("kid", "")))
    if header.get("alg") != "HS256" or key is None:
        return None, "invalid_token"
    expected = hmac.new(key, f"{parts[0]}.{parts[1]}".encode("ascii"), hashlib.sha256).digest()
    try:
        signature = b64url_decode(parts[2])
    except ValueError:
        return None, "invalid_token"
    if not hmac.compare_digest(expected, signature):
        return None, "invalid_token"
    if claims.get("iss") != config.idp_issuer:
        return None, "invalid_token"
    if claims.get("aud") not in (server.app_id, f"api://{server.app_id}"):
        return None, "invalid_token"
    if claims.get("tid") != config.tenant_id:
        return None, "invalid_token"
    skew = config.clock_skew_seconds
    exp, nbf = claims.get("exp"), claims.get("nbf", 0)
    if not isinstance(exp, int) or exp <= now - skew:
        return None, "invalid_token"
    if not isinstance(nbf, int) or nbf > now + skew:
        return None, "invalid_token"
    if server.scope not in str(claims.get("scp", "")).split(" "):
        return None, "insufficient_scope"
    return claims, ""


class FakeBackend:
    """Stands in for the tenant pipeline and MCP server behind the gateway."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def handle(self, server: McpServer, request: Request, context: dict[str, Any]) -> Response:
        self.calls.append({"server": server.path, "headers": dict(request.headers), "context": dict(context)})
        try:
            rpc = json.loads(request.body) if request.body else {}
        except ValueError:
            rpc = {}
        rpc_id = rpc.get("id") if isinstance(rpc, dict) else None
        result = {"server": server.name, "tenant": context["tenant_id"]}
        return json_response(200, {"jsonrpc": "2.0", "id": rpc_id, "result": result})


# ---------------------------------------------------------------------------
# Front door: the gateway surface (one handler per policy file)
# ---------------------------------------------------------------------------

PRM_PREFIX = "/.well-known/oauth-protected-resource"
AS_METADATA_PATH = "/.well-known/oauth-authorization-server/auth"
NO_STORE = {"Cache-Control": "no-store"}


class OAuthFrontDoor:
    def __init__(
        self,
        config: FrontDoorConfig,
        *,
        idp: IdentityProvider,
        registry: ClientRegistry,
        network: Network,
        clock: FakeClock,
        backend: FakeBackend | None = None,
    ) -> None:
        self.config = config
        self.idp = idp
        self.registry = registry
        self.network = network
        self.clock = clock
        self.backend = backend or FakeBackend()
        self.trace: list[dict[str, Any]] = []  # route, status, reason; never tokens or codes
        self._reason = ""
        self._registration_hits: dict[str, list[int]] = {}

    def handle(self, request: Request) -> Response:
        self._reason = ""
        route, response = self._dispatch(request)
        self.trace.append({"route": route, "status": response.status, "reason": self._reason})
        return response

    def _dispatch(self, request: Request) -> tuple[str, Response]:
        path, method = request.path, request.method.upper()
        config = self.config
        if path.startswith(PRM_PREFIX) and method == "GET":
            return "prm", self.prm(request)
        if path == AS_METADATA_PATH and method == "GET" and config.metadata_shim_enabled:
            return "as_metadata", self.as_metadata()
        if path == "/auth/register" and method == "POST":
            return "register", self.register(request)
        if path == "/auth/authorize" and method == "GET" and config.facades_enabled:
            return "authorize", self.authorize(request)
        if path == "/auth/token" and method == "POST" and config.facades_enabled:
            return "token", self.token(request)
        server = config.server_for_path(path)
        if server is not None:
            return "mcp", self.handle_mcp(server, request)
        return "not_found", json_response(404, {"error": "not_found"})

    # -- prm.xml ------------------------------------------------------------
    def prm(self, request: Request) -> Response:
        suffix = request.path[len(PRM_PREFIX):]
        server = self.config.server_for_path(suffix) if suffix.strip("/") else None
        if server is None:
            self._reason = "unknown_resource"
            return json_response(404, {"error": "not_found"}, {"Access-Control-Allow-Origin": "*"})
        return json_response(
            200,
            build_prm(self.config, server),
            {"Cache-Control": self.config.metadata_cache_control, "Access-Control-Allow-Origin": "*"},
        )

    # -- as-metadata-shim.xml ---------------------------------------------
    def as_metadata(self) -> Response:
        doc = derive_as_metadata(self.idp.openid_configuration(), self.config)
        return json_response(
            200, doc, {"Cache-Control": self.config.metadata_cache_control, "Access-Control-Allow-Origin": "*"}
        )

    # -- register-shim.xml --------------------------------------------------
    def _rate_limited(self, caller_ip: str) -> Response | None:
        now, period = self.clock.time(), self.config.registration_rate_period_seconds
        hits = [t for t in self._registration_hits.get(caller_ip, []) if t > now - period]
        if len(hits) >= self.config.registration_rate_calls:
            self._registration_hits[caller_ip] = hits
            retry_after = max(1, hits[0] + period - now)
            return json_response(
                429,
                {"statusCode": 429, "message": "Rate limit is exceeded."},
                {"Retry-After": str(retry_after)},
            )
        hits.append(now)
        self._registration_hits[caller_ip] = hits
        return None

    def _registration_error(self, error: str, description: str) -> Response:
        self._reason = error
        return json_response(400, {"error": error, "error_description": description}, NO_STORE)

    def register(self, request: Request) -> Response:
        config = self.config
        if not config.registration_enabled:
            self._reason = "registration_disabled"
            return Response(404)
        limited = self._rate_limited(request.client_ip)
        if limited is not None:
            self._reason = "rate_limited"
            return limited
        try:
            body = json.loads(request.body)
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return self._registration_error(
                "invalid_client_metadata", "The registration request could not be processed."
            )
        uris = body.get("redirect_uris")
        if (
            not isinstance(uris, list)
            or not uris
            or not all(isinstance(u, str) and re.fullmatch(config.redirect_regex, u) for u in uris)
        ):
            return self._registration_error("invalid_redirect_uri", "Redirect URI is not allowed.")
        method = body.get("token_endpoint_auth_method", "none")
        grants = body.get("grant_types")
        grants_checked = grants if isinstance(grants, list) else ["authorization_code"]
        responses = body.get("response_types")
        responses_checked = responses if isinstance(responses, list) else ["code"]
        name = body.get("client_name")
        if name is None:
            name = "MCP client"
        if (
            method != "none"
            or any(g not in ("authorization_code", "refresh_token") for g in grants_checked)
            or any(r != "code" for r in responses_checked)
            or not isinstance(name, str)
            or len(name) > 100
        ):
            return self._registration_error("invalid_client_metadata", "Client metadata is not allowed.")
        self.registry.record_registration(name, uris, request.client_ip)
        self._reason = "accepted"
        return json_response(
            201,
            {
                "client_id": self.registry.shared_public_client_id(),
                "client_id_issued_at": self.clock.time(),
                "client_name": name,
                "redirect_uris": uris,
                "grant_types": grants if isinstance(grants, list) else ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            },
            NO_STORE,
        )

    # -- authorize-facade.xml ----------------------------------------------
    def authorize(self, request: Request) -> Response:
        """Validate, map resource -> scope, and redirect. Never sees credentials."""
        config, q = self.config, request.query
        reason = ""
        if q.get("client_id", "") not in self.registry.allowed_client_ids():
            reason = "unknown_client"
        elif not re.fullmatch(config.redirect_regex, q.get("redirect_uri", "")):
            reason = "redirect_uri_not_allowed"
        elif q.get("response_type") != "code":
            reason = "unsupported_response_type"
        elif q.get("code_challenge_method") != "S256":
            reason = "pkce_s256_required"
        elif not re.fullmatch(PKCE_CHALLENGE_REGEX, q.get("code_challenge", "")):
            reason = "pkce_challenge_invalid"
        if reason:
            # Never redirect to an unvalidated redirect_uri: that is an open redirect.
            self._reason = reason
            return Response(
                400,
                {"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store"},
                f"The sign-in request was rejected ({reason}). Contact your administrator.",
            )
        scope = map_resource_to_scope(config, q.get("resource", ""), q.get("scope", ""))
        if scope is None:
            self._reason = "invalid_target"
            return json_response(
                400,
                {"error": "invalid_target", "error_description": "The requested resource is not served by this gateway."},
                NO_STORE,
            )
        pairs = [
            ("client_id", q["client_id"]),
            ("response_type", "code"),
            ("redirect_uri", q["redirect_uri"]),
            ("scope", scope + " offline_access"),
        ]
        for name in ("state", "code_challenge", "code_challenge_method", "prompt", "login_hint"):
            if q.get(name):
                pairs.append((name, q[name]))
        location = f"{config.idp_authority}/oauth2/v2.0/authorize?" + encode_pairs(pairs)
        self._reason = "redirected"
        return Response(302, {"Cache-Control": "no-store", "Location": location})

    # -- token-facade.xml ---------------------------------------------------
    def token(self, request: Request) -> Response:
        config = self.config
        form = parse_form(request.body)
        grant = form.get("grant_type", "")
        error = ""
        if grant not in ("authorization_code", "refresh_token"):
            error = "unsupported_grant_type"
        elif form.get("client_id", "") not in self.registry.allowed_client_ids():
            error = "unauthorized_client"
        elif grant == "authorization_code" and (
            not form.get("code")
            or not re.fullmatch(PKCE_VERIFIER_REGEX, form.get("code_verifier", ""))
            or not re.fullmatch(config.redirect_regex, form.get("redirect_uri", ""))
        ):
            error = "invalid_request"
        elif grant == "refresh_token" and not form.get("refresh_token"):
            error = "invalid_request"
        if error:
            self._reason = error
            return json_response(
                400,
                {"error": error, "error_description": "The token request was rejected by the gateway."},
                {"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        scope = map_resource_to_scope(config, form.get("resource", ""), form.get("scope", ""))
        if scope is None:
            self._reason = "invalid_target"
            return json_response(
                400,
                {"error": "invalid_target", "error_description": "The requested resource is not served by this gateway."},
                NO_STORE,
            )
        pairs = [(k, v) for k, v in form.items() if k not in ("resource", "scope")]
        pairs.append(("scope", scope + " offline_access"))
        headers = without_header(without_header(request.headers, "Cookie"), "Content-Type")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        upstream = Request(
            "POST",
            f"{config.idp_authority}/oauth2/v2.0/token",
            headers,
            encode_pairs(pairs),
            client_ip=request.client_ip,
        )
        try:
            response = self.network.send(upstream)
        except ConnectionError:
            self._reason = "idp_unreachable"
            return json_response(
                502,
                {"error": "temporarily_unavailable", "error_description": "The identity provider could not be reached."},
                NO_STORE,
            )
        headers_out = without_header(without_header(response.headers, "Cache-Control"), "Pragma")
        headers_out.update({"Cache-Control": "no-store", "Pragma": "no-cache"})
        self._reason = f"forwarded:{grant}"
        return Response(response.status, headers_out, response.body)

    # -- mcp-challenge-and-validate.xml ------------------------------------
    def handle_mcp(self, server: McpServer, request: Request) -> Response:
        config = self.config
        prm_url = config.prm_url_for(server)
        auth = get_header(request.headers, "Authorization") or ""
        if not auth.lower().startswith("bearer "):
            self._reason = "challenge"
            return json_response(
                401,
                {"message": "Authentication required."},
                {
                    "WWW-Authenticate": f'Bearer resource_metadata="{prm_url}", scope="{config.client_scope_for(server)}"',
                    "Access-Control-Expose-Headers": "WWW-Authenticate",
                    "Cache-Control": "no-store",
                },
            )
        claims, error = validate_access_token(
            auth[7:].strip(), server, config, self.idp.signing_keys(), self.clock.time()
        )
        if error == "insufficient_scope":
            self._reason = error
            return Response(
                403,
                {
                    "WWW-Authenticate": (
                        f'Bearer error="insufficient_scope", scope="{server.scope}", '
                        f'resource_metadata="{prm_url}"'
                    ),
                    "Access-Control-Expose-Headers": "WWW-Authenticate",
                    "Cache-Control": "no-store",
                },
            )
        if claims is None:
            self._reason = "invalid_token"
            return Response(
                401,
                {
                    "WWW-Authenticate": (
                        'Bearer error="invalid_token", '
                        'error_description="The access token is not valid for this resource.", '
                        f'resource_metadata="{prm_url}"'
                    ),
                    "Access-Control-Expose-Headers": "WWW-Authenticate",
                    "Cache-Control": "no-store",
                },
            )
        context = {
            "tenant_id": claims["tid"],
            "user_object_id": claims.get("oid", ""),
            "client_id": claims.get("azp", ""),
            "scopes": str(claims.get("scp", "")).split(" "),
        }
        # Token passthrough is forbidden: the client token stops here.
        forwarded = Request(
            request.method,
            request.url,
            without_header(request.headers, "Authorization"),
            request.body,
            request.client_ip,
        )
        self._reason = "validated"
        return self.backend.handle(server, forwarded, context)


# ---------------------------------------------------------------------------
# Client side: discovery and document checks
# ---------------------------------------------------------------------------

INITIALIZE_BODY = json.dumps(
    {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "<version>",
            "capabilities": {},
            "clientInfo": {"name": "example-client", "version": "1.0.0"},
        },
    },
    separators=(",", ":"),
)
MCP_REQUEST_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
FORM_HEADERS = {"Content-Type": "application/x-www-form-urlencoded"}


def parse_www_authenticate(header: str) -> tuple[str, dict[str, str]]:
    """Split a challenge into its scheme and auth-params (quoted or token values)."""
    scheme, _, rest = header.strip().partition(" ")
    params: dict[str, str] = {}
    pattern = r'([A-Za-z0-9_-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^\s,]+))'
    for key, quoted, token in re.findall(pattern, rest):
        params[key.lower()] = token if token and not quoted else quoted
    return scheme, params


def prm_url_from_resource(resource: str) -> str:
    """RFC 9728 fallback: insert the well-known segment between host and path."""
    parts = urlsplit(resource)
    return f"{parts.scheme}://{parts.netloc}{PRM_PREFIX}{parts.path.rstrip('/')}"


def discovery_urls(issuer: str) -> list[str]:
    """Metadata URLs a spec-current client tries for an issuer, in order.

    RFC 8414 inserts the well-known segment before the issuer path. OpenID
    Connect discovery appends it after the path. For an issuer with a path,
    clients commonly try both insertion styles and then the appended form.
    """
    parts = urlsplit(issuer)
    origin = f"{parts.scheme}://{parts.netloc}"
    path = parts.path.rstrip("/")
    if not path:
        return [f"{origin}/.well-known/oauth-authorization-server", f"{origin}/.well-known/openid-configuration"]
    return [
        f"{origin}/.well-known/oauth-authorization-server{path}",
        f"{origin}/.well-known/openid-configuration{path}",
        f"{origin}{path}/.well-known/openid-configuration",
    ]


def _is_https(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parts = urlsplit(value)
    return parts.scheme == "https" and bool(parts.netloc)


def validate_prm(doc: Any, expected_resource: str) -> list[str]:
    """Checks a client (or a release gate) applies to a PRM document."""
    if not isinstance(doc, dict):
        return ["PRM is not a JSON object"]
    errors: list[str] = []
    if doc.get("resource") != expected_resource:
        errors.append("resource does not match the protected resource URL")
    servers = doc.get("authorization_servers")
    if not isinstance(servers, list) or not servers or not all(_is_https(s) for s in servers):
        errors.append("authorization_servers must be a non-empty list of HTTPS URLs")
    scopes = doc.get("scopes_supported")
    if scopes is not None and (not isinstance(scopes, list) or not all(isinstance(s, str) and s for s in scopes)):
        errors.append("scopes_supported must be a list of non-empty strings")
    methods = doc.get("bearer_methods_supported")
    if methods is not None and (not isinstance(methods, list) or "header" not in methods):
        errors.append("bearer_methods_supported must include header")
    return errors


def validate_as_metadata(
    doc: Any, expected_issuer: str, *, require_s256: bool = True, require_registration: bool = False
) -> list[str]:
    """Checks a client (or a release gate) applies to authorization server metadata."""
    if not isinstance(doc, dict):
        return ["metadata is not a JSON object"]
    errors: list[str] = []
    if doc.get("issuer") != expected_issuer:
        errors.append("issuer does not match the authorization server URL")
    for name in ("authorization_endpoint", "token_endpoint"):
        if not _is_https(doc.get(name)):
            errors.append(f"{name} must be an HTTPS URL")
    response_types = doc.get("response_types_supported")
    if not isinstance(response_types, list) or "code" not in response_types:
        errors.append("response_types_supported must include code")
    methods = doc.get("code_challenge_methods_supported")
    methods = methods if isinstance(methods, list) else []
    if require_s256 and "S256" not in methods:
        errors.append("code_challenge_methods_supported does not include S256")
    if "plain" in methods:
        errors.append("code_challenge_methods_supported advertises plain (front door policy forbids it)")
    if require_registration and not _is_https(doc.get("registration_endpoint")):
        errors.append("registration_endpoint must be an HTTPS URL")
    return errors


# ---------------------------------------------------------------------------
# Client side: a public MCP client walking the whole flow
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClientProfile:
    """Generic client behaviors (client types in architecture section 12).

    These are illustrative types, not models of any named product.
    """

    label: str
    sends_resource: bool = True
    requires_s256_advertised: bool = True
    discovery: str = "all"  # "all" or "first-only"
    client_id: str | None = SHARED_PUBLIC_CLIENT_ID  # None: must register first
    redirect_uri: str = EXAMPLE_REDIRECT_URI
    client_name: str = "Example MCP Client"


SPEC_CURRENT_CLIENT = ClientProfile("Client type A (spec-current, pre-registered)")
REGISTRATION_DEPENDENT_CLIENT = ClientProfile("Client type B (registration-dependent)", client_id=None)
OLDER_DISCOVERY_CLIENT = ClientProfile(
    "Client type C (older discovery)",
    sends_resource=False,
    requires_s256_advertised=False,
    discovery="first-only",
)
LENIENT_CLIENT = ClientProfile(
    "Lenient test client (no resource, tolerant discovery)",
    sends_resource=False,
    requires_s256_advertised=False,
)


@dataclass
class FlowResult:
    ok: bool = False
    failed_step: str = ""
    error: str = ""
    log: list[str] = field(default_factory=list)  # method, URL without query, status
    client_id: str = ""
    resource: str = ""
    access_token: str = ""
    refresh_token: str = ""
    token_response: dict[str, Any] = field(default_factory=dict)
    prm: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    final: Response | None = None


class _FlowStop(Exception):
    def __init__(self, step: str, error: str) -> None:
        super().__init__(f"{step}: {error}")
        self.step = step
        self.error = error


def error_of(response: Response) -> str:
    """The OAuth error code from a JSON body, or the HTTP status."""
    try:
        payload = response.json()
    except ValueError:
        return f"HTTP {response.status}"
    if isinstance(payload, dict) and payload.get("error"):
        return str(payload["error"])
    return f"HTTP {response.status}"


class McpClientSim:
    """Challenge, PRM, discovery, optional registration, authorization code
    with PKCE S256, token exchange, then the MCP call. The browser hop to the
    IdP is followed automatically (the fake IdP signs the user in)."""

    def __init__(
        self,
        network: Network,
        profile: ClientProfile,
        *,
        verifier: str = RFC7636_VERIFIER,
        state: str = EXAMPLE_STATE,
    ) -> None:
        self.network = network
        self.profile = profile
        self.verifier = verifier
        self.state = state
        self._step = ""

    def _send(self, request: Request, log: list[str]) -> Response:
        parts = urlsplit(request.url)
        response = self.network.send(request)
        # Queries are dropped: they can carry codes and state.
        log.append(f"{request.method} {parts.scheme}://{parts.netloc}{parts.path} -> {response.status}")
        return response

    def connect(self, mcp_url: str) -> FlowResult:
        result = FlowResult(resource=mcp_url)
        try:
            self._run(mcp_url, result)
        except _FlowStop as stop:
            result.failed_step, result.error = stop.step, stop.error
        except ConnectionError:
            result.failed_step, result.error = self._step, "unreachable"
        return result

    def _run(self, mcp_url: str, result: FlowResult) -> None:
        profile, log = self.profile, result.log

        self._step = "challenge"
        first = self._send(Request("POST", mcp_url, dict(MCP_REQUEST_HEADERS), INITIALIZE_BODY), log)
        if first.status != 401:
            raise _FlowStop("challenge", f"expected 401, got {first.status}")
        scheme, params = parse_www_authenticate(first.header("WWW-Authenticate") or "")
        if scheme.lower() != "bearer":
            raise _FlowStop("challenge", "no Bearer challenge")
        prm_url = params.get("resource_metadata") or prm_url_from_resource(mcp_url)

        self._step = "prm"
        prm_response = self._send(Request("GET", prm_url), log)
        if prm_response.status != 200:
            raise _FlowStop("prm", f"HTTP {prm_response.status}")
        prm = prm_response.json()
        errors = validate_prm(prm, mcp_url)
        if errors:
            raise _FlowStop("prm", "; ".join(errors))
        result.prm = prm
        issuer = prm["authorization_servers"][0]

        self._step = "discovery"
        urls = discovery_urls(issuer)
        if profile.discovery == "first-only":
            urls = urls[:1]
        metadata: dict[str, Any] | None = None
        for url in urls:
            response = self._send(Request("GET", url), log)
            if response.status != 200:
                continue
            doc = response.json()
            if isinstance(doc, dict) and doc.get("issuer") == issuer:
                metadata = doc
                break
        if metadata is None:
            raise _FlowStop("discovery", "no authorization server metadata found")
        errors = validate_as_metadata(metadata, issuer, require_s256=profile.requires_s256_advertised)
        if errors:
            raise _FlowStop("discovery", "; ".join(errors))
        result.metadata = metadata

        self._step = "registration"
        client_id = profile.client_id
        if client_id is None:
            endpoint = metadata.get("registration_endpoint")
            if not endpoint:
                raise _FlowStop("registration", "no registration_endpoint advertised")
            body = {
                "client_name": profile.client_name,
                "redirect_uris": [profile.redirect_uri],
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
                "token_endpoint_auth_method": "none",
            }
            response = self._send(
                Request("POST", endpoint, {"Content-Type": "application/json"}, json.dumps(body)), log
            )
            if response.status != 201:
                raise _FlowStop("registration", error_of(response))
            client_id = str(response.json()["client_id"])
        result.client_id = client_id

        self._step = "authorize"
        scopes = prm.get("scopes_supported") or [""]
        pairs = [
            ("response_type", "code"),
            ("client_id", client_id),
            ("redirect_uri", profile.redirect_uri),
            ("scope", scopes[0]),
            ("state", self.state),
            ("code_challenge", make_challenge(self.verifier)),
            ("code_challenge_method", "S256"),
        ]
        if profile.sends_resource:
            pairs.append(("resource", mcp_url))
        response = self._send(Request("GET", metadata["authorization_endpoint"] + "?" + encode_pairs(pairs)), log)
        callback_host = urlsplit(profile.redirect_uri).netloc
        for _ in range(5):
            location = response.header("Location") if response.status == 302 else None
            if not location or urlsplit(location).netloc == callback_host:
                break
            response = self._send(Request("GET", location), log)
        location = response.header("Location") if response.status == 302 else None
        if not location or urlsplit(location).netloc != callback_host:
            raise _FlowStop("authorize", error_of(response))
        callback = first_values(urlsplit(location).query)
        if "error" in callback:
            raise _FlowStop("authorize", callback["error"])
        if callback.get("state") != self.state:
            raise _FlowStop("authorize", "state mismatch")

        self._step = "token"
        form = [
            ("grant_type", "authorization_code"),
            ("code", callback.get("code", "")),
            ("redirect_uri", profile.redirect_uri),
            ("client_id", client_id),
            ("code_verifier", self.verifier),
        ]
        if profile.sends_resource:
            form.append(("resource", mcp_url))
        response = self._send(Request("POST", metadata["token_endpoint"], dict(FORM_HEADERS), encode_pairs(form)), log)
        if response.status != 200:
            raise _FlowStop("token", error_of(response))
        tokens = response.json()
        result.token_response = tokens
        result.access_token = tokens["access_token"]
        result.refresh_token = tokens.get("refresh_token", "")

        self._step = "mcp"
        headers = dict(MCP_REQUEST_HEADERS)
        headers["Authorization"] = "Bearer " + result.access_token
        final = self._send(Request("POST", mcp_url, headers, INITIALIZE_BODY), log)
        result.final = final
        if final.status != 200:
            raise _FlowStop("mcp", f"HTTP {final.status}")
        result.ok = True

    def refresh(self, result: FlowResult) -> Response:
        pairs = [
            ("grant_type", "refresh_token"),
            ("refresh_token", result.refresh_token),
            ("client_id", result.client_id),
        ]
        if self.profile.sends_resource:
            pairs.append(("resource", result.resource))
        request = Request("POST", result.metadata["token_endpoint"], dict(FORM_HEADERS), encode_pairs(pairs))
        return self._send(request, result.log)


# ---------------------------------------------------------------------------
# Self-test: expected wire contracts (architecture.md section 13)
# ---------------------------------------------------------------------------

ORDERS, KNOWLEDGE = DEFAULT_SERVERS
ORDERS_URL = GATEWAY_BASE + ORDERS.path
KNOWLEDGE_URL = GATEWAY_BASE + KNOWLEDGE.path
ORDERS_IDP_SCOPE = f"api://{ORDERS_APP_ID}/{MCP_SCOPE}"
KNOWLEDGE_IDP_SCOPE = f"api://{KNOWLEDGE_APP_ID}/{MCP_SCOPE}"

EXPECTED_CHALLENGE = (
    'Bearer resource_metadata="https://mcp.contoso.example/.well-known/oauth-protected-resource/orders/mcp", '
    'scope="mcp.tools"'
)
EXPECTED_PRM_ORDERS_C = {
    "resource": "https://mcp.contoso.example/orders/mcp",
    "authorization_servers": ["https://mcp.contoso.example/auth"],
    "scopes_supported": ["mcp.tools"],
    "bearer_methods_supported": ["header"],
    "resource_name": "Contoso Orders MCP server",
    "resource_documentation": "https://docs.contoso.example/mcp/orders",
}
EXPECTED_PRM_ORDERS_A_CORE = {
    "resource": "https://mcp.contoso.example/orders/mcp",
    "authorization_servers": ["https://login.example.com/00000000-0000-0000-0000-000000000000/v2.0"],
    "scopes_supported": ["api://11111111-1111-1111-1111-111111111111/mcp.tools"],
    "bearer_methods_supported": ["header"],
}
EXPECTED_AS_METADATA_CR = {
    "issuer": "https://mcp.contoso.example/auth",
    "authorization_endpoint": "https://mcp.contoso.example/auth/authorize",
    "token_endpoint": "https://mcp.contoso.example/auth/token",
    "registration_endpoint": "https://mcp.contoso.example/auth/register",
    "response_types_supported": ["code"],
    "response_modes_supported": ["query"],
    "grant_types_supported": ["authorization_code", "refresh_token"],
    "code_challenge_methods_supported": ["S256"],
    "token_endpoint_auth_methods_supported": ["none"],
    "scopes_supported": ["mcp.tools", "offline_access"],
    "service_documentation": "https://docs.contoso.example/mcp/auth",
}
EXPECTED_DCR_REQUEST = {
    "client_name": "Example MCP Client",
    "redirect_uris": ["http://127.0.0.1:53682/callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}
EXPECTED_DCR_RESPONSE = {
    "client_id": "22222222-2222-2222-2222-222222222222",
    "client_id_issued_at": 1792454400,
    "client_name": "Example MCP Client",
    "redirect_uris": ["http://127.0.0.1:53682/callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}
EXPECTED_FACADE_AUTHORIZE_URL = (
    "https://mcp.contoso.example/auth/authorize?response_type=code"
    "&client_id=22222222-2222-2222-2222-222222222222"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback"
    "&scope=mcp.tools"
    "&state=af0ifjsldkj"
    "&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    "&code_challenge_method=S256"
    "&resource=https%3A%2F%2Fmcp.contoso.example%2Forders%2Fmcp"
)
EXPECTED_AUTHORIZE_LOCATION = (
    "https://login.example.com/00000000-0000-0000-0000-000000000000/oauth2/v2.0/authorize"
    "?client_id=22222222-2222-2222-2222-222222222222"
    "&response_type=code"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback"
    "&scope=api%3A%2F%2F11111111-1111-1111-1111-111111111111%2Fmcp.tools%20offline_access"
    "&state=af0ifjsldkj"
    "&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
    "&code_challenge_method=S256"
)
EXPECTED_CLIENT_TOKEN_FORM = (
    "grant_type=authorization_code&code={code}"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback"
    "&client_id=22222222-2222-2222-2222-222222222222"
    "&code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    "&resource=https%3A%2F%2Fmcp.contoso.example%2Forders%2Fmcp"
)
EXPECTED_FORWARDED_TOKEN_FORM = (
    "grant_type=authorization_code&code={code}"
    "&redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback"
    "&client_id=22222222-2222-2222-2222-222222222222"
    "&code_verifier=dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    "&scope=api%3A%2F%2F11111111-1111-1111-1111-111111111111%2Fmcp.tools%20offline_access"
)
EXPECTED_TOKEN_RESPONSE_KEYS = ["token_type", "scope", "expires_in", "access_token", "refresh_token"]
EXPECTED_CLAIM_KEYS = ["aud", "iss", "tid", "azp", "scp", "oid", "sub", "ver", "iat", "nbf", "exp"]
EXPECTED_CLAIMS_WITHOUT_SUB = {
    "aud": "11111111-1111-1111-1111-111111111111",
    "iss": "https://login.example.com/00000000-0000-0000-0000-000000000000/v2.0",
    "tid": "00000000-0000-0000-0000-000000000000",
    "azp": "22222222-2222-2222-2222-222222222222",
    "scp": "mcp.tools",
    "oid": "44444444-4444-4444-4444-444444444444",
    "ver": "2.0",
    "iat": 1792454400,
    "nbf": 1792454400,
    "exp": 1792458000,
}
EXPECTED_INVALID_TOKEN = (
    'Bearer error="invalid_token", error_description="The access token is not valid for this resource.", '
    'resource_metadata="https://mcp.contoso.example/.well-known/oauth-protected-resource/orders/mcp"'
)
EXPECTED_INSUFFICIENT_SCOPE = (
    'Bearer error="insufficient_scope", scope="mcp.tools", '
    'resource_metadata="https://mcp.contoso.example/.well-known/oauth-protected-resource/orders/mcp"'
)


# ---------------------------------------------------------------------------
# Self-test: world builder and helpers
# ---------------------------------------------------------------------------


@dataclass
class World:
    config: FrontDoorConfig
    clock: FakeClock
    idp: FakeIdentityProvider
    registry: InMemoryClientRegistry
    network: Network
    front_door: OAuthFrontDoor
    backend: FakeBackend

    def send(self, request: Request) -> Response:
        return self.network.send(request)


def build_world(variant: str = "C+R", **overrides: Any) -> World:
    config = config_for_variant(variant, **overrides)
    clock = FakeClock()
    idp = FakeIdentityProvider(clock)
    registry = InMemoryClientRegistry()
    network = Network()
    backend = FakeBackend()
    front_door = OAuthFrontDoor(config, idp=idp, registry=registry, network=network, clock=clock, backend=backend)
    network.register(GATEWAY_HOST, front_door.handle)
    network.register(IDP_HOST, idp.handle)
    return World(config, clock, idp, registry, network, front_door, backend)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_raises(exc_type: type[BaseException], fn: Callable[[], Any], message: str) -> None:
    try:
        fn()
    except exc_type:
        return
    raise AssertionError(message)


def _apply(params: dict[str, str], overrides: dict[str, str | None]) -> list[tuple[str, str]]:
    """Override, add or (with None) remove parameters, keeping the original order."""
    merged = dict(params)
    for key, value in overrides.items():
        if value is None:
            merged.pop(key, None)
        else:
            merged[key] = value
    return list(merged.items())


def mcp_request(url: str = ORDERS_URL, token: str | None = None, headers: dict[str, str] | None = None) -> Request:
    merged = dict(MCP_REQUEST_HEADERS)
    if token is not None:
        merged["Authorization"] = "Bearer " + token
    merged.update(headers or {})
    return Request("POST", url, merged, INITIALIZE_BODY)


def facade_authorize_url(**overrides: str | None) -> str:
    params = {
        "response_type": "code",
        "client_id": SHARED_PUBLIC_CLIENT_ID,
        "redirect_uri": EXAMPLE_REDIRECT_URI,
        "scope": MCP_SCOPE,
        "state": EXAMPLE_STATE,
        "code_challenge": RFC7636_CHALLENGE,
        "code_challenge_method": "S256",
        "resource": ORDERS_URL,
    }
    return f"{FACADE_ISSUER}/authorize?" + encode_pairs(_apply(params, overrides))


def idp_authorize(world: World, **overrides: str | None) -> dict[str, str]:
    """Call the IdP's authorize endpoint directly; return the callback parameters."""
    params = {
        "client_id": SHARED_PUBLIC_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": EXAMPLE_REDIRECT_URI,
        "scope": ORDERS_IDP_SCOPE,
        "state": EXAMPLE_STATE,
        "code_challenge": RFC7636_CHALLENGE,
        "code_challenge_method": "S256",
    }
    url = f"{IDP_AUTHORITY}/oauth2/v2.0/authorize?" + encode_pairs(_apply(params, overrides))
    response = world.send(Request("GET", url))
    return first_values(urlsplit(response.header("Location") or "").query)


def token_form(code: str, **overrides: str | None) -> str:
    params = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": EXAMPLE_REDIRECT_URI,
        "client_id": SHARED_PUBLIC_CLIENT_ID,
        "code_verifier": RFC7636_VERIFIER,
        "resource": ORDERS_URL,
    }
    return encode_pairs(_apply(params, overrides))


def refresh_form(refresh_token: str, **overrides: str | None) -> str:
    params = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": SHARED_PUBLIC_CLIENT_ID,
        "resource": ORDERS_URL,
    }
    return encode_pairs(_apply(params, overrides))


def post_form(world: World, url: str, body: str, headers: dict[str, str] | None = None) -> Response:
    merged = dict(FORM_HEADERS)
    merged.update(headers or {})
    return world.send(Request("POST", url, merged, body))


def post_json(world: World, url: str, payload: Any, client_ip: str = "203.0.113.10") -> Response:
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return world.send(Request("POST", url, {"Content-Type": "application/json"}, body, client_ip))


def obtain_code(world: World, **overrides: str | None) -> str:
    """Authorize through the facade, follow the redirect to the IdP, return the code."""
    facade = world.send(Request("GET", facade_authorize_url(**overrides)))
    check(facade.status == 302, f"facade authorize returned {facade.status}")
    idp = world.send(Request("GET", facade.header("Location") or ""))
    callback = first_values(urlsplit(idp.header("Location") or "").query)
    check("code" in callback, f"IdP returned {callback.get('error', idp.status)}")
    return callback["code"]


def last_reason(world: World) -> str:
    return str(world.front_door.trace[-1]["reason"])


TOKEN_URL = FACADE_ISSUER + "/token"
REGISTER_URL = FACADE_ISSUER + "/register"
SCENARIOS: list[tuple[str, Callable[[], None]]] = []


def scenario(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        SCENARIOS.append((name, fn))
        return fn

    return register


# ---------------------------------------------------------------------------
# Self-test scenarios (S-numbers match docs/evaluation-and-troubleshooting.md)
# ---------------------------------------------------------------------------


@scenario("s1-challenge-carries-resource-metadata")
def _s1() -> None:
    w = build_world("C+R")
    r = w.send(mcp_request())
    check(r.status == 401, f"status {r.status}")
    check(r.header("WWW-Authenticate") == EXPECTED_CHALLENGE, "challenge differs from section 13.1")
    check(r.header("Access-Control-Expose-Headers") == "WWW-Authenticate", "challenge not exposed to browsers")
    check(r.header("Cache-Control") == "no-store", "challenge is cacheable")
    check(r.json() == {"message": "Authentication required."}, "unexpected challenge body")
    _, params = parse_www_authenticate(w.send(mcp_request(KNOWLEDGE_URL)).header("WWW-Authenticate") or "")
    check(params.get("resource_metadata") == w.config.prm_url_for(KNOWLEDGE), "knowledge challenge names the wrong PRM")
    other = w.send(mcp_request(headers={"Authorization": "Token abc"}))
    check(other.status == 401 and other.header("WWW-Authenticate") == EXPECTED_CHALLENGE, "non-Bearer not challenged")
    _, params_a = parse_www_authenticate(build_world("A").send(mcp_request()).header("WWW-Authenticate") or "")
    check(params_a.get("scope") == ORDERS_IDP_SCOPE, "Variant A should challenge with the IdP scope")
    check(prm_url_from_resource(ORDERS_URL) == w.config.prm_url_for(ORDERS), "RFC 9728 fallback URL differs")
    check(not w.backend.calls, "an unauthenticated call reached the backend")


@scenario("s2-prm-per-server")
def _s2() -> None:
    w = build_world("C+R")
    r = w.send(Request("GET", w.config.prm_url_for(ORDERS)))
    doc = r.json()
    check(r.status == 200, f"status {r.status}")
    check(doc == EXPECTED_PRM_ORDERS_C and list(doc) == list(EXPECTED_PRM_ORDERS_C), "PRM differs from section 13.2")
    check(r.header("Access-Control-Allow-Origin") == "*", "PRM not readable from browsers")
    check(r.header("Cache-Control") == w.config.metadata_cache_control, "PRM cache policy missing")
    check(validate_prm(doc, ORDERS_URL) == [], f"PRM invalid: {validate_prm(doc, ORDERS_URL)}")
    knowledge = w.send(Request("GET", w.config.prm_url_for(KNOWLEDGE))).json()
    check(knowledge["resource"] == KNOWLEDGE_URL and validate_prm(knowledge, KNOWLEDGE_URL) == [], "knowledge PRM")
    a_doc = build_world("A").send(Request("GET", w.config.prm_url_for(ORDERS))).json()
    a_core = {key: a_doc.get(key) for key in EXPECTED_PRM_ORDERS_A_CORE}
    check(a_core == EXPECTED_PRM_ORDERS_A_CORE, "Variant A PRM differs from section 13.2")
    b_doc = build_world("B").send(Request("GET", w.config.prm_url_for(ORDERS))).json()
    check(b_doc["authorization_servers"] == [FACADE_ISSUER], "Variant B should name the shim issuer")
    check(b_doc["scopes_supported"] == [ORDERS_IDP_SCOPE], "Variant B has no facade, so it lists the IdP scope")
    check(validate_prm(dict(doc, resource=KNOWLEDGE_URL), ORDERS_URL) != [], "validator missed a resource mismatch")


@scenario("s3-prm-unknown-path-404")
def _s3() -> None:
    w = build_world("C+R")
    for path in (PRM_PREFIX, PRM_PREFIX + "/", PRM_PREFIX + "/billing/mcp", PRM_PREFIX + "/orders"):
        r = w.send(Request("GET", GATEWAY_BASE + path))
        check(r.status == 404, f"{path} returned {r.status}")
        check(r.header("Access-Control-Allow-Origin") == "*", f"{path} 404 not readable from browsers")
    post = w.send(Request("POST", w.config.prm_url_for(ORDERS), body="{}"))
    check(post.status == 404 and w.front_door.trace[-1]["route"] == "not_found", "PRM accepted a POST")


@scenario("s4-discovery-finds-shim-with-s256")
def _s4() -> None:
    w = build_world("C+R")
    urls = discovery_urls(FACADE_ISSUER)
    check(
        urls
        == [
            GATEWAY_BASE + "/.well-known/oauth-authorization-server/auth",
            GATEWAY_BASE + "/.well-known/openid-configuration/auth",
            GATEWAY_BASE + "/auth/.well-known/openid-configuration",
        ],
        "discovery URL order changed",
    )
    r = w.send(Request("GET", urls[0]))
    doc = r.json()
    check(r.status == 200, f"status {r.status}")
    check(doc == EXPECTED_AS_METADATA_CR and list(doc) == list(EXPECTED_AS_METADATA_CR), "differs from section 13.3")
    check(validate_as_metadata(doc, FACADE_ISSUER, require_registration=True) == [], "shim metadata invalid")
    check("jwks_uri" not in doc, "the shim must not advertise keys it does not sign with")
    check(r.header("Access-Control-Allow-Origin") == "*", "metadata not readable from browsers")
    for url in urls[1:]:
        check(w.send(Request("GET", url)).status == 404, f"{url} should not be served")
    root = discovery_urls("https://as.example.com")
    check(
        root
        == [
            "https://as.example.com/.well-known/oauth-authorization-server",
            "https://as.example.com/.well-known/openid-configuration",
        ],
        "discovery URLs for a root issuer",
    )
    idp_errors = validate_as_metadata(w.idp.openid_configuration(), IDP_ISSUER)
    check(any("S256" in e for e in idp_errors), "the modeled IdP document should not advertise S256")
    plain = validate_as_metadata(dict(doc, code_challenge_methods_supported=["plain", "S256"]), FACADE_ISSUER)
    check(any("plain" in e for e in plain), "plain PKCE not flagged")
    insecure = validate_as_metadata(dict(doc, token_endpoint="http://mcp.contoso.example/auth/token"), FACADE_ISSUER)
    check(any("token_endpoint" in e for e in insecure), "non-HTTPS endpoint not flagged")


@scenario("s4b-metadata-shim-per-variant")
def _s4b() -> None:
    a = build_world("A")
    check(a.send(Request("GET", GATEWAY_BASE + AS_METADATA_PATH)).status == 404, "Variant A should serve no shim")
    oidc = a.idp.openid_configuration()
    b_doc = build_world("B").send(Request("GET", GATEWAY_BASE + AS_METADATA_PATH)).json()
    check(b_doc["issuer"] == FACADE_ISSUER, "Variant B shim issuer")
    check(
        b_doc["authorization_endpoint"] == oidc["authorization_endpoint"]
        and b_doc["token_endpoint"] == oidc["token_endpoint"],
        "Variant B should point at the IdP endpoints",
    )
    check("registration_endpoint" not in b_doc, "Variant B must not advertise registration")
    check(b_doc["scopes_supported"] == [ORDERS_IDP_SCOPE, KNOWLEDGE_IDP_SCOPE, "offline_access"], "Variant B scopes")
    check(b_doc["code_challenge_methods_supported"] == ["S256"], "Variant B must advertise S256")
    c_doc = build_world("C").send(Request("GET", GATEWAY_BASE + AS_METADATA_PATH)).json()
    check(c_doc["authorization_endpoint"] == FACADE_ISSUER + "/authorize", "Variant C should point at the facade")
    check("registration_endpoint" not in c_doc, "Variant C must not advertise registration")
    rogue = dict(oidc, issuer=f"https://{IDP_HOST}/{ROGUE_TENANT_ID}/v2.0")
    config = config_for_variant("C+R")
    expect_raises(ValueError, lambda: derive_as_metadata(rogue, config), "a mismatched OIDC issuer was accepted")


@scenario("s5-registration-returns-shared-client")
def _s5() -> None:
    w = build_world("C+R")
    r = post_json(w, REGISTER_URL, EXPECTED_DCR_REQUEST)
    check(r.status == 201, f"status {r.status}")
    check(r.json() == EXPECTED_DCR_RESPONSE, "registration response differs from section 13.4")
    check(r.header("Cache-Control") == "no-store", "registration response is cacheable")
    audit = [{"client_name": "Example MCP Client", "redirect_uris": [EXAMPLE_REDIRECT_URI], "caller_ip": "203.0.113.10"}]
    check(w.registry.registrations == audit, "registration not recorded for audit")
    minimal = post_json(w, REGISTER_URL, {"redirect_uris": ["http://localhost/callback"]}).json()
    check(minimal["client_name"] == "MCP client", "default client_name")
    check(minimal["grant_types"] == ["authorization_code", "refresh_token"], "default grant_types")
    unnamed = post_json(w, REGISTER_URL, {"redirect_uris": [EXAMPLE_REDIRECT_URI], "client_name": None}).json()
    check(unnamed["client_name"] == "MCP client", "null client_name should take the default")
    ids = {doc["client_id"] for doc in (r.json(), minimal, unnamed)}
    check(ids == {SHARED_PUBLIC_CLIENT_ID}, "every caller should get the same shared client ID")


@scenario("s6-registration-rejects-disallowed-metadata")
def _s6() -> None:
    w = build_world("C+R")
    good = {"redirect_uris": [EXAMPLE_REDIRECT_URI]}
    cases: list[tuple[Any, str]] = [
        ({"redirect_uris": ["https://evil.example/callback"]}, "invalid_redirect_uri"),
        ({"redirect_uris": ["http://127.0.0.1.evil.example/callback"]}, "invalid_redirect_uri"),
        ({"redirect_uris": [EXAMPLE_REDIRECT_URI + "?next=https://evil.example"]}, "invalid_redirect_uri"),
        ({"redirect_uris": [EXAMPLE_REDIRECT_URI, "https://evil.example/callback"]}, "invalid_redirect_uri"),
        ({"redirect_uris": []}, "invalid_redirect_uri"),
        ({"redirect_uris": EXAMPLE_REDIRECT_URI}, "invalid_redirect_uri"),
        (dict(good, token_endpoint_auth_method="client_secret_basic"), "invalid_client_metadata"),
        (dict(good, grant_types=["client_credentials"]), "invalid_client_metadata"),
        (dict(good, response_types=["token"]), "invalid_client_metadata"),
        (dict(good, client_name="x" * 101), "invalid_client_metadata"),
        (dict(good, client_name=123), "invalid_client_metadata"),
        ("not json", "invalid_client_metadata"),
        ("[]", "invalid_client_metadata"),
    ]
    for index, (payload, expected) in enumerate(cases):
        r = post_json(w, REGISTER_URL, payload, client_ip=f"198.51.100.{index + 1}")
        check(r.status == 400 and r.json().get("error") == expected, f"case {index}: {r.status} {r.body}")
        check(r.header("Cache-Control") == "no-store", f"case {index}: error is cacheable")
    refused = post_json(w, REGISTER_URL, {"redirect_uris": ["https://evil.example/callback"]}, "198.51.100.99")
    check(refused.json().get("error_description") == "Redirect URI is not allowed.", "section 13.4 error text")
    check(not w.registry.registrations, "a rejected registration was recorded")


@scenario("s6b-registration-disabled-and-rate-limited")
def _s6b() -> None:
    c = build_world("C")
    r = post_json(c, REGISTER_URL, EXPECTED_DCR_REQUEST)
    check(r.status == 404 and last_reason(c) == "registration_disabled", "Variant C must not register clients")
    w = build_world("C+R", registration_rate_calls=3)
    responses = [post_json(w, REGISTER_URL, EXPECTED_DCR_REQUEST) for _ in range(4)]
    statuses = [resp.status for resp in responses]
    check(statuses == [201, 201, 201, 429], f"statuses {statuses}")
    limited = responses[-1]
    check(limited.header("Retry-After") == str(w.config.registration_rate_period_seconds), "Retry-After")
    check(limited.json() == {"statusCode": 429, "message": "Rate limit is exceeded."}, "429 body")
    check(post_json(w, REGISTER_URL, EXPECTED_DCR_REQUEST, "198.51.100.7").status == 201, "limit is per caller")
    w.clock.advance(w.config.registration_rate_period_seconds + 1)
    check(post_json(w, REGISTER_URL, EXPECTED_DCR_REQUEST).status == 201, "limit should reset after the period")
    junk = build_world("C+R", registration_rate_calls=2)
    statuses = [post_json(junk, REGISTER_URL, body).status for body in ("x", "y", EXPECTED_DCR_REQUEST)]
    check(statuses == [400, 400, 429], f"malformed requests must count toward the limit: {statuses}")


@scenario("s7-authorize-maps-resource-to-scope")
def _s7() -> None:
    w = build_world("C+R")
    url = facade_authorize_url()
    check(url == EXPECTED_FACADE_AUTHORIZE_URL, "client authorize URL differs from section 13.5")
    r = w.send(Request("GET", url))
    check(r.status == 302 and r.header("Location") == EXPECTED_AUTHORIZE_LOCATION, "redirect differs from 13.5")
    check(r.header("Cache-Control") == "no-store", "authorize redirect is cacheable")
    q = first_values(urlsplit(r.header("Location") or "").query)
    check("resource" not in q, "resource leaked to the IdP")
    check(
        (q["state"], q["code_challenge"], q["code_challenge_method"]) == (EXAMPLE_STATE, RFC7636_CHALLENGE, "S256"),
        "state or PKCE values changed",
    )

    def mapped(**overrides: str | None) -> dict[str, str]:
        location = w.send(Request("GET", facade_authorize_url(**overrides))).header("Location") or ""
        return first_values(urlsplit(location).query)

    check(mapped(resource=KNOWLEDGE_URL)["scope"] == KNOWLEDGE_IDP_SCOPE + " offline_access", "knowledge mapping")
    check(mapped(resource=None)["scope"] == ORDERS_IDP_SCOPE + " offline_access", "default scope not applied")
    explicit = mapped(resource=None, scope=KNOWLEDGE_IDP_SCOPE + " openid")
    check(explicit["scope"] == KNOWLEDGE_IDP_SCOPE + " offline_access", "explicit IdP scope not honored")
    extra = mapped(prompt="select_account", foo="bar")
    check(extra.get("prompt") == "select_account" and "foo" not in extra, "pass-through allow-list not applied")
    idp = w.send(Request("GET", r.header("Location") or ""))
    callback = first_values(urlsplit(idp.header("Location") or "").query)
    check(idp.status == 302 and "code" in callback, f"IdP rejected the mapped request: {callback}")
    check(callback.get("state") == EXAMPLE_STATE, "state not returned unchanged")
    check(all("resource" not in a for a in w.idp.authorize_requests), "IdP saw a resource parameter")


@scenario("s8-authorize-requires-pkce-s256")
def _s8() -> None:
    w = build_world("C+R")
    cases: list[tuple[dict[str, str | None], str]] = [
        ({"code_challenge_method": None}, "pkce_s256_required"),
        ({"code_challenge_method": "plain", "code_challenge": RFC7636_VERIFIER}, "pkce_s256_required"),
        ({"code_challenge": None}, "pkce_challenge_invalid"),
        ({"code_challenge": "too-short"}, "pkce_challenge_invalid"),
        ({"code_challenge": RFC7636_CHALLENGE + "="}, "pkce_challenge_invalid"),
    ]
    for overrides, reason in cases:
        r = w.send(Request("GET", facade_authorize_url(**overrides)))
        check(r.status == 400 and r.header("Location") is None, f"{reason}: status {r.status}")
        check((r.header("Content-Type") or "").startswith("text/plain") and reason in r.body, f"{reason}: page")
        check(last_reason(w) == reason, f"{reason}: trace says {last_reason(w)}")
    check(not w.idp.authorize_requests, "a request without valid PKCE reached the IdP")
    direct = idp_authorize(w, code_challenge=RFC7636_VERIFIER, code_challenge_method="plain")
    check(direct.get("error") == "invalid_request", "the modeled IdP accepted plain PKCE")


@scenario("s9-authorize-rejects-unknown-client-or-redirect")
def _s9() -> None:
    w = build_world("C+R")
    cases: list[tuple[dict[str, str | None], str]] = [
        ({"client_id": UNKNOWN_CLIENT_ID}, "unknown_client"),
        ({"client_id": None}, "unknown_client"),
        ({"redirect_uri": "https://evil.example/callback"}, "redirect_uri_not_allowed"),
        ({"redirect_uri": "http://127.0.0.1:53682/callback/../steal"}, "redirect_uri_not_allowed"),
        ({"redirect_uri": None}, "redirect_uri_not_allowed"),
        ({"response_type": "token"}, "unsupported_response_type"),
    ]
    for overrides, reason in cases:
        r = w.send(Request("GET", facade_authorize_url(**overrides)))
        check(r.status == 400 and r.header("Location") is None, f"{reason}: status {r.status}")
        check(reason in r.body and "evil.example" not in r.body, f"{reason}: page must not echo the redirect URI")
        check(last_reason(w) == reason, f"{reason}: trace says {last_reason(w)}")
    check(not w.idp.authorize_requests, "a rejected request reached the IdP")
    # The IdP enforces its own registration as a second line of defense.
    for bad in ("https://evil.example/callback", "http://127.0.0.1:53682/other", EXAMPLE_REDIRECT_URI + "?next=x"):
        check(idp_authorize(w, redirect_uri=bad) == {}, f"the modeled IdP redirected to {bad}")
    check("code" in idp_authorize(w, redirect_uri="http://localhost:61000/callback"), "loopback port is ignored")


@scenario("s10-unknown-resource-invalid-target")
def _s10() -> None:
    w = build_world("C+R")
    for resource in (GATEWAY_BASE + "/billing/mcp", "https://mcp.fabrikam.example/orders/mcp", ORDERS_URL + "/"):
        r = w.send(Request("GET", facade_authorize_url(resource=resource)))
        check(r.status == 400 and r.header("Location") is None, f"authorize accepted {resource}")
        check(r.json().get("error") == "invalid_target", f"{resource}: {r.body}")
    t = post_form(w, TOKEN_URL, token_form("unused-code", resource=GATEWAY_BASE + "/billing/mcp"))
    check(t.status == 400 and t.json().get("error") == "invalid_target", "token facade accepted an unknown resource")
    check(not w.idp.authorize_requests and not w.idp.token_requests, "an unknown resource reached the IdP")


@scenario("variant-b-idp-rejects-foreign-resource")
def _variant_b() -> None:
    w = build_world("B")
    strict = McpClientSim(w.network, SPEC_CURRENT_CLIENT).connect(ORDERS_URL)
    check(
        not strict.ok and (strict.failed_step, strict.error) == ("authorize", "invalid_target"),
        f"expected authorize:invalid_target, got {strict.failed_step}:{strict.error}",
    )
    tolerant = McpClientSim(w.network, replace(SPEC_CURRENT_CLIENT, sends_resource=False)).connect(ORDERS_URL)
    check(tolerant.ok, f"without resource Variant B should work: {tolerant.failed_step}:{tolerant.error}")
    check(tolerant.metadata["authorization_endpoint"].startswith(IDP_AUTHORITY), "Variant B authorizes at the IdP")
    check(idp_authorize(w, scope=MCP_SCOPE).get("error") == "invalid_scope", "IdP accepted a bare scope")


@scenario("variant-a-direct-idp-discovery-gaps")
def _variant_a() -> None:
    w = build_world("A")
    strict = McpClientSim(w.network, SPEC_CURRENT_CLIENT).connect(ORDERS_URL)
    check(
        (strict.failed_step, strict.error) == ("discovery", "code_challenge_methods_supported does not include S256"),
        f"spec-current client: {strict.failed_step}:{strict.error}",
    )
    statuses = [line.rsplit(" -> ", 1)[1] for line in strict.log]
    check(statuses == ["401", "200", "404", "404", "200"], f"discovery walk {statuses}")
    older = McpClientSim(w.network, OLDER_DISCOVERY_CLIENT).connect(ORDERS_URL)
    check(
        (older.failed_step, older.error) == ("discovery", "no authorization server metadata found"),
        f"older-discovery client: {older.failed_step}:{older.error}",
    )
    lenient = McpClientSim(w.network, LENIENT_CLIENT).connect(ORDERS_URL)
    check(lenient.ok, f"lenient client: {lenient.failed_step}:{lenient.error}")
    check(lenient.metadata.get("issuer") == IDP_ISSUER, "Variant A clients discover the IdP itself")
    check(lenient.token_response.get("scope") == ORDERS_IDP_SCOPE, "Variant A token scope")
    check(lenient.refresh_token == "", "no offline_access was requested, so no refresh token")
    check({t["route"] for t in w.front_door.trace} == {"mcp", "prm"}, "Variant A gateway serves only PRM and MCP")
    check(w.send(Request("GET", facade_authorize_url())).status == 404, "Variant A serves no facade")


@scenario("s11-end-to-end-token-audience")
def _s11() -> None:
    w = build_world("C+R")
    result = McpClientSim(w.network, REGISTRATION_DEPENDENT_CLIENT).connect(ORDERS_URL)
    check(result.ok, f"flow failed at {result.failed_step}: {result.error}")
    routes = [t["route"] for t in w.front_door.trace]
    check(routes == ["mcp", "prm", "as_metadata", "register", "authorize", "token", "mcp"], f"routes {routes}")
    reasons = [t["reason"] for t in w.front_door.trace]
    expected_reasons = ["challenge", "", "", "accepted", "redirected", "forwarded:authorization_code", "validated"]
    check(reasons == expected_reasons, f"reasons {reasons}")
    statuses = [line.rsplit(" -> ", 1)[1] for line in result.log]
    check(statuses == ["401", "200", "200", "201", "302", "302", "200", "200"], f"client log {statuses}")
    check(result.client_id == SHARED_PUBLIC_CLIENT_ID, "registration should return the shared client ID")
    tokens = result.token_response
    check(list(tokens) == EXPECTED_TOKEN_RESPONSE_KEYS, f"token response keys {list(tokens)}")
    check(
        (tokens["token_type"], tokens["scope"], tokens["expires_in"])
        == ("Bearer", ORDERS_IDP_SCOPE, ACCESS_TOKEN_LIFETIME),
        "token response differs from section 13.8",
    )
    header, claims = decode_jwt_unverified(result.access_token)
    check(header.get("alg") == "HS256" and header.get("typ") == "JWT", "unexpected test token header")
    check(list(claims) == EXPECTED_CLAIM_KEYS, f"claim order {list(claims)}")
    without_sub = {k: v for k, v in claims.items() if k != "sub"}
    check(without_sub == EXPECTED_CLAIMS_WITHOUT_SUB, "claims differ from section 13.9")
    check(isinstance(claims["sub"], str) and claims["sub"] not in ("", USER_OBJECT_ID), "sub should be pairwise")
    final = result.final
    check(final is not None and final.status == 200, "final MCP call failed")
    if final is not None:
        check(final.json()["result"] == {"server": ORDERS.name, "tenant": TENANT_ID}, "backend result")
    c_world = build_world("C")
    blocked = McpClientSim(c_world.network, REGISTRATION_DEPENDENT_CLIENT).connect(ORDERS_URL)
    check(
        (blocked.failed_step, blocked.error) == ("registration", "no registration_endpoint advertised"),
        f"Variant C should stop a registration-dependent client: {blocked.failed_step}:{blocked.error}",
    )
    preregistered = McpClientSim(c_world.network, SPEC_CURRENT_CLIENT).connect(ORDERS_URL)
    check(preregistered.ok, f"Variant C pre-registered client: {preregistered.failed_step}:{preregistered.error}")


@scenario("s12-wrong-verifier-and-code-reuse")
def _s12() -> None:
    w = build_world("C+R")
    code = obtain_code(w)
    wrong = post_form(w, TOKEN_URL, token_form(code, code_verifier="a" * 43))
    check(wrong.status == 400 and wrong.json().get("error") == "invalid_grant", f"wrong verifier: {wrong.body}")
    check(last_reason(w) == "forwarded:authorization_code", "the IdP, not the facade, checks the verifier")
    retry = post_form(w, TOKEN_URL, token_form(code))
    check(retry.json().get("error") == "invalid_grant", "a code must not survive a failed redemption")
    code = obtain_code(w)
    first = post_form(w, TOKEN_URL, token_form(code))
    check(first.status == 200, f"redemption failed: {first.body}")
    reuse = post_form(w, TOKEN_URL, token_form(code))
    check(reuse.status == 400 and reuse.json().get("error") == "invalid_grant", "a code was redeemed twice")
    code = obtain_code(w)
    moved = post_form(w, TOKEN_URL, token_form(code, redirect_uri="http://localhost:53682/callback"))
    check(moved.json().get("error") == "invalid_grant", "the redirect URI must match the authorization request")
    sent = len(w.idp.token_requests)
    malformed = [
        token_form(code, code_verifier="short"),
        token_form(code, code_verifier=None),
        token_form(""),
        token_form(code, redirect_uri="https://evil.example/callback"),
    ]
    for index, body in enumerate(malformed):
        r = post_form(w, TOKEN_URL, body)
        check(r.status == 400 and r.json().get("error") == "invalid_request", f"case {index}: {r.body}")
    check(len(w.idp.token_requests) == sent, "a malformed token request reached the IdP")


@scenario("token-facade-forwarding-contract")
def _token_contract() -> None:
    w = build_world("C+R")
    code = obtain_code(w)
    body = token_form(code)
    check(body == EXPECTED_CLIENT_TOKEN_FORM.format(code=quote(code, safe="")), "client form differs from 13.7")
    origin = "http://127.0.0.1:53682"
    r = post_form(w, TOKEN_URL, body, {"Cookie": "pref=1", "Origin": origin})
    check(r.status == 200, f"status {r.status}: {r.body}")
    check(list(r.json()) == EXPECTED_TOKEN_RESPONSE_KEYS, "token response keys")
    check((r.header("Cache-Control"), r.header("Pragma")) == ("no-store", "no-cache"), "token response is cacheable")
    check(last_reason(w) == "forwarded:authorization_code", f"trace says {last_reason(w)}")
    upstream = w.idp.token_requests[-1]
    check(upstream.url == IDP_AUTHORITY + "/oauth2/v2.0/token", "forwarded to the wrong endpoint")
    forwarded = EXPECTED_FORWARDED_TOKEN_FORM.format(code=quote(code, safe=""))
    check(upstream.body == forwarded, "forwarded form differs from section 13.7")
    check(get_header(upstream.headers, "Cookie") is None, "gateway cookies were forwarded to the IdP")
    check(get_header(upstream.headers, "Origin") == origin, "Origin must reach the IdP unchanged")
    check(get_header(upstream.headers, "Content-Type") == FORM_HEADERS["Content-Type"], "forwarded content type")
    sent = len(w.idp.token_requests)
    rejected = [
        (token_form(code, grant_type="client_credentials"), "unsupported_grant_type"),
        (token_form(code, grant_type="password"), "unsupported_grant_type"),
        (token_form(code, client_id=UNKNOWN_CLIENT_ID), "unauthorized_client"),
    ]
    for form, expected in rejected:
        resp = post_form(w, TOKEN_URL, form)
        check(resp.status == 400 and resp.json().get("error") == expected, f"{expected}: {resp.body}")
        check(resp.header("Cache-Control") == "no-store", f"{expected}: error is cacheable")
    check(len(w.idp.token_requests) == sent, "a rejected token request reached the IdP")
    check(post_form(build_world("B"), TOKEN_URL, body).status == 404, "Variant B serves no token facade")


@scenario("token-facade-idp-unreachable-502")
def _idp_down() -> None:
    w = build_world("C+R")
    code = obtain_code(w)
    w.network.unregister(IDP_HOST)
    r = post_form(w, TOKEN_URL, token_form(code))
    check(r.status == 502 and r.json().get("error") == "temporarily_unavailable", f"status {r.status}: {r.body}")
    check(r.header("Cache-Control") == "no-store", "502 is cacheable")
    check(last_reason(w) == "idp_unreachable", f"trace says {last_reason(w)}")
    check(w.send(mcp_request()).status == 401, "the challenge must not depend on the IdP")
    check(w.send(Request("GET", w.config.prm_url_for(ORDERS))).status == 200, "PRM must not depend on the IdP")


@scenario("s13-wrong-audience-or-expired-rejected")
def _s13() -> None:
    w = build_world("C+R")

    def call(world: World, token: str, url: str = ORDERS_URL) -> Response:
        return world.send(mcp_request(url, token))

    knowledge_token = w.idp.mint_access_token(aud=KNOWLEDGE_APP_ID)
    r = call(w, knowledge_token)
    check(r.status == 401 and r.header("WWW-Authenticate") == EXPECTED_INVALID_TOKEN, "wrong audience accepted")
    check(r.header("Access-Control-Expose-Headers") == "WWW-Authenticate", "401 challenge not exposed")
    check(last_reason(w) == "invalid_token", f"trace says {last_reason(w)}")
    check(call(w, knowledge_token, KNOWLEDGE_URL).status == 200, "the knowledge token should work on its own server")
    check(call(w, w.idp.mint_access_token(aud=f"api://{ORDERS_APP_ID}")).status == 200, "api:// audience form")
    token = w.idp.mint_access_token()
    w.clock.advance(ACCESS_TOKEN_LIFETIME - 1)
    check(call(w, token).status == 200, "a token should be valid until exp")
    w.clock.advance(1)
    check(call(w, token).status == 401, "an expired token was accepted")
    early = w.idp.mint_access_token()
    w.clock.advance(-10)
    check(call(w, early).status == 401, "a token used before nbf was accepted")
    skewed = build_world("C+R", clock_skew_seconds=300)
    late = skewed.idp.mint_access_token()
    skewed.clock.advance(ACCESS_TOKEN_LIFETIME + 299)
    check(call(skewed, late).status == 200, "configured clock skew not applied to exp")
    skewed.clock.advance(1)
    check(call(skewed, late).status == 401, "clock skew must be bounded")
    early_skewed = skewed.idp.mint_access_token()
    skewed.clock.advance(-10)
    check(call(skewed, early_skewed).status == 200, "configured clock skew not applied to nbf")
    for index, garbage in enumerate(("", "not-a-jwt", "a.b.c", "e30.e30.", token + ".extra")):
        resp = call(w, garbage)
        check(resp.status == 401 and last_reason(w) == "invalid_token", f"malformed token {index} accepted")
    check(len(w.backend.calls) == 3, f"backend calls {len(w.backend.calls)}")


@scenario("s14-wrong-issuer-key-or-tenant-rejected")
def _s14() -> None:
    w = build_world("C+R")
    good = w.idp.mint_access_token()
    header, claims = decode_jwt_unverified(good)
    head_b64, payload_b64, sig_b64 = good.split(".")

    def segment(value: dict[str, Any]) -> str:
        return b64url(json.dumps(value, separators=(",", ":")).encode("utf-8"))

    escalated = segment(dict(claims, scp="mcp.tools mcp.admin"))
    none_header = segment(dict(header, alg="none"))
    other_idp = FakeIdentityProvider(w.clock, tenant_id=ROGUE_TENANT_ID)
    forged = {
        "rogue issuer": w.idp.mint_access_token(issuer=f"https://{IDP_HOST}/{ROGUE_TENANT_ID}/v2.0"),
        "rogue key": w.idp.mint_access_token(key=secrets.token_bytes(32)),
        "unknown kid": w.idp.mint_access_token(kid="test-unknown"),
        "wrong tenant claim": w.idp.mint_access_token(tid=ROGUE_TENANT_ID),
        "another tenant's IdP": other_idp.mint_access_token(),
        "tampered payload": f"{head_b64}.{escalated}.{sig_b64}",
        "alg none": f"{none_header}.{payload_b64}.",
        "alg none with signature": f"{none_header}.{payload_b64}.{sig_b64}",
    }
    for label, token in forged.items():
        r = w.send(mcp_request(token=token))
        check(r.status == 401 and r.header("WWW-Authenticate") == EXPECTED_INVALID_TOKEN, f"{label} accepted")
    check(not w.backend.calls, "a forged token reached the backend")
    check(w.send(mcp_request(token=good)).status == 200, "the control token should pass")


@scenario("s15-missing-scope-403")
def _s15() -> None:
    w = build_world("C+R")
    r = w.send(mcp_request(token=w.idp.mint_access_token(scp="other")))
    check(r.status == 403 and r.header("WWW-Authenticate") == EXPECTED_INSUFFICIENT_SCOPE, f"status {r.status}")
    check(r.header("Access-Control-Expose-Headers") == "WWW-Authenticate", "403 challenge not exposed")
    check(last_reason(w) == "insufficient_scope", f"trace says {last_reason(w)}")
    for scp in ("mcp.tools.extra", "", "MCP.TOOLS", "mcp.toolsx other"):
        resp = w.send(mcp_request(token=w.idp.mint_access_token(scp=scp)))
        check(resp.status == 403, f"scope {scp!r} accepted")
    check(not w.backend.calls, "an under-scoped token reached the backend")
    multi = w.send(mcp_request(token=w.idp.mint_access_token(scp="other mcp.tools")))
    check(multi.status == 200, "a token carrying the required scope among others should pass")


@scenario("s16-no-token-passthrough")
def _s16() -> None:
    w = build_world("C+R")
    result = McpClientSim(w.network, SPEC_CURRENT_CLIENT).connect(ORDERS_URL)
    check(result.ok, f"flow failed at {result.failed_step}: {result.error}")
    token = w.idp.mint_access_token()
    lower = w.send(mcp_request(headers={"authorization": "Bearer " + token, "X-Request-Id": "req-1"}))
    check(lower.status == 200, "a lower-case Authorization header should validate")
    check(len(w.backend.calls) == 2, f"backend calls {len(w.backend.calls)}")
    expected_context = {
        "tenant_id": TENANT_ID,
        "user_object_id": USER_OBJECT_ID,
        "client_id": SHARED_PUBLIC_CLIENT_ID,
        "scopes": [MCP_SCOPE],
    }
    for call in w.backend.calls:
        headers = call["headers"]
        check(get_header(headers, "Authorization") is None, "the client token was forwarded to the backend")
        check(
            not any(result.access_token in value or token in value for value in headers.values()),
            "a token leaked into a forwarded header",
        )
        check(call["context"] == expected_context, f"context {call['context']}")
    check(get_header(w.backend.calls[0]["headers"], "Content-Type") == "application/json", "content type dropped")
    check(get_header(w.backend.calls[1]["headers"], "X-Request-Id") == "req-1", "other headers should pass through")


@scenario("s17-refresh-keeps-audience")
def _s17() -> None:
    w = build_world("C+R")
    client = McpClientSim(w.network, SPEC_CURRENT_CLIENT)
    result = client.connect(ORDERS_URL)
    check(result.ok and bool(result.refresh_token), "the facade adds offline_access, so a refresh token is expected")
    r = client.refresh(result)
    check(r.status == 200, f"refresh failed: {r.body}")
    check(last_reason(w) == "forwarded:refresh_token", f"trace says {last_reason(w)}")
    refreshed = r.json()
    _, claims = decode_jwt_unverified(refreshed["access_token"])
    check(claims["aud"] == ORDERS_APP_ID and refreshed["scope"] == ORDERS_IDP_SCOPE, "refresh changed the audience")
    check(refreshed["refresh_token"] != result.refresh_token, "the refresh token should rotate")
    upstream = parse_form(w.idp.token_requests[-1].body)
    check(upstream.get("scope") == ORDERS_IDP_SCOPE + " offline_access", "refresh scope not mapped")
    check("resource" not in upstream, "resource leaked to the IdP on refresh")
    check(w.send(mcp_request(token=refreshed["access_token"])).status == 200, "refreshed token rejected")
    check(w.send(mcp_request(KNOWLEDGE_URL, refreshed["access_token"])).status == 401, "refreshed token reused")
    other = post_form(w, TOKEN_URL, refresh_form(refreshed["refresh_token"], resource=KNOWLEDGE_URL))
    check(other.status == 200, f"knowledge refresh failed: {other.body}")
    _, other_claims = decode_jwt_unverified(other.json()["access_token"])
    check(other_claims["aud"] == KNOWLEDGE_APP_ID, "resource should select the audience on refresh")
    rt = other.json()["refresh_token"]
    cases = [
        (refresh_form(rt, client_id=UNKNOWN_CLIENT_ID), "unauthorized_client"),
        (refresh_form("not-a-known-refresh-token"), "invalid_grant"),
        (refresh_form(""), "invalid_request"),
        (refresh_form(rt, resource=GATEWAY_BASE + "/billing/mcp"), "invalid_target"),
    ]
    for body, expected in cases:
        resp = post_form(w, TOKEN_URL, body)
        check(resp.status == 400 and resp.json().get("error") == expected, f"{expected}: {resp.body}")


@scenario("pkce-rfc7636-test-vector")
def _pkce() -> None:
    check(make_challenge(RFC7636_VERIFIER) == RFC7636_CHALLENGE, "RFC 7636 appendix B test vector")
    check(re.fullmatch(PKCE_VERIFIER_REGEX, RFC7636_VERIFIER) is not None, "test vector verifier format")
    check(re.fullmatch(PKCE_CHALLENGE_REGEX, RFC7636_CHALLENGE) is not None, "test vector challenge format")
    for bad in ("a" * 42, "a" * 129, "a" * 42 + "+", "a" * 42 + "="):
        check(re.fullmatch(PKCE_VERIFIER_REGEX, bad) is None, f"verifier of length {len(bad)} accepted")
    check(re.fullmatch(PKCE_CHALLENGE_REGEX, RFC7636_CHALLENGE + "=") is None, "padded challenge accepted")
    verifier = b64url(secrets.token_bytes(32))
    check(re.fullmatch(PKCE_VERIFIER_REGEX, verifier) is not None, "generated verifier format")
    w = build_world("C+R")
    result = McpClientSim(w.network, SPEC_CURRENT_CLIENT, verifier=verifier).connect(ORDERS_URL)
    check(result.ok, f"flow with a fresh verifier failed at {result.failed_step}: {result.error}")
    forwarded = w.idp.authorize_requests[-1]
    check(forwarded.get("code_challenge") == make_challenge(verifier), "the facade altered code_challenge")
    check(forwarded.get("code_challenge_method") == "S256", "the facade altered code_challenge_method")
    upstream = parse_form(w.idp.token_requests[-1].body)
    check(upstream.get("code_verifier") == verifier, "the facade altered code_verifier")


@scenario("production-adapter-is-gated")
def _gated() -> None:
    expect_raises(RuntimeError, lambda: EntraIdentityProvider(IDP_AUTHORITY), "production adapter is not gated")
    adapter = EntraIdentityProvider(IDP_AUTHORITY, enable_network=True)
    check(adapter.issuer == IDP_ISSUER, "adapter issuer")
    expect_raises(NotImplementedError, adapter.openid_configuration, "stub must not fetch discovery")
    expect_raises(NotImplementedError, adapter.signing_keys, "stub must not fetch keys")
    expect_raises(ValueError, lambda: config_for_variant("Z"), "unknown variant accepted")


@scenario("emitted-documents-valid-and-trace-clean")
def _emitted() -> None:
    documents = emit_documents()
    statuses = [response.status for _, _, response in documents]
    check(statuses == [401, 200, 200, 200, 201, 302], f"statuses {statuses}")
    challenge = documents[0][2]
    scheme, params = parse_www_authenticate(challenge.header("WWW-Authenticate") or "")
    check(scheme == "Bearer", f"scheme {scheme}")
    check(params.get("resource_metadata") == prm_url_from_resource(ORDERS_URL), "resource_metadata URL")
    check(validate_prm(documents[1][2].json(), ORDERS_URL) == [], "emitted PRM (C+R) invalid")
    check(validate_prm(documents[2][2].json(), ORDERS_URL) == [], "emitted PRM (A) invalid")
    errors = validate_as_metadata(documents[3][2].json(), FACADE_ISSUER, require_registration=True)
    check(errors == [], f"emitted metadata invalid: {errors}")
    check(documents[4][2].json().get("client_id") == SHARED_PUBLIC_CLIENT_ID, "emitted registration response")
    location = documents[5][2].header("Location") or ""
    check(location.startswith(IDP_AUTHORITY + "/oauth2/v2.0/authorize?"), "emitted authorize redirect")
    check("resource=" not in location, "resource leaked to the IdP")

    w = build_world("C+R")
    client = McpClientSim(w.network, REGISTRATION_DEPENDENT_CLIENT)
    result = client.connect(ORDERS_URL)
    check(result.ok, f"flow failed at {result.failed_step}: {result.error}")
    refreshed = client.refresh(result).json()
    secrets_seen = [
        result.access_token,
        result.refresh_token,
        refreshed.get("access_token", ""),
        refreshed.get("refresh_token", ""),
    ]
    check(all(secrets_seen), "expected tokens to compare against")
    gateway_trace = json.dumps(w.front_door.trace)
    client_log = "\n".join(result.log)
    registrations = json.dumps(w.registry.registrations)
    for secret in secrets_seen:
        for label, text in (("trace", gateway_trace), ("log", client_log), ("registrations", registrations)):
            check(secret not in text, f"a token appears in the {label}")
    check(not any("?" in line for line in result.log), "a query string (code or state) appears in the client log")
    check(set(w.front_door.trace[0]) == {"route", "status", "reason"}, "trace entries carry extra fields")


def run_self_test() -> int:
    failures = 0
    for name, fn in SCENARIOS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report every scenario
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# --emit and --demo
# ---------------------------------------------------------------------------


def emit_documents() -> list[tuple[str, str, Response]]:
    """The wire contracts from architecture.md section 13, produced by the simulator."""
    cr = build_world("C+R")
    a = build_world("A")
    documents: list[tuple[str, str, Response]] = []

    def add(title: str, world: World, request: Request) -> None:
        documents.append((title, f"{request.method} {request.url}", world.send(request)))

    add("13.1 Challenge on an unauthenticated MCP request", cr, mcp_request())
    add("13.2 Protected resource metadata (Variants B, C, C+R)", cr, Request("GET", cr.config.prm_url_for(ORDERS)))
    add("13.2 Protected resource metadata (Variant A)", a, Request("GET", a.config.prm_url_for(ORDERS)))
    add("13.3 Authorization server metadata shim (Variant C+R)", cr, Request("GET", GATEWAY_BASE + AS_METADATA_PATH))
    registration = Request("POST", REGISTER_URL, {"Content-Type": "application/json"}, json.dumps(EXPECTED_DCR_REQUEST))
    add("13.4 Registration shim (Variant C+R)", cr, registration)
    add("13.5 Authorization request through the facade", cr, Request("GET", facade_authorize_url()))
    return documents


def render_response(response: Response) -> str:
    lines = [f"HTTP {response.status}"]
    lines.extend(f"{name}: {value}" for name, value in response.headers.items())
    if response.body:
        try:
            body = json.dumps(json.loads(response.body), indent=2)
        except ValueError:
            body = response.body
        lines.extend(["", body])
    return "\n".join(lines)


def emit() -> int:
    for title, request_line, response in emit_documents():
        print(f"=== {title}")
        print(request_line)
        print(render_response(response))
        print()
    return 0


def demo() -> int:
    print(f"Variant C+R: a registration-dependent client connects to {ORDERS_URL}")
    w = build_world("C+R")
    client = McpClientSim(w.network, REGISTRATION_DEPENDENT_CLIENT)
    result = client.connect(ORDERS_URL)
    print("\nClient requests (query strings omitted because they carry codes and state):")
    for line in result.log:
        print(f"  {line}")
    if not result.ok:
        print(f"\nFlow stopped at {result.failed_step}: {result.error}")
        return 1
    _, claims = decode_jwt_unverified(result.access_token)
    print("\nAccess token claims (the token itself is never printed):")
    for key in ("aud", "iss", "tid", "azp", "scp", "oid"):
        print(f"  {key}: {claims[key]}")
    print("\nGateway trace (route, status, reason):")
    for entry in w.front_door.trace:
        print(f"  {entry['route']:<12} {entry['status']}  {entry['reason']}")
    refreshed = client.refresh(result)
    if refreshed.status == 200:
        _, refreshed_claims = decode_jwt_unverified(refreshed.json()["access_token"])
        print(f"\nRefresh: HTTP 200, audience still {refreshed_claims['aud']}")
    else:
        print(f"\nRefresh: HTTP {refreshed.status} {error_of(refreshed)}")
    wrong = w.send(mcp_request(KNOWLEDGE_URL, result.access_token))
    print(f"Same token on {KNOWLEDGE.name}: HTTP {wrong.status}")
    print(f"  WWW-Authenticate: {wrong.header('WWW-Authenticate')}")
    call = w.backend.calls[-1]
    print("\nBackend received (no Authorization header; identity travels as context):")
    print(f"  headers: {', '.join(sorted(call['headers']))}")
    print(f"  context: {json.dumps(call['context'])}")

    profiles = (SPEC_CURRENT_CLIENT, REGISTRATION_DEPENDENT_CLIENT, OLDER_DISCOVERY_CLIENT, LENIENT_CLIENT)
    print("\nCompatibility by variant (simulated client types, not named products):")
    for variant in ("A", "B", "C", "C+R"):
        print(f"  Variant {variant}")
        for profile in profiles:
            world = build_world(variant)
            outcome = McpClientSim(world.network, profile).connect(ORDERS_URL)
            status = "signed in" if outcome.ok else f"stops at {outcome.failed_step} ({outcome.error})"
            print(f"    {profile.label}: {status}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline simulator for the MCP OAuth front door pattern (no network, no packages).",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="run every scenario and exit 0 only if all pass")
    mode.add_argument("--emit", action="store_true", help="print the wire-contract documents (section 13)")
    mode.add_argument("--demo", action="store_true", help="narrate one sign-in and the variant comparison")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.emit:
        return emit()
    if args.demo:
        return demo()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
