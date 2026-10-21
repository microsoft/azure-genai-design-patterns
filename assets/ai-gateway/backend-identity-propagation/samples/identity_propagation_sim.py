#!/usr/bin/env python3
"""Backend identity propagation simulator (standard library only).

Simulates how an MCP server or agent gateway carries the end user's identity
to backend APIs without ever passing the client's token through:

- Option A: on-behalf-of token exchange (the reporting API trusts the same IdP).
- Option B: signed user assertion plus identity link, bound to the gateway's
  mTLS connection (the field-service API has its own user store).
- Option C: per-tenant credential bridge with an acting-user header
  (the orders API only supports per-tenant API keys). Transitional.
- App-only calls for reference data that needs no user context.
- An agent acting as itself, without a user.
- Token passthrough, which the backends reject by audience and algorithm.

The fake identity provider is modeled on Microsoft Entra on-behalf-of
semantics (jwt-bearer grant, client assertion, consent and Conditional
Access errors). Verify behavior against current documentation.

TEST ONLY: tokens are HS256 and signed with in-memory secrets. The assertion
header says ES256 but is HMAC-signed by a simulated key; the backend receives
a verify-only view that stands in for a JWKS document. Certificates are
labels hashed into thumbprints, and there is no network. Production uses
asymmetric keys held in Key Vault or an HSM, a published JWKS, and real mTLS.

Usage:
    python identity_propagation_sim.py --self-test
    python identity_propagation_sim.py --demo
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

# ---------------------------------------------------------------------------
# Identifiers (placeholders only)
# ---------------------------------------------------------------------------

GATEWAY_APP_ID = "11111111-1111-1111-1111-111111111111"
MCP_CLIENT_ID = "33333333-3333-3333-3333-333333333333"
GATEWAY_MI_ID = "44444444-4444-4444-4444-444444444444"
ROGUE_MI_ID = "55555555-5555-5555-5555-555555555555"
AGENT_APP_ID = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
CONTOSO_TID = "dddddddd-dddd-dddd-dddd-dddddddddddd"
FABRIKAM_TID = "00000000-0000-0000-0000-000000000000"
NORTHWIND_TID = "66666666-6666-6666-6666-666666666666"
UNKNOWN_TID = "cccccccc-cccc-cccc-cccc-cccccccccccc"
ALICE = "22222222-2222-2222-2222-222222222222"
BOB = "77777777-7777-7777-7777-777777777777"
DAVE = "88888888-8888-8888-8888-888888888888"
CAROL = "99999999-9999-9999-9999-999999999999"
ERIN = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
AGENT_OID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

GATEWAY_SCOPE = "mcp.tools"
AGENT_ROLE = "Mcp.Agent"
REPORTING_AUD = "api://contoso-erp-reporting"
REPORTING_SCOPE = REPORTING_AUD + "/Reports.Read"
FIELD_SERVICE_AUD = "urn:contoso:erp:field-service"
ORDERS_AUD = "api://contoso-erp-orders"
REFERENCE_ROLE = "Reference.Read"
TOKEN_EXCHANGE_AUDIENCE = "api://AzureADTokenExchange"
ASSERTION_ISSUER = "https://mcp.contoso.example/assertions"
ASSERTION_TYP = "user-assertion+jwt"
ALLOWED_ASSERTION_ALGS = ("ES256", "PS256")
LINK_URL = "https://mcp.contoso.example/link"
ORDERS_SECRET_TEMPLATE = "tenant-{tenantId}-orders-api-key"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
CLIENT_ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"

# Simulation values, not recommendations.
IDP_TOKEN_LIFETIME = 3600
OBO_EXPIRY_SKEW = 120
ASSERTION_LIFETIME = 60
ASSERTION_MAX_LIFETIME = 120
CLOCK_SKEW = 30
LINK_CACHE_TTL = 300
LINK_NEGATIVE_TTL = 10
TENANT_KEY_TTL = 300
JWKS_TTL = 600
JWKS_FORCED_REFRESH_INTERVAL = 60

MFA_CLAIMS = json.dumps(
    {"access_token": {"amr": {"essential": True, "values": ["mfa"]}}},
    separators=(",", ":"),
)

# Validated Entra tenant ID -> (backend tenant slug, deployment cell).
TENANT_DIRECTORY = {
    FABRIKAM_TID: ("fabrikam", "cell-eu-1"),
    NORTHWIND_TID: ("northwind", "cell-us-1"),
}


class FakeClock:
    def __init__(self, now: int = 1792483200) -> None:
        self.now = now

    def advance(self, seconds: int) -> None:
        self.now += seconds


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def encode_segment(obj: Any) -> str:
    return b64url(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class TokenError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def split_jwt(token: Any) -> tuple[dict, dict, bytes, bytes]:
    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise TokenError("malformed")
    try:
        header = json.loads(b64url_decode(parts[0]))
        claims = json.loads(b64url_decode(parts[1]))
        signature = b64url_decode(parts[2])
    except ValueError:
        raise TokenError("malformed") from None
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("malformed")
    return header, claims, (parts[0] + "." + parts[1]).encode("ascii"), signature


def sign_jws(header: dict, claims: dict, sign_fn: Callable[[bytes], bytes]) -> str:
    signing_input = encode_segment(header) + "." + encode_segment(claims)
    return signing_input + "." + b64url(sign_fn(signing_input.encode("ascii")))


def thumbprint(label: str) -> str:
    """Stand-in for the base64url SHA-256 thumbprint of a client certificate."""
    return b64url(hashlib.sha256(("cert:" + label).encode("utf-8")).digest())


GATEWAY_CERT = thumbprint("gateway-primary")
SECONDARY_GATEWAY_CERT = thumbprint("gateway-secondary")
OPS_TOOL_CERT = thumbprint("ops-tool")


@dataclass(frozen=True)
class Connection:
    """Transport facts the backend sees: network path and mTLS client cert."""

    network: str
    client_cert: str | None


GATEWAY_CONNECTION = Connection("gateway-private", GATEWAY_CERT)
SECONDARY_GATEWAY_CONNECTION = Connection("gateway-private", SECONDARY_GATEWAY_CERT)
OPS_TOOL_CONNECTION = Connection("ops-subnet", OPS_TOOL_CERT)
INTERNET = Connection("internet", None)


class PropagationError(Exception):
    """A gateway-side failure mapped to a coarse client-facing reason."""

    def __init__(self, reason: str, status: int, claims: str | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status
        self.claims = claims


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


@dataclass
class IdpResponse:
    status: int
    body: dict


# ---------------------------------------------------------------------------
# Fake identity provider (Entra-like)
# ---------------------------------------------------------------------------


class FakeIdentityProvider:
    """Issues and verifies HS256 tokens and performs on-behalf-of exchanges."""

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self._key = secrets.token_bytes(32)
        self.kid = "idp-" + secrets.token_hex(4)
        self.consents: set[tuple[str, str, str]] = set()
        self.federated_credentials: dict[str, set[str]] = {}
        self.app_role_assignments: dict[tuple[str, str], set[str]] = {}
        self.mfa_required: set[str] = set()
        self.blocked: set[str] = set()
        self.available = True
        self.exchange_calls = 0
        self.issued: list[str] = []

    @staticmethod
    def issuer(tid: str) -> str:
        return f"https://login.microsoftonline.com/{tid}/v2.0"

    def _mac(self, data: bytes) -> bytes:
        return hmac.new(self._key, data, hashlib.sha256).digest()

    def _sign(self, claims: dict) -> str:
        token = sign_jws({"alg": "HS256", "typ": "JWT", "kid": self.kid}, claims, self._mac)
        self.issued.append(token)
        return token

    def _base_claims(self, tid: str, oid: str, azp: str, audience: str, lifetime: int) -> dict:
        now = self.clock.now
        return {
            "iss": self.issuer(tid), "aud": audience, "tid": tid, "oid": oid,
            "sub": sha256_hex(oid + "|" + azp)[:43], "azp": azp,
            "iat": now, "nbf": now, "exp": now + lifetime,
            "ver": "2.0", "uti": secrets.token_hex(8),
        }

    def issue_user_token(self, tid: str, oid: str, client_id: str, audience: str, scope: str,
                         lifetime: int = IDP_TOKEN_LIFETIME, amr: tuple[str, ...] = ("pwd",)) -> str:
        claims = self._base_claims(tid, oid, client_id, audience, lifetime)
        claims.update({"idtyp": "user", "scp": scope, "amr": list(amr)})
        return self._sign(claims)

    def issue_app_token(self, tid: str, app_id: str, audience: str, roles: Any,
                        lifetime: int = IDP_TOKEN_LIFETIME, oid: str | None = None) -> str:
        claims = self._base_claims(tid, oid or app_id, app_id, audience, lifetime)
        claims.update({"idtyp": "app", "roles": sorted(roles)})
        return self._sign(claims)

    def issue_managed_identity_token(self, mi_id: str, audience: str) -> str:
        roles = self.app_role_assignments.get((mi_id, audience), set())
        return self.issue_app_token(CONTOSO_TID, mi_id, audience, roles, oid=mi_id)

    def verify(self, token: str, audience: str) -> dict:
        header, claims, signing_input, signature = split_jwt(token)
        if header.get("alg") != "HS256" or header.get("kid") != self.kid:
            raise TokenError("unknown_key")
        if not hmac.compare_digest(self._mac(signing_input), signature):
            raise TokenError("bad_signature")
        tid = claims.get("tid")
        if not isinstance(tid, str) or claims.get("iss") != self.issuer(tid):
            raise TokenError("bad_issuer")
        if claims.get("aud") != audience:
            raise TokenError("wrong_audience")
        nbf, exp = claims.get("nbf"), claims.get("exp")
        if not isinstance(nbf, int) or not isinstance(exp, int):
            raise TokenError("malformed")
        now = self.clock.now
        if nbf > now + CLOCK_SKEW:
            raise TokenError("not_yet_valid")
        if exp <= now - CLOCK_SKEW:
            raise TokenError("expired")
        return claims

    @staticmethod
    def _error(status: int, error: str, description: str, codes: tuple[int, ...] = (),
               **extra: Any) -> IdpResponse:
        body: dict[str, Any] = {"error": error, "error_description": "(sim) " + description}
        if codes:
            body["error_codes"] = list(codes)
        body.update(extra)
        return IdpResponse(status, body)

    def exchange_on_behalf_of(self, tid: str, form: dict[str, str]) -> IdpResponse:
        """Token endpoint for the jwt-bearer on_behalf_of grant."""
        self.exchange_calls += 1
        if not self.available:
            return self._error(503, "temporarily_unavailable", "token service unavailable")
        client_id = form.get("client_id", "")
        if "client_secret" in form:
            return self._error(401, "invalid_client", "client secrets are not accepted for this app")
        if form.get("client_assertion_type") != CLIENT_ASSERTION_TYPE:
            return self._error(401, "invalid_client", "client assertion required")
        try:
            client = self.verify(form.get("client_assertion", ""), TOKEN_EXCHANGE_AUDIENCE)
        except TokenError:
            return self._error(401, "invalid_client", "client assertion rejected")
        if client.get("oid") not in self.federated_credentials.get(client_id, set()):
            return self._error(401, "invalid_client", "no federated credential matches the assertion")
        if form.get("grant_type") != JWT_BEARER_GRANT or form.get("requested_token_use") != "on_behalf_of":
            return self._error(400, "unsupported_grant_type", "expected jwt-bearer on_behalf_of")
        try:
            user = self.verify(form.get("assertion", ""), client_id)
        except TokenError:
            return self._error(400, "invalid_grant", "user assertion rejected")
        if user.get("idtyp") != "user" or user.get("tid") != tid:
            return self._error(400, "invalid_grant", "assertion is not a user token for this tenant")
        oid = user["oid"]
        if oid in self.blocked:
            return self._error(400, "invalid_grant", "blocked by Conditional Access", codes=(53003,))
        scopes = form.get("scope", "").split()
        resources = {s.rsplit("/", 1)[0] for s in scopes}
        if not scopes or len(resources) != 1:
            return self._error(400, "invalid_scope", "scopes must target exactly one resource")
        if any((tid, client_id, s) not in self.consents for s in scopes):
            return self._error(400, "invalid_grant", "consent required", codes=(65001,))
        if oid in self.mfa_required and "mfa" not in user.get("amr", []):
            return self._error(400, "interaction_required", "multi-factor authentication required",
                               codes=(50076,), claims=MFA_CLAIMS)
        resource = resources.pop()
        claims = self._base_claims(tid, oid, client_id, resource, IDP_TOKEN_LIFETIME)
        claims.update({
            "idtyp": "user",
            "scp": " ".join(s.rsplit("/", 1)[1] for s in scopes),
            "amr": list(user.get("amr", [])),
        })
        return IdpResponse(200, {
            "token_type": "Bearer", "scope": " ".join(scopes),
            "expires_in": IDP_TOKEN_LIFETIME, "access_token": self._sign(claims),
        })


# ---------------------------------------------------------------------------
# Option A: on-behalf-of exchanger with a per-user cache
# ---------------------------------------------------------------------------

OBO_ERROR_CODE_MAP = {
    65001: ("consent_required", 403),
    50076: ("interaction_required", 401),
    50079: ("interaction_required", 401),
    53003: ("access_blocked", 403),
}


def map_obo_error(response: IdpResponse) -> PropagationError:
    """Map token endpoint errors to coarse reasons; never leak descriptions."""
    body = response.body
    for code in body.get("error_codes", []):
        if code in OBO_ERROR_CODE_MAP:
            reason, status = OBO_ERROR_CODE_MAP[code]
            claims = body.get("claims") if reason == "interaction_required" else None
            return PropagationError(reason, status, claims)
    if body.get("error") == "interaction_required":
        return PropagationError("interaction_required", 401, body.get("claims"))
    if response.status >= 500 or body.get("error") == "temporarily_unavailable":
        return PropagationError("temporarily_unavailable", 503)
    return PropagationError("backend_auth_failed", 502)


class OnBehalfOfExchanger:
    def __init__(self, idp: FakeIdentityProvider, clock: FakeClock, client_id: str, mi_id: str) -> None:
        self.idp = idp
        self.clock = clock
        self.client_id = client_id
        self.mi_id = mi_id
        self._cache: dict[str, tuple[str, int]] = {}
        self.last_form_fields: list[str] = []

    @staticmethod
    def cache_key(tid: str, oid: str, audience: str, scopes: list[str]) -> str:
        return sha256_hex("|".join([tid, oid, audience, " ".join(sorted(scopes))]))

    def get_token(self, inbound_token: str, tid: str, oid: str, audience: str,
                  scopes: list[str]) -> tuple[str, bool]:
        key = self.cache_key(tid, oid, audience, scopes)
        entry = self._cache.get(key)
        if entry and entry[1] > self.clock.now:
            return entry[0], True
        form = {
            "grant_type": JWT_BEARER_GRANT,
            "requested_token_use": "on_behalf_of",
            "client_id": self.client_id,
            "client_assertion_type": CLIENT_ASSERTION_TYPE,
            "client_assertion": self.idp.issue_managed_identity_token(self.mi_id, TOKEN_EXCHANGE_AUDIENCE),
            "assertion": inbound_token,
            "scope": " ".join(scopes),
        }
        self.last_form_fields = sorted(form)
        response = self.idp.exchange_on_behalf_of(tid, form)
        if response.status != 200:
            raise map_obo_error(response)
        token = response.body["access_token"]
        self._cache[key] = (token, self.clock.now + int(response.body["expires_in"]) - OBO_EXPIRY_SKEW)
        return token, False

    def cached_keys(self) -> list[str]:
        return list(self._cache)

    def cached_tokens(self) -> list[str]:
        return [token for token, _ in self._cache.values()]


# ---------------------------------------------------------------------------
# Option B (and C): identity links
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityLink:
    issuer: str
    tid: str
    oid: str
    backend_tenant: str
    backend_user: str
    status: str = "active"
    source: str = "admin"


class LinkConflict(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class LinkStoreUnavailable(Exception):
    pass


class InMemoryIdentityLinkStore:
    """Identity link table: (issuer, tid, oid) -> (backend tenant, backend user)."""

    def __init__(self, entitlements: dict[str, set[str]]) -> None:
        self.entitlements = entitlements
        self.links: list[IdentityLink] = []
        self.events: list[dict] = []
        self.available = True
        self.lookups = 0
        self.subscribers: list[Callable[[dict], None]] = []

    def _emit(self, event_type: str, link: IdentityLink, actor: str) -> None:
        event = {
            "type": event_type, "issuer": link.issuer, "tid": link.tid, "oid": link.oid,
            "backendTenant": link.backend_tenant, "backendUser": link.backend_user, "actor": actor,
        }
        self.events.append(event)
        for subscriber in self.subscribers:
            subscriber(event)

    def _active(self) -> list[IdentityLink]:
        return [link for link in self.links if link.status == "active"]

    def link(self, new: IdentityLink, actor: str) -> IdentityLink:
        reason = None
        if new.backend_tenant not in self.entitlements.get(new.tid, set()):
            reason = "tenant_not_entitled"
        elif any((l.issuer, l.tid, l.oid, l.backend_tenant) == (new.issuer, new.tid, new.oid, new.backend_tenant)
                 for l in self._active()):
            reason = "identity_already_linked"
        elif any((l.backend_tenant, l.backend_user) == (new.backend_tenant, new.backend_user)
                 for l in self._active()):
            reason = "backend_account_already_linked"
        if reason:
            self._emit("link.conflict", new, actor)
            raise LinkConflict(reason)
        self.links.append(new)
        self._emit("link.created", new, actor)
        return new

    def insert_unchecked(self, link: IdentityLink) -> None:
        """TEST ONLY: models a legacy row that bypassed entitlement checks."""
        self.links.append(link)

    def revoke(self, issuer: str, tid: str, oid: str, backend_tenant: str, actor: str) -> None:
        for index, link in enumerate(self.links):
            if (link.issuer, link.tid, link.oid, link.backend_tenant, link.status) == (
                    issuer, tid, oid, backend_tenant, "active"):
                self.links[index] = replace(link, status="revoked")
                self._emit("link.revoked", link, actor)

    def resolve(self, issuer: str, tid: str, oid: str, backend_tenant: str) -> IdentityLink | None:
        if not self.available:
            raise LinkStoreUnavailable()
        self.lookups += 1
        for link in self._active():
            if (link.issuer, link.tid, link.oid, link.backend_tenant) == (issuer, tid, oid, backend_tenant):
                return link
        return None


class CachedLinkResolver:
    """Short positive and shorter negative cache, invalidated by link events."""

    def __init__(self, store: InMemoryIdentityLinkStore, clock: FakeClock) -> None:
        self.store = store
        self.clock = clock
        self._cache: dict[tuple[str, str, str, str], tuple[IdentityLink | None, int]] = {}
        store.subscribers.append(self._on_event)

    def _on_event(self, event: dict) -> None:
        if event["type"] in ("link.created", "link.revoked"):
            self._cache.pop((event["issuer"], event["tid"], event["oid"], event["backendTenant"]), None)

    def resolve(self, issuer: str, tid: str, oid: str, backend_tenant: str) -> tuple[IdentityLink | None, str]:
        key = (issuer, tid, oid, backend_tenant)
        entry = self._cache.get(key)
        if entry and entry[1] > self.clock.now:
            return entry[0], "cache"
        try:
            link = self.store.resolve(issuer, tid, oid, backend_tenant)
        except LinkStoreUnavailable:
            raise PropagationError("temporarily_unavailable", 503) from None
        ttl = LINK_CACHE_TTL if link else LINK_NEGATIVE_TTL
        self._cache[key] = (link, self.clock.now + ttl)
        return link, "store"


# ---------------------------------------------------------------------------
# Option B: signing keys, JWKS publication and assertion minting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VerifyOnlyKey:
    """What a backend gets from the JWKS: a kid, an alg and a verify function."""

    kid: str
    alg: str
    verify: Callable[[bytes, bytes], bool]


class SimulatedSigningKey:
    """TEST ONLY: HMAC stands in for an asymmetric Key Vault or HSM key."""

    def __init__(self, kid: str, alg: str = "ES256") -> None:
        self.kid = kid
        self.alg = alg
        self._secret = secrets.token_bytes(32)
        self.sign_calls = 0

    def sign(self, data: bytes) -> bytes:
        self.sign_calls += 1
        return hmac.new(self._secret, data, hashlib.sha256).digest()

    def verify(self, data: bytes, signature: bytes) -> bool:
        expected = hmac.new(self._secret, data, hashlib.sha256).digest()
        return hmac.compare_digest(expected, signature)

    def public_view(self) -> VerifyOnlyKey:
        return VerifyOnlyKey(self.kid, self.alg, self.verify)


class JwksPublisher:
    def __init__(self) -> None:
        self._keys: dict[str, VerifyOnlyKey] = {}
        self.fetches = 0

    def publish(self, key: SimulatedSigningKey) -> None:
        self._keys[key.kid] = key.public_view()

    def withdraw(self, kid: str) -> None:
        self._keys.pop(kid, None)

    def fetch(self) -> dict[str, VerifyOnlyKey]:
        self.fetches += 1
        return dict(self._keys)

    def document(self) -> dict:
        return {"keys": [
            {"kid": k.kid, "alg": k.alg, "use": "sig", "kty": "EC" if k.alg.startswith("ES") else "RSA"}
            for k in self._keys.values()
        ]}


class AssertionMinter:
    def __init__(self, signer: SimulatedSigningKey, clock: FakeClock) -> None:
        self.signer = signer
        self.clock = clock

    def mint(self, sub: str, aud: str, tenant_id: str, scope: str, req_id: str, cnf_thumbprint: str,
             actor: str | None = None, lifetime: int = ASSERTION_LIFETIME,
             extra: dict | None = None) -> tuple[str, dict]:
        now = self.clock.now
        claims: dict[str, Any] = {
            "iss": ASSERTION_ISSUER, "sub": sub, "aud": aud, "tenant_id": tenant_id,
            "scope": scope, "jti": secrets.token_hex(12),
            "iat": now, "nbf": now, "exp": now + lifetime,
            "req_id": req_id, "cnf": {"x5t#S256": cnf_thumbprint},
        }
        if actor:
            claims["act"] = {"sub": actor}
        if extra:
            claims.update(extra)
        header = {"alg": self.signer.alg, "typ": ASSERTION_TYP, "kid": self.signer.kid}
        return sign_jws(header, claims, self.signer.sign), claims


# ---------------------------------------------------------------------------
# Contoso ERP backends (each enforces its own authorization)
# ---------------------------------------------------------------------------


@dataclass
class BackendRequest:
    method: str
    path: str
    headers: list[tuple[str, str]]
    body: dict
    connection: Connection

    def header_values(self, name: str) -> list[str]:
        return [v for k, v in self.headers if k.lower() == name.lower()]

    def header(self, name: str) -> str | None:
        values = self.header_values(name)
        return values[0] if values else None

    @property
    def segments(self) -> list[str]:
        return [s for s in self.path.split("/") if s]

    @property
    def tenant(self) -> str:
        return self.segments[0] if self.segments else ""


@dataclass
class BackendResponse:
    status: int
    body: dict

    @property
    def reason(self) -> str | None:
        return self.body.get("error")


class BackendBase:
    name = "backend"

    def __init__(self, trusted_certs: set[str] | None = None) -> None:
        self.trusted_certs = trusted_certs or set()
        self.received: list[BackendRequest] = []
        self.log: list[dict] = []
        self.alerts: list[dict] = []

    def handle(self, request: BackendRequest) -> BackendResponse:
        self.received.append(request)
        return self._handle(request)

    def _handle(self, request: BackendRequest) -> BackendResponse:
        raise NotImplementedError

    @staticmethod
    def _deny(status: int, reason: str) -> BackendResponse:
        return BackendResponse(status, {"error": reason})

    def _from_gateway(self, request: BackendRequest) -> bool:
        conn = request.connection
        return conn.network == "gateway-private" and conn.client_cert in self.trusted_certs

    def _reject_untrusted(self, request: BackendRequest, identity_header: str) -> BackendResponse:
        self.alerts.append({
            "backend": self.name, "reason": "not_from_gateway",
            "network": request.connection.network,
            "identityHeaderPresent": request.header(identity_header) is not None,
        })
        return self._deny(403, "not_from_gateway")


def bearer(request: BackendRequest) -> str | None:
    value = request.header("Authorization") or ""
    return value[7:] if value.startswith("Bearer ") else None


class ReportingBackend(BackendBase):
    """Option A target: trusts Entra tokens issued for its own audience."""

    name = "reporting"

    def __init__(self, idp: FakeIdentityProvider, tenant_by_tid: dict[str, str],
                 reports: dict[tuple[str, str], list[str]]) -> None:
        super().__init__()
        self.idp = idp
        self.tenant_by_tid = tenant_by_tid
        self.reports = reports

    def _handle(self, request: BackendRequest) -> BackendResponse:
        token = bearer(request)
        if not token:
            return self._deny(401, "missing_token")
        try:
            claims = self.idp.verify(token, REPORTING_AUD)
        except TokenError as exc:
            return self._deny(401, exc.reason)
        if "Reports.Read" not in str(claims.get("scp", "")).split():
            return self._deny(403, "insufficient_scope")
        if self.tenant_by_tid.get(claims["tid"]) != request.tenant:
            return self._deny(403, "wrong_instance")
        self.log.append({"oid": claims["oid"], "azp": claims["azp"], "reqId": request.header("X-Request-Id")})
        return BackendResponse(200, {"reports": self.reports.get((request.tenant, claims["oid"]), [])})


class FieldServiceBackend(BackendBase):
    """Option B target: own user store, verifies gateway-signed assertions."""

    name = "field-service"

    def __init__(self, jwks: JwksPublisher, clock: FakeClock, trusted_certs: set[str],
                 local_users: dict[str, set[str]], work_orders: dict[str, list[dict]]) -> None:
        super().__init__(trusted_certs)
        self.jwks = jwks
        self.clock = clock
        self.local_users = local_users
        self.work_orders = work_orders
        self._keys: dict[str, VerifyOnlyKey] = {}
        self._fetched_at: int | None = None
        self._last_forced: int | None = None
        self.jwks_refreshes = 0
        self._seen_jti: dict[str, int] = {}

    def _refresh(self) -> None:
        self._keys = self.jwks.fetch()
        self._fetched_at = self.clock.now
        self.jwks_refreshes += 1

    def _key_for(self, kid: Any) -> VerifyOnlyKey | None:
        now = self.clock.now
        if self._fetched_at is None or now - self._fetched_at >= JWKS_TTL:
            self._refresh()
        if kid not in self._keys and (
                self._last_forced is None or now - self._last_forced >= JWKS_FORCED_REFRESH_INTERVAL):
            self._last_forced = now
            self._refresh()
        return self._keys.get(kid)

    def _verify(self, token: str, request: BackendRequest) -> dict:
        header, claims, signing_input, signature = split_jwt(token)
        if header.get("alg") not in ALLOWED_ASSERTION_ALGS:
            raise TokenError("alg_not_allowed")
        key = self._key_for(header.get("kid"))
        if key is None:
            raise TokenError("unknown_kid")
        if key.alg != header.get("alg") or not key.verify(signing_input, signature):
            raise TokenError("bad_signature")
        if claims.get("iss") != ASSERTION_ISSUER:
            raise TokenError("bad_issuer")
        if claims.get("aud") != FIELD_SERVICE_AUD:
            raise TokenError("wrong_audience")
        if header.get("typ") != ASSERTION_TYP:
            raise TokenError("wrong_typ")
        iat, nbf, exp, jti = claims.get("iat"), claims.get("nbf"), claims.get("exp"), claims.get("jti")
        if not all(isinstance(v, int) for v in (iat, nbf, exp)) or not isinstance(jti, str) or not jti:
            raise TokenError("malformed")
        now = self.clock.now
        if nbf > now + CLOCK_SKEW:
            raise TokenError("not_yet_valid")
        if exp <= now - CLOCK_SKEW:
            raise TokenError("expired")
        if exp - iat > ASSERTION_MAX_LIFETIME:
            raise TokenError("lifetime_too_long")
        self._seen_jti = {j: until for j, until in self._seen_jti.items() if until > now}
        if jti in self._seen_jti:
            raise TokenError("replayed")
        self._seen_jti[jti] = exp + CLOCK_SKEW
        if claims.get("tenant_id") != request.tenant:
            raise TokenError("wrong_instance")
        if claims.get("sub") not in self.local_users.get(request.tenant, set()):
            raise TokenError("unknown_local_user")
        cnf = claims.get("cnf")
        if not isinstance(cnf, dict) or cnf.get("x5t#S256") != request.connection.client_cert:
            raise TokenError("cnf_mismatch")
        if "work-orders.read" not in str(claims.get("scope", "")).split():
            raise TokenError("insufficient_scope")
        return claims

    def _handle(self, request: BackendRequest) -> BackendResponse:
        if not self._from_gateway(request):
            return self._reject_untrusted(request, "X-User-Assertion")
        values = request.header_values("X-User-Assertion")
        if not values:
            return self._deny(401, "missing_assertion")
        if len(values) > 1:
            return self._deny(401, "duplicate_assertion")
        try:
            claims = self._verify(values[0], request)
        except TokenError as exc:
            status = 403 if exc.reason in ("wrong_instance", "unknown_local_user", "insufficient_scope") else 401
            return self._deny(status, exc.reason)
        status_filter = request.body.get("status")
        rows = sorted(
            (wo for wo in self.work_orders.get(request.tenant, [])
             if wo["assignedTo"] == claims["sub"] and (status_filter is None or wo["status"] == status_filter)),
            key=lambda wo: wo["id"],
        )
        act = claims.get("act")
        self.log.append({
            "jti": claims["jti"], "sub": claims["sub"],
            "act": act.get("sub") if isinstance(act, dict) else None,
            "reqId": claims.get("req_id"), "headerReqId": request.header("X-Request-Id"),
        })
        return BackendResponse(200, {"workOrders": [{"id": wo["id"], "status": wo["status"]} for wo in rows]})


class OrdersBackend(BackendBase):
    """Option C target: per-tenant API keys plus a gateway-asserted acting user."""

    name = "orders"

    def __init__(self, trusted_certs: set[str], keys: dict[str, str] | None = None,
                 orders: dict[str, dict[str, str]] | None = None) -> None:
        super().__init__(trusted_certs)
        self.keys = keys or {}
        self.orders = orders or {}

    def rotate_key(self, tenant: str) -> str:
        self.keys[tenant] = "sim-key-" + secrets.token_hex(16)
        return self.keys[tenant]

    def _handle(self, request: BackendRequest) -> BackendResponse:
        if not self._from_gateway(request):
            return self._reject_untrusted(request, "X-Acting-User")
        expected = self.keys.get(request.tenant, "")
        presented = request.header("X-Api-Key") or ""
        if not expected or not hmac.compare_digest(expected.encode(), presented.encode()):
            return self._deny(401, "invalid_api_key")
        acting_user = request.header("X-Acting-User")
        if not acting_user:
            return self._deny(400, "missing_acting_user")
        segments = request.segments
        order_id = segments[5] if len(segments) > 5 else ""
        self.log.append({"actingUser": acting_user, "orderId": order_id, "reqId": request.header("X-Request-Id")})
        status = self.orders.get(request.tenant, {}).get(order_id)
        if status is None:
            return self._deny(404, "not_found")
        return BackendResponse(200, {"orderId": order_id, "status": status})


class ReferenceBackend(BackendBase):
    """App-only target: tenant-neutral reference data behind an app role."""

    name = "orders-reference"

    def __init__(self, idp: FakeIdentityProvider, trusted_apps: set[str], order_types: list[str]) -> None:
        super().__init__()
        self.idp = idp
        self.trusted_apps = trusted_apps
        self.order_types = order_types

    def _handle(self, request: BackendRequest) -> BackendResponse:
        token = bearer(request)
        if not token:
            return self._deny(401, "missing_token")
        try:
            claims = self.idp.verify(token, ORDERS_AUD)
        except TokenError as exc:
            return self._deny(401, exc.reason)
        if claims.get("idtyp") != "app" or REFERENCE_ROLE not in claims.get("roles", []):
            return self._deny(403, "insufficient_role")
        if claims.get("azp") not in self.trusted_apps:
            return self._deny(403, "untrusted_app")
        self.log.append({
            "azp": claims["azp"], "tenantHeader": request.header("X-Contoso-Gateway-Tenant"),
            "hasUserClaims": "scp" in claims, "body": dict(request.body),
        })
        return BackendResponse(200, {"orderTypes": list(self.order_types)})


# ---------------------------------------------------------------------------
# Option C: Key Vault per-tenant secrets
# ---------------------------------------------------------------------------


class FakeKeyVault:
    SECRET_NAME = re.compile(r"[0-9A-Za-z-]{1,127}")

    def __init__(self, readers: set[str]) -> None:
        self.readers = readers
        self._secrets: dict[str, str] = {}
        self.reads: list[tuple[str, str, str]] = []

    def set_secret(self, name: str, value: str) -> None:
        if not self.SECRET_NAME.fullmatch(name):
            raise ValueError("invalid secret name")
        self._secrets[name] = value

    def get_secret(self, name: str, identity: str) -> str:
        if identity not in self.readers:
            self.reads.append((identity, name, "forbidden"))
            raise PermissionError(name)
        if name not in self._secrets:
            self.reads.append((identity, name, "not_found"))
            raise KeyError(name)
        self.reads.append((identity, name, "ok"))
        return self._secrets[name]

    def secret_values(self) -> list[str]:
        return list(self._secrets.values())


class TenantKeyResolver:
    TENANT_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")

    def __init__(self, vault: FakeKeyVault, identity: str, clock: FakeClock) -> None:
        self.vault = vault
        self.identity = identity
        self.clock = clock
        self._cache: dict[tuple[str, str], tuple[str, int]] = {}

    def get(self, api: str, tenant: str, template: str) -> tuple[str, bool]:
        if not self.TENANT_SLUG.fullmatch(tenant):
            raise PropagationError("unknown_tenant", 403)
        entry = self._cache.get((api, tenant))
        if entry and entry[1] > self.clock.now:
            return entry[0], True
        try:
            key = self.vault.get_secret(template.format(tenantId=tenant), self.identity)
        except (PermissionError, KeyError):
            raise PropagationError("backend_auth_failed", 502) from None
        self._cache[(api, tenant)] = (key, self.clock.now + TENANT_KEY_TTL)
        return key, False

    def invalidate(self, api: str, tenant: str) -> None:
        self._cache.pop((api, tenant), None)


# ---------------------------------------------------------------------------
# Gateway configuration (mirrors the governed config in architecture.md)
# ---------------------------------------------------------------------------

BACKEND_CONFIG: dict[str, dict[str, Any]] = {
    "reporting": {"option": "obo", "audience": REPORTING_AUD, "scopes": ["Reports.Read"]},
    "field-service": {"option": "signed-assertion", "audience": FIELD_SERVICE_AUD, "transport": "mtls",
                      "scope": "work-orders.read"},
    "orders": {"option": "tenant-bridge", "secretName": ORDERS_SECRET_TEMPLATE, "sunset": "2027-06-30",
               "toolsAllowed": ["get_order_status"]},
    "orders-reference": {"option": "app-only", "audience": ORDERS_AUD, "appRole": REFERENCE_ROLE},
}

TOOL_PARAMETERS: dict[tuple[str, str], tuple[str, ...]] = {
    ("reporting", "list_my_reports"): (),
    ("field-service", "search_work_orders"): ("status",),
    ("orders", "get_order_status"): ("order_id",),
    ("orders", "cancel_order"): ("order_id",),
    ("orders-reference", "list_order_types"): (),
}

BACKEND_ROUTES: dict[str, tuple[str, str]] = {
    "list_my_reports": ("GET", "/{tenant}/api/reporting/v1/reports"),
    "search_work_orders": ("POST", "/{tenant}/api/field-service/v1/work-orders/search"),
    "get_order_status": ("GET", "/{tenant}/api/orders/v2/orders/{order_id}/status"),
    "cancel_order": ("POST", "/{tenant}/api/orders/v2/orders/{order_id}/cancel"),
    "list_order_types": ("GET", "/api/orders/v2/reference/order-types"),
}

ORDER_ID = re.compile(r"[A-Z]{2}-[0-9]{1,8}")
WORK_ORDER_STATUSES = {"open", "closed", "scheduled"}

# Identity and credential headers a client must never be able to set.
STRIPPED_HEADERS = {
    "authorization", "x-user-assertion", "x-acting-user", "x-api-key",
    "x-contoso-user", "x-contoso-tenant", "x-contoso-gateway-tenant",
}
# Correlation headers the gateway owns and always sets itself.
GATEWAY_OWNED_HEADERS = {"x-request-id", "traceparent"}


def validate_arguments(api: str, tool: str, args: dict, audit: dict) -> dict:
    declared = TOOL_PARAMETERS[(api, tool)]
    dropped = sorted(k for k in args if k not in declared)
    if dropped:
        audit["droppedArgs"] = dropped
    clean = {k: v for k, v in args.items() if k in declared}
    if "order_id" in declared:
        order_id = clean.get("order_id")
        if not isinstance(order_id, str) or not ORDER_ID.fullmatch(order_id):
            raise PropagationError("invalid_params", 400)
    if "status" in clean and clean["status"] not in WORK_ORDER_STATUSES:
        raise PropagationError("invalid_params", 400)
    return clean


def _route(tool: str, tenant: str, args: dict) -> tuple[str, str, dict]:
    method, template = BACKEND_ROUTES[tool]
    path = template.replace("{tenant}", tenant)
    body = {}
    for name, value in args.items():
        placeholder = "{" + name + "}"
        if placeholder in path:
            path = path.replace(placeholder, value)
        else:
            body[name] = value
    return method, path, body


# ---------------------------------------------------------------------------
# Gateway
# ---------------------------------------------------------------------------


@dataclass
class McpCall:
    api: str
    tool: str
    token: str
    arguments: dict = field(default_factory=dict)
    headers: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class GatewayResult:
    status: int
    result: dict | None
    error: dict | None
    headers: dict[str, str]
    request_id: str

    @property
    def ok(self) -> bool:
        return self.status == 200

    @property
    def reason(self) -> str | None:
        return self.error["data"]["reason"] if self.error else None


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" or "agent"
    tid: str
    oid: str
    azp: str
    issuer: str
    tenant: str
    cell: str


@dataclass
class Dispatch:
    call: McpCall
    principal: Principal
    config: dict
    method: str
    path: str
    body: dict
    headers: list[tuple[str, str]]
    audit: dict
    actor: str | None
    request_id: str


ERROR_MESSAGES = {
    400: "Invalid params", 401: "Unauthorized", 403: "Forbidden", 404: "Not found",
    502: "Bad gateway", 503: "Service unavailable",
}


class Gateway:
    def __init__(self, clock: FakeClock, idp: FakeIdentityProvider, tenant_directory: dict[str, tuple[str, str]],
                 obo: OnBehalfOfExchanger, link_resolver: CachedLinkResolver, minter: AssertionMinter,
                 key_resolver: TenantKeyResolver, backends: dict[str, BackendBase],
                 connection: Connection = GATEWAY_CONNECTION, mi_id: str = GATEWAY_MI_ID) -> None:
        self.clock = clock
        self.idp = idp
        self.tenant_directory = tenant_directory
        self.obo = obo
        self.link_resolver = link_resolver
        self.minter = minter
        self.key_resolver = key_resolver
        self.backends = backends
        self.connection = connection
        self.mi_id = mi_id
        self.audit: list[dict] = []
        self._counter = 0

    def call(self, call: McpCall) -> GatewayResult:
        self._counter += 1
        request_id = "req-%04d" % self._counter
        traceparent = "00-%s-%s-01" % (sha256_hex("trace|" + request_id)[:32], sha256_hex("span|" + request_id)[:16])
        config = BACKEND_CONFIG.get(call.api)
        audit: dict[str, Any] = {
            "requestId": request_id, "time": self.clock.now, "api": call.api, "tool": call.tool,
            "mode": config["option"] if config else None, "tid": None, "oid": None, "clientAppId": None,
            "tenant": None, "cell": None, "principalKind": None, "backendUserId": None, "actor": None,
            "outcome": None, "reason": None, "backendStatus": None, "backendReason": None,
        }
        self.audit.append(audit)
        try:
            principal = self._authenticate(call.token)
            actor = f"client:{principal.azp}" if principal.kind == "user" else None
            audit.update(tid=principal.tid, oid=principal.oid, clientAppId=principal.azp, tenant=principal.tenant,
                         cell=principal.cell, principalKind=principal.kind, actor=actor)
            if config is None or (call.api, call.tool) not in TOOL_PARAMETERS:
                raise PropagationError("unknown_tool", 404)
            args = validate_arguments(call.api, call.tool, call.arguments, audit)
            method, path, body = _route(call.tool, principal.tenant, args)
            headers = [(k, v) for k, v in call.headers
                       if k.lower() not in STRIPPED_HEADERS and k.lower() not in GATEWAY_OWNED_HEADERS]
            headers += [("X-Request-Id", request_id), ("traceparent", traceparent)]
            handler = {
                "obo": self._obo, "signed-assertion": self._assertion,
                "tenant-bridge": self._bridge, "app-only": self._app_only,
            }[config["option"]]
            response = handler(Dispatch(call, principal, config, method, path, body, headers, audit, actor, request_id))
            audit["backendStatus"] = response.status
            if response.status != 200:
                audit["backendReason"] = response.reason
                if response.status == 401:
                    raise PropagationError("backend_auth_failed", 502)
                if response.status >= 500:
                    raise PropagationError("temporarily_unavailable", 503)
                raise PropagationError("backend_denied", 403)
            audit["outcome"] = "ok"
            return GatewayResult(200, response.body, None, {"X-Request-Id": request_id}, request_id)
        except PropagationError as exc:
            audit.update(outcome="denied", reason=exc.reason)
            return self._error(exc, request_id)

    def _authenticate(self, token: str) -> Principal:
        try:
            claims = self.idp.verify(token, GATEWAY_APP_ID)
        except TokenError:
            raise PropagationError("invalid_token", 401) from None
        if claims.get("idtyp") == "app":
            if AGENT_ROLE not in claims.get("roles", []):
                raise PropagationError("insufficient_scope", 403)
            kind = "agent"
        else:
            if GATEWAY_SCOPE not in str(claims.get("scp", "")).split():
                raise PropagationError("insufficient_scope", 403)
            kind = "user"
        if claims["tid"] not in self.tenant_directory:
            raise PropagationError("unknown_tenant", 403)
        tenant, cell = self.tenant_directory[claims["tid"]]
        return Principal(kind, claims["tid"], claims["oid"], claims["azp"], claims["iss"], tenant, cell)

    @staticmethod
    def _error(exc: PropagationError, request_id: str) -> GatewayResult:
        code = -32602 if exc.reason in ("unknown_tool", "invalid_params") else -32001
        data: dict[str, Any] = {"reason": exc.reason, "requestId": request_id}
        if exc.reason == "identity_not_linked":
            data["linkUrl"] = LINK_URL
        headers = {"X-Request-Id": request_id}
        if exc.reason == "interaction_required" and exc.claims:
            encoded = base64.b64encode(exc.claims.encode("utf-8")).decode("ascii")
            headers["WWW-Authenticate"] = f'Bearer error="insufficient_claims", claims="{encoded}"'
        elif exc.reason == "invalid_token":
            headers["WWW-Authenticate"] = 'Bearer error="invalid_token"'
        error = {"code": code, "message": ERROR_MESSAGES.get(exc.status, "Error"), "data": data}
        return GatewayResult(exc.status, None, error, headers, request_id)

    def _send(self, d: Dispatch, headers: list[tuple[str, str]]) -> BackendResponse:
        request = BackendRequest(d.method, d.path, headers, dict(d.body), self.connection)
        return self.backends[d.call.api].handle(request)

    def _obo(self, d: Dispatch) -> BackendResponse:
        audience = d.config["audience"]
        scopes = [f"{audience}/{s}" for s in d.config["scopes"]]
        token, hit = self.obo.get_token(d.call.token, d.principal.tid, d.principal.oid, audience, scopes)
        d.audit.update(credential="obo", cacheHit=hit, tokenHashPrefix=sha256_hex(token)[:8],
                       backendUserId=d.principal.oid)
        return self._send(d, d.headers + [("Authorization", "Bearer " + token)])

    def _assertion(self, d: Dispatch) -> BackendResponse:
        p = d.principal
        link, source = self.link_resolver.resolve(p.issuer, p.tid, p.oid, p.tenant)
        d.audit.update(credential="signed-assertion", linkSource=source, linkCacheHit=source == "cache")
        if link is None or link.backend_tenant != p.tenant:
            raise PropagationError("identity_not_linked", 403)
        token, claims = self.minter.mint(
            sub=link.backend_user, aud=d.config["audience"], tenant_id=p.tenant, scope=d.config["scope"],
            req_id=d.request_id, cnf_thumbprint=self.connection.client_cert or "", actor=d.actor)
        d.audit.update(backendUserId=link.backend_user, jti=claims["jti"], kid=self.minter.signer.kid)
        return self._send(d, d.headers + [("X-User-Assertion", token)])

    def _bridge(self, d: Dispatch) -> BackendResponse:
        p = d.principal
        d.audit["credential"] = "tenant-key"
        if d.call.tool not in d.config["toolsAllowed"]:
            raise PropagationError("not_entitled", 403)
        link, source = self.link_resolver.resolve(p.issuer, p.tid, p.oid, p.tenant)
        acting_user = link.backend_user if link else f"entra:{p.oid}"
        d.audit.update(linkSource=source, backendUserId=acting_user)
        attempts, refreshed = 0, False
        while True:
            attempts += 1
            key, hit = self.key_resolver.get(d.call.api, p.tenant, d.config["secretName"])
            if attempts == 1:
                d.audit["keyCacheHit"] = hit
            response = self._send(d, d.headers + [("X-Api-Key", key), ("X-Acting-User", acting_user)])
            if response.status == 401 and hit and attempts == 1:
                self.key_resolver.invalidate(d.call.api, p.tenant)
                refreshed = True
                continue
            break
        d.audit.update(attempts=attempts, keyRefreshed=refreshed)
        return response

    def _app_only(self, d: Dispatch) -> BackendResponse:
        token = self.idp.issue_managed_identity_token(self.mi_id, d.config["audience"])
        d.audit.update(credential="app-only", backendUserId=None)
        return self._send(d, d.headers + [("Authorization", "Bearer " + token),
                                          ("X-Contoso-Gateway-Tenant", d.principal.tenant)])


def naive_passthrough(world: "World", api: str, token: str) -> BackendResponse:
    """ANTI-PATTERN: forwards the client's token unchanged. Used only to prove it fails."""
    if api == "reporting":
        request = BackendRequest("GET", "/fabrikam/api/reporting/v1/reports",
                                 [("Authorization", "Bearer " + token)], {}, GATEWAY_CONNECTION)
    else:
        request = BackendRequest("POST", "/fabrikam/api/field-service/v1/work-orders/search",
                                 [("X-User-Assertion", token)], {}, GATEWAY_CONNECTION)
    return world.backends[api].handle(request)


# ---------------------------------------------------------------------------
# Production seams and gated stubs
# ---------------------------------------------------------------------------


class TokenExchanger(Protocol):
    def get_token(self, inbound_token: str, tid: str, oid: str, audience: str,
                  scopes: list[str]) -> tuple[str, bool]: ...


class IdentityLinkResolver(Protocol):
    def resolve(self, issuer: str, tid: str, oid: str,
                backend_tenant: str) -> tuple[IdentityLink | None, str]: ...


class SecretStore(Protocol):
    def get_secret(self, name: str, identity: str) -> str: ...


class AssertionSigner(Protocol):
    kid: str
    alg: str

    def sign(self, data: bytes) -> bytes: ...


class EntraOnBehalfOfExchanger:
    """Gated stub for a real on-behalf-of exchange against Microsoft Entra ID.

    A real implementation would use a token library (for example MSAL) with a
    managed identity federated credential as the client assertion and a
    cache partitioned per user. See architecture.md sections 10.2 and 12.
    """

    def __init__(self, tenant_authority: str, client_id: str, *, enable_network: bool = False) -> None:
        if not enable_network:
            raise RuntimeError("EntraOnBehalfOfExchanger needs enable_network=True and real configuration")
        self.tenant_authority = tenant_authority
        self.client_id = client_id

    def get_token(self, inbound_token: str, tid: str, oid: str, audience: str,
                  scopes: list[str]) -> tuple[str, bool]:
        raise NotImplementedError("Implement with a token library; see architecture.md sections 10.2 and 12")


class KeyVaultAssertionSigner:
    """Gated stub for signing assertions with a non-exportable Key Vault key.

    The Key Vault sign operation takes a SHA-256 digest of the signing input.
    Verify in current documentation how ES256 signatures are encoded (R and S
    concatenated) before publishing the matching JWKS. See architecture.md 5.7.
    """

    def __init__(self, vault_uri: str, key_name: str, kid: str, alg: str = "ES256", *,
                 enable_network: bool = False) -> None:
        if not enable_network:
            raise RuntimeError("KeyVaultAssertionSigner needs enable_network=True and real configuration")
        self.vault_uri = vault_uri
        self.key_name = key_name
        self.kid = kid
        self.alg = alg

    def sign(self, data: bytes) -> bytes:
        raise NotImplementedError("Implement with the Key Vault keys client; see architecture.md section 5.7")


# ---------------------------------------------------------------------------
# Simulated world
# ---------------------------------------------------------------------------


@dataclass
class World:
    clock: FakeClock
    idp: FakeIdentityProvider
    links: InMemoryIdentityLinkStore
    link_resolver: CachedLinkResolver
    signer: SimulatedSigningKey
    jwks: JwksPublisher
    minter: AssertionMinter
    vault: FakeKeyVault
    key_resolver: TenantKeyResolver
    obo: OnBehalfOfExchanger
    backends: dict[str, BackendBase]
    gateway: Gateway

    def user_token(self, oid: str, tid: str = FABRIKAM_TID, client_id: str = MCP_CLIENT_ID,
                   audience: str = GATEWAY_APP_ID, scope: str = GATEWAY_SCOPE, lifetime: int = 3600,
                   amr: tuple[str, ...] = ("pwd",)) -> str:
        return self.idp.issue_user_token(tid, oid, client_id, audience, scope, lifetime, amr)

    def agent_token(self, roles: tuple[str, ...] = (AGENT_ROLE,), tid: str = FABRIKAM_TID) -> str:
        return self.idp.issue_app_token(tid, AGENT_APP_ID, GATEWAY_APP_ID, roles, oid=AGENT_OID)

    def call(self, api: str, tool: str, token: str, args: dict | None = None,
             headers: Any = ()) -> GatewayResult:
        return self.gateway.call(McpCall(api, tool, token, dict(args or {}), list(headers)))


def build_world() -> World:
    clock = FakeClock()
    idp = FakeIdentityProvider(clock)
    idp.consents.add((FABRIKAM_TID, GATEWAY_APP_ID, REPORTING_SCOPE))
    idp.federated_credentials[GATEWAY_APP_ID] = {GATEWAY_MI_ID}
    idp.app_role_assignments[(GATEWAY_MI_ID, ORDERS_AUD)] = {REFERENCE_ROLE}

    links = InMemoryIdentityLinkStore({FABRIKAM_TID: {"fabrikam"}, NORTHWIND_TID: {"northwind"}})
    for tid, oid, user, source, actor in [
        (FABRIKAM_TID, ALICE, "fs-1001", "scim", "scim-provisioner"),
        (FABRIKAM_TID, BOB, "fs-1002", "jit", "jit-link-flow"),
        (NORTHWIND_TID, CAROL, "nw-2001", "admin", "tenant-admin"),
        (FABRIKAM_TID, AGENT_OID, "svc-dispatch-agent", "admin", "tenant-admin"),
    ]:
        backend_tenant = TENANT_DIRECTORY[tid][0]
        links.link(IdentityLink(idp.issuer(tid), tid, oid, backend_tenant, user, source=source), actor)
    link_resolver = CachedLinkResolver(links, clock)

    signer = SimulatedSigningKey("gw-assert-2026-10")
    jwks = JwksPublisher()
    jwks.publish(signer)
    minter = AssertionMinter(signer, clock)

    reporting = ReportingBackend(idp, {tid: slug for tid, (slug, _) in TENANT_DIRECTORY.items()}, {
        ("fabrikam", ALICE): ["RPT-11", "RPT-12"],
        ("fabrikam", BOB): ["RPT-21"],
        ("northwind", CAROL): ["RPT-31"],
    })
    field_service = FieldServiceBackend(
        jwks, clock, {GATEWAY_CERT, SECONDARY_GATEWAY_CERT},
        {"fabrikam": {"fs-1001", "fs-1002", "fs-1003", "svc-dispatch-agent"}, "northwind": {"nw-2001"}},
        {
            "fabrikam": [
                {"id": "WO-501", "status": "open", "assignedTo": "fs-1001"},
                {"id": "WO-502", "status": "open", "assignedTo": "fs-1002"},
                {"id": "WO-503", "status": "scheduled", "assignedTo": "fs-1001"},
                {"id": "WO-504", "status": "open", "assignedTo": "fs-1003"},
                {"id": "WO-510", "status": "scheduled", "assignedTo": "svc-dispatch-agent"},
            ],
            "northwind": [{"id": "WO-901", "status": "open", "assignedTo": "nw-2001"}],
        },
    )
    orders = OrdersBackend({GATEWAY_CERT}, orders={"fabrikam": {"SO-1042": "shipped"},
                                                   "northwind": {"SO-2001": "open"}})
    vault = FakeKeyVault({GATEWAY_MI_ID})
    for slug, _ in TENANT_DIRECTORY.values():
        vault.set_secret(ORDERS_SECRET_TEMPLATE.format(tenantId=slug), orders.rotate_key(slug))
    reference = ReferenceBackend(idp, {GATEWAY_MI_ID}, ["standard", "rush", "return"])
    backends: dict[str, BackendBase] = {
        "reporting": reporting, "field-service": field_service,
        "orders": orders, "orders-reference": reference,
    }

    key_resolver = TenantKeyResolver(vault, GATEWAY_MI_ID, clock)
    obo = OnBehalfOfExchanger(idp, clock, GATEWAY_APP_ID, GATEWAY_MI_ID)
    gateway = Gateway(clock, idp, TENANT_DIRECTORY, obo, link_resolver, minter, key_resolver, backends)
    return World(clock, idp, links, link_resolver, signer, jwks, minter, vault, key_resolver, obo,
                 backends, gateway)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _mint(w: World, sub: str = "fs-1001", aud: str = FIELD_SERVICE_AUD, tenant: str = "fabrikam",
          minter: AssertionMinter | None = None, **kw: Any) -> str:
    token, _ = (minter or w.minter).mint(sub=sub, aud=aud, tenant_id=tenant, scope="work-orders.read",
                                         req_id="req-direct", cnf_thumbprint=GATEWAY_CERT, **kw)
    return token


def _send_assertion(w: World, token: str, connection: Connection = GATEWAY_CONNECTION,
                    tenant: str = "fabrikam", extra_headers: Any = ()) -> BackendResponse:
    headers = [("X-User-Assertion", token)] + list(extra_headers)
    request = BackendRequest("POST", f"/{tenant}/api/field-service/v1/work-orders/search", headers, {}, connection)
    return w.backends["field-service"].handle(request)


def _resign(w: World, token: str, header: dict | None = None, claims: dict | None = None,
            sign: Callable[[bytes], bytes] | None = None) -> str:
    old_header, old_claims, _, _ = split_jwt(token)
    new_header = dict(old_header, **(header or {}))
    new_claims = dict(old_claims, **(claims or {}))
    return sign_jws(new_header, new_claims, sign or w.signer.sign)


def _decode(token: str) -> tuple[dict, dict]:
    header, claims, _, _ = split_jwt(token)
    return header, claims


def _fs(w: World) -> FieldServiceBackend:
    backend = w.backends["field-service"]
    assert isinstance(backend, FieldServiceBackend)
    return backend


def _orders(w: World) -> OrdersBackend:
    backend = w.backends["orders"]
    assert isinstance(backend, OrdersBackend)
    return backend


def _wo_ids(result: GatewayResult) -> list[str]:
    return [row["id"] for row in (result.result or {}).get("workOrders", [])]


# ---------------------------------------------------------------------------
# Scenarios: Option A (on-behalf-of)
# ---------------------------------------------------------------------------


def s_obo_cache_hit() -> None:
    w = build_world()
    token = w.user_token(ALICE)
    r1 = w.call("reporting", "list_my_reports", token)
    r2 = w.call("reporting", "list_my_reports", token)
    check(r1.ok and r2.ok, f"calls failed: {r1.reason} {r2.reason}")
    check(r1.result == {"reports": ["RPT-11", "RPT-12"]}, f"unexpected rows {r1.result}")
    check(w.idp.exchange_calls == 1, f"expected one exchange, got {w.idp.exchange_calls}")
    a1, a2 = w.gateway.audit[-2:]
    check(a1["cacheHit"] is False and a2["cacheHit"] is True, "cache hit flags wrong")
    check(a1["tokenHashPrefix"] == a2["tokenHashPrefix"], "cached token changed")
    check("client_assertion" in w.obo.last_form_fields, "exchange did not use a client assertion")
    check("client_secret" not in w.obo.last_form_fields, "exchange used a client secret")
    sent = (w.backends["reporting"].received[-1].header("Authorization") or "")[7:]
    _, claims = _decode(sent)
    check(claims["aud"] == REPORTING_AUD, "downstream audience wrong")
    check(claims["oid"] == ALICE and claims["azp"] == GATEWAY_APP_ID, "downstream subject or client wrong")
    check(sent != token, "inbound token was passed through")


def s_obo_no_cross_user_leak() -> None:
    w = build_world()
    ta, tb = w.user_token(ALICE), w.user_token(BOB)
    results = [w.call("reporting", "list_my_reports", t) for t in (ta, tb, ta)]
    expected = [["RPT-11", "RPT-12"], ["RPT-21"], ["RPT-11", "RPT-12"]]
    check([(r.result or {}).get("reports") for r in results] == expected, "rows leaked across users")
    check(w.idp.exchange_calls == 2, f"expected two exchanges, got {w.idp.exchange_calls}")
    check(len(w.obo.cached_keys()) == 2, "cache not partitioned per user")
    auths = [req.header("Authorization") for req in w.backends["reporting"].received]
    check(auths[0] == auths[2] and auths[0] != auths[1], "downstream tokens shared across users")


def s_obo_expiry_skew() -> None:
    check(OBO_EXPIRY_SKEW > 0, "expiry skew must be positive so cached tokens refresh before they expire")
    w = build_world()
    token = w.user_token(ALICE)
    w.call("reporting", "list_my_reports", token)
    w.clock.advance(IDP_TOKEN_LIFETIME - OBO_EXPIRY_SKEW - 1)
    w.call("reporting", "list_my_reports", token)
    check(w.gateway.audit[-1]["cacheHit"] is True, "expected a cache hit just before skewed expiry")
    w.clock.advance(1)
    r = w.call("reporting", "list_my_reports", token)
    check(r.ok and w.gateway.audit[-1]["cacheHit"] is False, "expected a refresh at skewed expiry")
    check(w.idp.exchange_calls == 2, f"expected two exchanges, got {w.idp.exchange_calls}")


def s_obo_errors_not_cached() -> None:
    w = build_world()
    errors: list[GatewayResult] = []
    tc = w.user_token(CAROL, tid=NORTHWIND_TID)
    for _ in range(2):
        r = w.call("reporting", "list_my_reports", tc)
        check(r.status == 403 and r.reason == "consent_required", f"expected consent_required, got {r.reason}")
        errors.append(r)
    check(w.idp.exchange_calls == 2, "consent error was cached")
    w.idp.consents.add((NORTHWIND_TID, GATEWAY_APP_ID, REPORTING_SCOPE))
    r = w.call("reporting", "list_my_reports", tc)
    check(r.ok and r.result == {"reports": ["RPT-31"]}, f"consent grant not honored: {r.reason}")

    w.idp.mfa_required.add(BOB)
    r = w.call("reporting", "list_my_reports", w.user_token(BOB))
    check(r.status == 401 and r.reason == "interaction_required", f"expected interaction_required, got {r.reason}")
    challenge = r.headers.get("WWW-Authenticate", "")
    check('error="insufficient_claims"' in challenge, "missing claims challenge")
    match = re.search(r'claims="([^"]+)"', challenge)
    check(match is not None and "mfa" in base64.b64decode(match.group(1)).decode("utf-8"),
          "claims challenge does not ask for MFA")
    errors.append(r)
    r = w.call("reporting", "list_my_reports", w.user_token(BOB, amr=("pwd", "mfa")))
    check(r.ok and r.result == {"reports": ["RPT-21"]}, f"step-up token not accepted: {r.reason}")

    w.idp.blocked.add(DAVE)
    r = w.call("reporting", "list_my_reports", w.user_token(DAVE))
    check(r.status == 403 and r.reason == "access_blocked", f"expected access_blocked, got {r.reason}")
    errors.append(r)
    w.idp.available = False
    r = w.call("reporting", "list_my_reports", w.user_token(ALICE))
    check(r.status == 503 and r.reason == "temporarily_unavailable", f"expected 503, got {r.reason}")
    errors.append(r)
    for r in errors:
        text = json.dumps(r.error) + json.dumps(r.headers)
        check("(sim)" not in text and "AADSTS" not in text, "IdP error description leaked to the client")


def s_obo_requires_federated_credential() -> None:
    w = build_world()
    reporting = w.backends["reporting"]
    token = w.user_token(ALICE)
    w.obo.mi_id = ROGUE_MI_ID
    r = w.call("reporting", "list_my_reports", token)
    check(r.status == 502 and r.reason == "backend_auth_failed", f"rogue identity not rejected: {r.reason}")
    check(not reporting.received, "backend called without a valid exchange")
    response = w.idp.exchange_on_behalf_of(FABRIKAM_TID, {
        "grant_type": JWT_BEARER_GRANT, "requested_token_use": "on_behalf_of",
        "client_id": GATEWAY_APP_ID, "client_secret": "placeholder",
        "assertion": token, "scope": REPORTING_SCOPE,
    })
    check(response.status == 401 and response.body["error"] == "invalid_client", "client secret accepted")
    w.obo.mi_id = GATEWAY_MI_ID
    r = w.call("reporting", "list_my_reports", w.agent_token())
    check(r.status == 502, f"app token exchanged on behalf of a user: {r.status}")
    check(not reporting.received, "backend called for an agent token on an OBO route")


# ---------------------------------------------------------------------------
# Scenarios: Option B (signed assertion and identity link)
# ---------------------------------------------------------------------------


def s_assertion_valid() -> None:
    w = build_world()
    fs = _fs(w)
    ta = w.user_token(ALICE)
    r = w.call("field-service", "search_work_orders", ta)
    check(_wo_ids(r) == ["WO-501", "WO-503"], f"alice rows wrong: {r.result} {r.reason}")
    r = w.call("field-service", "search_work_orders", ta, {"status": "open"})
    check(_wo_ids(r) == ["WO-501"], f"status filter wrong: {r.result}")
    r = w.call("field-service", "search_work_orders", w.user_token(BOB))
    check(_wo_ids(r) == ["WO-502"], f"bob rows wrong: {r.result}")

    request = fs.received[0]
    header, claims = _decode(request.header("X-User-Assertion") or "")
    check(header == {"alg": "ES256", "typ": ASSERTION_TYP, "kid": "gw-assert-2026-10"}, f"header {header}")
    check(set(claims) == {"iss", "sub", "aud", "tenant_id", "act", "scope", "jti", "iat", "nbf", "exp",
                          "req_id", "cnf"}, f"claim set {sorted(claims)}")
    check(claims["sub"] == "fs-1001" and claims["aud"] == FIELD_SERVICE_AUD, "subject or audience wrong")
    check(claims["tenant_id"] == "fabrikam" and claims["iss"] == ASSERTION_ISSUER, "tenant or issuer wrong")
    check(claims["act"] == {"sub": f"client:{MCP_CLIENT_ID}"}, f"actor wrong: {claims['act']}")
    check(claims["cnf"] == {"x5t#S256": GATEWAY_CERT}, "assertion not bound to the gateway certificate")
    check(claims["exp"] - claims["iat"] == ASSERTION_LIFETIME, "assertion lifetime wrong")
    audit = next(a for a in w.gateway.audit if a["api"] == "field-service")
    check(fs.log[0]["jti"] == audit["jti"], "backend log and gateway audit disagree on jti")
    check(fs.log[0]["reqId"] == fs.log[0]["headerReqId"] == audit["requestId"], "request IDs not correlated")
    check(request.header("Authorization") is None, "an Authorization header reached the backend")


def s_assertion_expired() -> None:
    w = build_world()
    token = _mint(w)
    w.clock.advance(ASSERTION_LIFETIME + CLOCK_SKEW + 1)
    check(_send_assertion(w, token).reason == "expired", "expired assertion accepted")
    check(_send_assertion(w, _mint(w, lifetime=600)).reason == "lifetime_too_long", "long-lived assertion accepted")
    r = _send_assertion(w, _mint(w, extra={"nbf": w.clock.now + 300}))
    check(r.reason == "not_yet_valid", f"future assertion accepted: {r.reason}")
    check(_send_assertion(w, _mint(w)).status == 200, "control assertion rejected")


def s_assertion_wrong_aud() -> None:
    w = build_world()
    r = _send_assertion(w, _mint(w, aud="urn:contoso:erp:orders"))
    check(r.status == 401 and r.reason == "wrong_audience", f"wrong audience accepted: {r.reason}")
    r = _send_assertion(w, _mint(w, extra={"iss": "https://attacker.example/assertions"}))
    check(r.reason == "bad_issuer", f"wrong issuer accepted: {r.reason}")


def s_assertion_replayed_jti() -> None:
    w = build_world()
    token = _mint(w)
    check(_send_assertion(w, token).status == 200, "first use rejected")
    check(_send_assertion(w, token).reason == "replayed", "replay accepted")
    ta = w.user_token(ALICE)
    for _ in range(3):
        w.call("field-service", "search_work_orders", ta)
    jtis = [a["jti"] for a in w.gateway.audit]
    check(len(jtis) == 3 and len(set(jtis)) == 3, "gateway reused a jti")
    w.clock.advance(ASSERTION_LIFETIME + CLOCK_SKEW + 1)
    check(_send_assertion(w, token).reason == "expired", "old assertion not rejected after expiry")


def s_assertion_tampered() -> None:
    w = build_world()
    good = _mint(w)
    parts = good.split(".")
    _, claims = _decode(good)
    forged = parts[0] + "." + encode_segment(dict(claims, sub="fs-1002")) + "." + parts[2]
    check(_send_assertion(w, forged).reason == "bad_signature", "modified claims accepted")
    check(_send_assertion(w, parts[0] + "." + parts[1] + ".").reason == "bad_signature", "empty signature accepted")
    check(_send_assertion(w, "not-a-jwt").reason == "malformed", "garbage accepted")
    impostor = SimulatedSigningKey("gw-assert-2026-10")
    check(_send_assertion(w, _resign(w, good, sign=impostor.sign)).reason == "bad_signature",
          "impostor key with the same kid accepted")
    check(_send_assertion(w, good).status == 200, "genuine assertion rejected")


def s_assertion_alg_none_rejected() -> None:
    w = build_world()
    good = _mint(w)
    _, claims = _decode(good)
    unsigned = encode_segment({"alg": "none", "typ": ASSERTION_TYP, "kid": w.signer.kid}) + "." \
        + encode_segment(claims) + "."
    check(_send_assertion(w, unsigned).reason == "alg_not_allowed", "alg none accepted")
    check(_send_assertion(w, w.user_token(ALICE)).reason == "alg_not_allowed", "IdP token accepted as assertion")
    hs256 = _resign(w, good, header={"alg": "HS256"})
    check(_send_assertion(w, hs256).reason == "alg_not_allowed", "HS256 assertion accepted")
    wrong_typ = _resign(w, _mint(w), header={"typ": "JWT"})
    check(_send_assertion(w, wrong_typ).reason == "wrong_typ", "generic JWT typ accepted")


def s_assertion_wrong_tenant() -> None:
    w = build_world()
    fs = _fs(w)
    r = _send_assertion(w, _mint(w, tenant="northwind"))
    check(r.status == 403 and r.reason == "wrong_instance", f"cross-tenant assertion accepted: {r.reason}")
    r = _send_assertion(w, _mint(w, sub="fs-9999"))
    check(r.status == 403 and r.reason == "unknown_local_user", f"unknown user accepted: {r.reason}")
    r = w.call("field-service", "search_work_orders", w.user_token(CAROL, tid=NORTHWIND_TID))
    check(_wo_ids(r) == ["WO-901"], f"carol rows wrong: {r.result} {r.reason}")
    erin_link = IdentityLink(w.idp.issuer(NORTHWIND_TID), NORTHWIND_TID, ERIN, "fabrikam", "fs-1003")
    try:
        w.links.link(erin_link, "tenant-admin")
    except LinkConflict as exc:
        check(exc.reason == "tenant_not_entitled", f"unexpected conflict {exc.reason}")
    else:
        raise AssertionError("cross-tenant link was accepted")
    w.links.insert_unchecked(erin_link)
    before = len(fs.received)
    r = w.call("field-service", "search_work_orders", w.user_token(ERIN, tid=NORTHWIND_TID))
    check(r.status == 403 and r.reason == "identity_not_linked", f"legacy cross-tenant link honored: {r.reason}")
    check(len(fs.received) == before, "backend called for a cross-tenant link")


def s_assertion_bound_to_gateway_connection() -> None:
    w = build_world()
    fs = _fs(w)
    for connection in (OPS_TOOL_CONNECTION, INTERNET):
        r = _send_assertion(w, _mint(w), connection)
        check(r.status == 403 and r.reason == "not_from_gateway", f"non-gateway path accepted: {r.reason}")
    check(len(fs.alerts) == 2 and all(a["identityHeaderPresent"] for a in fs.alerts), "alerts missing")
    r = _send_assertion(w, _mint(w), SECONDARY_GATEWAY_CONNECTION)
    check(r.reason == "cnf_mismatch", f"assertion accepted on a different certificate: {r.reason}")
    r = _send_assertion(w, _mint(w), extra_headers=[("x-user-assertion", _mint(w, sub="fs-1002"))])
    check(r.reason == "duplicate_assertion", f"duplicate assertion headers accepted: {r.reason}")
    check(_send_assertion(w, _mint(w)).status == 200, "control assertion rejected")


def s_signing_key_rotation() -> None:
    w = build_world()
    fs = _fs(w)
    ta = w.user_token(ALICE)
    check(w.call("field-service", "search_work_orders", ta).ok, "first call failed")
    check(fs.jwks_refreshes == 1, f"expected one JWKS fetch, got {fs.jwks_refreshes}")
    old_minter = AssertionMinter(w.signer, w.clock)
    new_key = SimulatedSigningKey("gw-assert-2027-01")
    w.jwks.publish(new_key)
    w.minter.signer = new_key
    r = w.call("field-service", "search_work_orders", ta)
    check(r.ok and fs.jwks_refreshes == 2, f"new kid not fetched on demand: {r.reason} {fs.jwks_refreshes}")
    check(w.gateway.audit[-1]["kid"] == "gw-assert-2027-01", "gateway did not switch keys")
    check(_send_assertion(w, _mint(w, minter=old_minter)).status == 200, "overlap window broken")
    w.jwks.withdraw(old_minter.signer.kid)
    w.clock.advance(JWKS_TTL)
    check(_send_assertion(w, _mint(w, minter=old_minter)).reason == "unknown_kid", "withdrawn key still trusted")
    check(w.call("field-service", "search_work_orders", ta).ok, "new key rejected after withdrawal")
    w.clock.advance(JWKS_FORCED_REFRESH_INTERVAL)
    before = fs.jwks_refreshes
    _send_assertion(w, _resign(w, _mint(w), header={"kid": "bogus"}))
    check(fs.jwks_refreshes == before + 1, "unknown kid did not trigger one forced refresh")
    r = _send_assertion(w, _resign(w, _mint(w), header={"kid": "bogus"}))
    check(r.reason == "unknown_kid" and fs.jwks_refreshes == before + 1, "forced refresh not rate limited")
    document = w.jwks.document()
    check(all(set(k) == {"kid", "alg", "use", "kty"} for k in document["keys"]), "JWKS exposes extra fields")
    check({k["kid"] for k in document["keys"]} == {"gw-assert-2027-01"}, "JWKS key set wrong")


def s_unlinked_user_denied() -> None:
    w = build_world()
    fs = _fs(w)
    td = w.user_token(DAVE)
    r = w.call("field-service", "search_work_orders", td)
    check(r.status == 403 and r.reason == "identity_not_linked", f"unlinked user not denied: {r.reason}")
    check((r.error or {})["data"].get("linkUrl") == LINK_URL, "link URL missing")
    check(not fs.received, "backend called for an unlinked user")
    check("fs-" not in json.dumps(r.error), "backend account IDs leaked to the client")
    lookups = w.links.lookups
    w.call("field-service", "search_work_orders", td)
    check(w.links.lookups == lookups, "negative result not cached")
    w.links.link(IdentityLink(w.idp.issuer(FABRIKAM_TID), FABRIKAM_TID, DAVE, "fabrikam", "fs-1003",
                              source="jit"), "jit-link-flow")
    r = w.call("field-service", "search_work_orders", td)
    check(_wo_ids(r) == ["WO-504"], f"new link not honored: {r.result} {r.reason}")


def s_link_revocation_and_conflict() -> None:
    w = build_world()
    tb = w.user_token(BOB)
    check(w.call("field-service", "search_work_orders", tb).ok, "bob denied before revocation")
    w.links.revoke(w.idp.issuer(FABRIKAM_TID), FABRIKAM_TID, BOB, "fabrikam", "tenant-admin")
    r = w.call("field-service", "search_work_orders", tb)
    check(r.reason == "identity_not_linked", f"revoked link still honored: {r.reason}")
    try:
        w.links.link(IdentityLink(w.idp.issuer(FABRIKAM_TID), FABRIKAM_TID, DAVE, "fabrikam", "fs-1001"),
                     "jit-link-flow")
    except LinkConflict as exc:
        check(exc.reason == "backend_account_already_linked", f"unexpected conflict {exc.reason}")
    else:
        raise AssertionError("second identity linked to the same backend account")
    check(w.links.events[-1]["type"] == "link.conflict", "conflict not recorded")


def s_link_store_outage_fails_closed() -> None:
    w = build_world()
    ta = w.user_token(ALICE)
    check(w.call("field-service", "search_work_orders", ta).ok, "warm-up call failed")
    w.links.available = False
    check(w.call("field-service", "search_work_orders", ta).ok, "cached link not used during outage")
    r = w.call("field-service", "search_work_orders", w.user_token(DAVE))
    check(r.status == 503 and r.reason == "temporarily_unavailable", f"outage not fail-closed: {r.reason}")
    w.clock.advance(LINK_CACHE_TTL)
    r = w.call("field-service", "search_work_orders", ta)
    check(r.status == 503, f"stale link used after TTL: {r.status}")
    reads = len(w.vault.reads)
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    check(r.status == 503 and len(w.vault.reads) == reads, "bridge read secrets without a link decision")


# ---------------------------------------------------------------------------
# Scenarios: Option C (per-tenant credential bridge)
# ---------------------------------------------------------------------------


def _order_request(tenant: str, order_id: str, headers: list[tuple[str, str]],
                   connection: Connection = GATEWAY_CONNECTION) -> BackendRequest:
    return BackendRequest("GET", f"/{tenant}/api/orders/v2/orders/{order_id}/status", headers, {}, connection)


def s_bridge_wrong_tenant_key_denied() -> None:
    w = build_world()
    orders = _orders(w)
    ta = w.user_token(ALICE)
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    check(r.ok and r.result == {"orderId": "SO-1042", "status": "shipped"}, f"bridge call failed: {r.reason}")
    check(w.gateway.audit[-1]["keyCacheHit"] is False, "first key read reported as a cache hit")
    check(orders.log[-1]["actingUser"] == "fs-1001", "acting user not taken from the identity link")
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    check(r.ok and w.gateway.audit[-1]["keyCacheHit"] is True, "tenant key not cached")
    resp = orders.handle(_order_request("fabrikam", "SO-1042",
                                        [("X-Api-Key", orders.keys["northwind"]), ("X-Acting-User", "fs-1001")]))
    check(resp.status == 401 and resp.reason == "invalid_api_key", f"cross-tenant key accepted: {resp.reason}")
    tc = w.user_token(CAROL, tid=NORTHWIND_TID)
    r = w.call("orders", "get_order_status", tc, {"order_id": "SO-1042"})
    check(r.status == 403 and r.reason == "backend_denied", f"carol read another tenant's order: {r.reason}")
    check(w.gateway.audit[-1]["backendReason"] == "not_found", "backend reason not recorded")
    r = w.call("orders", "get_order_status", tc, {"order_id": "SO-2001"})
    check(r.ok and (r.result or {}).get("status") == "open", f"carol denied her own order: {r.reason}")
    r = w.call("orders", "get_order_status", w.user_token(DAVE), {"order_id": "SO-1042"})
    check(r.ok and orders.log[-1]["actingUser"] == f"entra:{DAVE}", "unlinked acting user not namespaced")
    w.vault.set_secret(ORDERS_SECRET_TEMPLATE.format(tenantId="fabrikam"), orders.rotate_key("fabrikam"))
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    audit = w.gateway.audit[-1]
    check(r.ok and audit["attempts"] == 2 and audit["keyRefreshed"], f"rotation not absorbed: {r.reason}")
    orders.rotate_key("fabrikam")
    before = len(orders.received)
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    check(r.status == 502 and r.reason == "backend_auth_failed", f"stale vault not surfaced: {r.reason}")
    check(len(orders.received) == before + 2, "retry loop is not bounded to one refresh")


def s_bridge_spoofed_acting_user_non_gateway_denied() -> None:
    w = build_world()
    orders = _orders(w)
    key = orders.keys["fabrikam"]
    for connection in (OPS_TOOL_CONNECTION, INTERNET):
        resp = orders.handle(_order_request("fabrikam", "SO-1042",
                                            [("X-Api-Key", key), ("X-Acting-User", "fs-1002")], connection))
        check(resp.status == 403 and resp.reason == "not_from_gateway", f"non-gateway path accepted: {resp.reason}")
    check(len(orders.alerts) == 2 and all(a["identityHeaderPresent"] for a in orders.alerts), "alerts missing")
    check(not orders.log, "spoofed request reached business logic")
    r = w.call("orders", "get_order_status", w.user_token(ALICE), {"order_id": "SO-1042"},
               [("X-Acting-User", "fs-1002")])
    check(r.ok, f"gateway call failed: {r.reason}")
    check(orders.received[-1].header_values("X-Acting-User") == ["fs-1001"], "client-supplied acting user forwarded")


def s_bridge_tool_allow_list() -> None:
    w = build_world()
    ta = w.user_token(ALICE)
    r = w.call("orders", "cancel_order", ta, {"order_id": "SO-1042"})
    check(r.status == 403 and r.reason == "not_entitled", f"write tool allowed on the bridge: {r.reason}")
    check(not w.vault.reads and w.links.lookups == 0, "secrets or links read for a disallowed tool")
    r = w.call("orders", "delete_all_orders", ta, {})
    check(r.status == 404 and r.reason == "unknown_tool" and (r.error or {}).get("code") == -32602,
          f"unknown tool not rejected: {r.reason}")
    r = w.call("orders", "get_order_status", ta, {"order_id": "SO-1042/../../admin"})
    check(r.status == 400 and r.reason == "invalid_params" and (r.error or {}).get("code") == -32602,
          f"path traversal not rejected: {r.reason}")
    check(not _orders(w).received, "orders backend called for a rejected request")


# ---------------------------------------------------------------------------
# Scenarios: cross-cutting
# ---------------------------------------------------------------------------


def s_client_identity_headers_stripped() -> None:
    w = build_world()
    names = ["Authorization", "X-User-Assertion", "X-Acting-User", "X-Api-Key", "X-Contoso-User",
             "X-Contoso-Tenant", "X-Contoso-Gateway-Tenant", "X-Request-Id", "traceparent"]
    headers = [(n, "spoofed-" + n) for n in names] + [(n.lower(), "spoofed-lower") for n in names]
    headers.append(("Accept-Language", "en-US"))
    ta = w.user_token(ALICE)
    for api, tool, args in [
        ("reporting", "list_my_reports", {}),
        ("field-service", "search_work_orders", {}),
        ("orders", "get_order_status", {"order_id": "SO-1042"}),
        ("orders-reference", "list_order_types", {}),
    ]:
        r = w.call(api, tool, ta, args, headers)
        check(r.ok, f"{api} failed with client headers present: {r.reason}")
        request = w.backends[api].received[-1]
        check(not any("spoofed" in v for _, v in request.headers), f"{api} received a client identity header")
        check(request.header("Accept-Language") == "en-US", f"{api} lost a benign header")
        check(request.header_values("X-Request-Id") == [r.request_id], f"{api} request id not gateway-owned")


def s_passthrough_token_rejected_by_audience() -> None:
    w = build_world()
    ta = w.user_token(ALICE)
    resp = naive_passthrough(w, "reporting", ta)
    check(resp.status == 401 and resp.reason == "wrong_audience", f"passthrough accepted: {resp.reason}")
    resp = naive_passthrough(w, "field-service", ta)
    check(resp.status == 401 and resp.reason == "alg_not_allowed", f"IdP token accepted as assertion: {resp.reason}")
    r = w.call("reporting", "list_my_reports", ta)
    check(r.ok and r.result == {"reports": ["RPT-11", "RPT-12"]}, f"OBO path failed: {r.reason}")
    downstream = w.obo.cached_tokens()[0]
    r = w.call("reporting", "list_my_reports", downstream)
    check(r.status == 401 and r.reason == "invalid_token", "downstream token accepted by the gateway")


def s_app_only_ignores_user_arg() -> None:
    w = build_world()
    ref = w.backends["orders-reference"]
    r = w.call("orders-reference", "list_order_types", w.user_token(ALICE), {"user": "fs-1002"})
    check(r.ok and r.result == {"orderTypes": ["standard", "rush", "return"]}, f"app-only call failed: {r.reason}")
    audit = w.gateway.audit[-1]
    check(audit["droppedArgs"] == ["user"] and audit["backendUserId"] is None, "user argument not dropped")
    entry = ref.log[-1]
    check(entry["body"] == {} and entry["hasUserClaims"] is False, "user context leaked into app-only call")
    check(entry["tenantHeader"] == "fabrikam" and entry["azp"] == GATEWAY_MI_ID, "app-only identity wrong")
    check(not w.obo.cached_keys() and w.links.lookups == 0, "app-only call used user credentials")
    w.idp.app_role_assignments[(GATEWAY_MI_ID, ORDERS_AUD)] = set()
    r = w.call("orders-reference", "list_order_types", w.user_token(ALICE))
    check(r.status == 403 and r.reason == "backend_denied", f"call without app role allowed: {r.reason}")
    check(w.gateway.audit[-1]["backendReason"] == "insufficient_role", "backend reason not recorded")


def s_agent_identity_acts_as_itself() -> None:
    w = build_world()
    fs = _fs(w)
    agent = w.agent_token()
    r = w.call("field-service", "search_work_orders", agent)
    check(_wo_ids(r) == ["WO-510"], f"agent rows wrong: {r.result} {r.reason}")
    check(fs.log[-1]["act"] is None and fs.log[-1]["sub"] == "svc-dispatch-agent", "agent assertion wrong")
    audit = w.gateway.audit[-1]
    check(audit["principalKind"] == "agent" and audit["actor"] is None, "agent audit fields wrong")
    r = w.call("reporting", "list_my_reports", agent)
    check(r.status == 502 and r.reason == "backend_auth_failed", f"app token exchanged on-behalf-of: {r.reason}")
    r = w.call("field-service", "search_work_orders", w.agent_token(roles=()))
    check(r.status == 403 and r.reason == "insufficient_scope", f"agent without role allowed: {r.reason}")
    r = w.call("field-service", "search_work_orders", w.user_token(ALICE, client_id=AGENT_APP_ID))
    check(_wo_ids(r) == ["WO-501", "WO-503"], f"delegated rows wrong: {r.result} {r.reason}")
    check(fs.log[-1]["act"] == f"client:{AGENT_APP_ID}", "delegating agent not recorded as actor")


def s_inbound_token_validated_first() -> None:
    w = build_world()
    expiring = w.user_token(ALICE, lifetime=60)
    cases = [
        (w.user_token(ALICE, audience=REPORTING_AUD), 401, "invalid_token"),
        ("not-a-jwt", 401, "invalid_token"),
        (w.user_token(ALICE, tid=UNKNOWN_TID), 403, "unknown_tenant"),
        (w.user_token(ALICE, scope="profile"), 403, "insufficient_scope"),
    ]
    w.clock.advance(120)
    cases.append((expiring, 401, "invalid_token"))
    for token, status, reason in cases:
        for api, tool, args in [("reporting", "list_my_reports", {}),
                                ("orders", "get_order_status", {"order_id": "SO-1042"})]:
            r = w.call(api, tool, token, args)
            check(r.status == status and r.reason == reason, f"expected {reason}, got {r.status} {r.reason}")
            if reason == "invalid_token":
                check("invalid_token" in r.headers.get("WWW-Authenticate", ""), "WWW-Authenticate missing")
    check(all(not b.received for b in w.backends.values()), "backend called before inbound validation")
    check(w.idp.exchange_calls == 0 and not w.vault.reads and w.links.lookups == 0, "work done for a bad token")


def s_production_adapter_is_gated() -> None:
    authority = "https://login.microsoftonline.com/" + FABRIKAM_TID
    vault_uri = "https://vault.contoso.example"
    for factory in (lambda **kw: EntraOnBehalfOfExchanger(authority, GATEWAY_APP_ID, **kw),
                    lambda **kw: KeyVaultAssertionSigner(vault_uri, "assertion-signing", "gw-assert-2026-10", **kw)):
        try:
            factory()
        except RuntimeError:
            pass
        else:
            raise AssertionError("production adapter constructed without enable_network")
    exchanger: TokenExchanger = EntraOnBehalfOfExchanger(authority, GATEWAY_APP_ID, enable_network=True)
    signer: AssertionSigner = KeyVaultAssertionSigner(vault_uri, "assertion-signing", "gw-assert-2026-10",
                                                      enable_network=True)
    for action in (lambda: exchanger.get_token("t", FABRIKAM_TID, ALICE, REPORTING_AUD, [REPORTING_SCOPE]),
                   lambda: signer.sign(b"data")):
        try:
            action()
        except NotImplementedError:
            pass
        else:
            raise AssertionError("production stub returned a value")


def s_no_secrets_in_audit() -> None:
    w = build_world()
    ta, td = w.user_token(ALICE), w.user_token(DAVE)
    w.call("reporting", "list_my_reports", ta)
    w.call("reporting", "list_my_reports", ta)
    w.call("field-service", "search_work_orders", ta)
    w.call("field-service", "search_work_orders", td)
    w.call("orders", "get_order_status", ta, {"order_id": "SO-1042"})
    w.call("orders-reference", "list_order_types", ta, {"user": "fs-1002"})
    w.call("field-service", "search_work_orders", w.agent_token())
    w.call("reporting", "list_my_reports", w.user_token(ALICE, audience=REPORTING_AUD))
    _send_assertion(w, _mint(w), OPS_TOOL_CONNECTION)
    records = {
        "audit": w.gateway.audit, "links": w.links.events,
        "backends": {name: {"log": b.log, "alerts": b.alerts} for name, b in w.backends.items()},
    }
    dump = json.dumps(records)
    credentials = list(w.idp.issued) + w.vault.secret_values()
    credentials += [v for req in _fs(w).received for v in req.header_values("X-User-Assertion")]
    leaked = [c for c in credentials if c and c in dump]
    check(not leaked, f"{len(leaked)} credential(s) found in audit or logs")
    check("Bearer " not in dump, "authorization header value found in logs")
    check(all(len(a.get("tokenHashPrefix", "")) <= 8 for a in w.gateway.audit), "token hash too long")
    check(all(a["requestId"] for a in w.gateway.audit), "audit record without request id")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, Callable[[], None]]] = [
    ("obo-cache-hit", s_obo_cache_hit),
    ("obo-no-cross-user-leak", s_obo_no_cross_user_leak),
    ("obo-expiry-skew", s_obo_expiry_skew),
    ("obo-errors-not-cached", s_obo_errors_not_cached),
    ("obo-requires-federated-credential", s_obo_requires_federated_credential),
    ("assertion-valid", s_assertion_valid),
    ("assertion-expired", s_assertion_expired),
    ("assertion-wrong-aud", s_assertion_wrong_aud),
    ("assertion-replayed-jti", s_assertion_replayed_jti),
    ("assertion-tampered", s_assertion_tampered),
    ("assertion-alg-none-rejected", s_assertion_alg_none_rejected),
    ("assertion-wrong-tenant", s_assertion_wrong_tenant),
    ("assertion-bound-to-gateway-connection", s_assertion_bound_to_gateway_connection),
    ("signing-key-rotation", s_signing_key_rotation),
    ("unlinked-user-denied", s_unlinked_user_denied),
    ("link-revocation-and-conflict", s_link_revocation_and_conflict),
    ("link-store-outage-fails-closed", s_link_store_outage_fails_closed),
    ("bridge-wrong-tenant-key-denied", s_bridge_wrong_tenant_key_denied),
    ("bridge-spoofed-acting-user-non-gateway-denied", s_bridge_spoofed_acting_user_non_gateway_denied),
    ("bridge-tool-allow-list", s_bridge_tool_allow_list),
    ("client-identity-headers-stripped", s_client_identity_headers_stripped),
    ("passthrough-token-rejected-by-audience", s_passthrough_token_rejected_by_audience),
    ("app-only-ignores-user-arg", s_app_only_ignores_user_arg),
    ("agent-identity-acts-as-itself", s_agent_identity_acts_as_itself),
    ("inbound-token-validated-first", s_inbound_token_validated_first),
    ("production-adapter-is-gated", s_production_adapter_is_gated),
    ("no-secrets-in-audit", s_no_secrets_in_audit),
]


def run_self_test() -> int:
    failures = 0
    for name, scenario in SCENARIOS:
        try:
            scenario()
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        else:
            print(f"PASS {name}")
    print(f"{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed")
    return 0 if failures == 0 else 1


def _pick(source: dict, *keys: str) -> dict:
    return {k: source.get(k) for k in keys if k in source}


def _show(label: str, value: Any) -> None:
    print(f"  {label}: {json.dumps(value, sort_keys=True)}")


def run_demo() -> int:
    w = build_world()
    alice = w.user_token(ALICE)
    _, inbound = _decode(alice)
    print("Inbound MCP request (token values are never printed)")
    _show("inbound claims", _pick(inbound, "aud", "tid", "oid", "azp", "scp", "idtyp"))

    print("\nOption A - on-behalf-of exchange to the reporting API")
    r = w.call("reporting", "list_my_reports", alice)
    w.call("reporting", "list_my_reports", alice)
    _, downstream = _decode(w.obo.cached_tokens()[0])
    _show("downstream claims", _pick(downstream, "aud", "tid", "oid", "azp", "scp"))
    _show("result", r.result)
    _show("audit (second call)", _pick(w.gateway.audit[-1], "mode", "cacheHit", "tokenHashPrefix", "backendUserId"))

    print("\nOption B - signed assertion plus identity link to the field-service API")
    r = w.call("field-service", "search_work_orders", alice)
    header, claims = _decode(_fs(w).received[-1].header("X-User-Assertion") or "")
    _show("assertion header", header)
    _show("assertion claims", _pick(claims, "iss", "aud", "sub", "tenant_id", "scope", "act", "cnf", "req_id"))
    _show("result", r.result)
    r = w.call("field-service", "search_work_orders", w.user_token(DAVE))
    _show("unlinked user", {"status": r.status, "reason": r.reason, "linkUrl": (r.error or {})["data"].get("linkUrl")})

    print("\nOption C - per-tenant credential bridge to the orders API (transitional)")
    r = w.call("orders", "get_order_status", alice, {"order_id": "SO-1042"}, [("X-Acting-User", "fs-1002")])
    sent = _orders(w).received[-1]
    _show("headers sent", {"X-Api-Key": "<redacted>", "X-Acting-User": sent.header("X-Acting-User"),
                           "X-Request-Id": sent.header("X-Request-Id")})
    _show("result", r.result)
    resp = _orders(w).handle(_order_request("fabrikam", "SO-1042",
                                            [("X-Api-Key", "guess"), ("X-Acting-User", "fs-1002")], INTERNET))
    _show("direct call from the internet", {"status": resp.status, "reason": resp.reason})

    print("\nApp-only call for reference data")
    r = w.call("orders-reference", "list_order_types", alice, {"user": "fs-1002"})
    _show("backend saw", w.backends["orders-reference"].log[-1])
    _show("audit", _pick(w.gateway.audit[-1], "mode", "droppedArgs", "backendUserId"))

    print("\nAgent acting as itself")
    r = w.call("field-service", "search_work_orders", w.agent_token())
    _show("backend saw", _pick(_fs(w).log[-1], "sub", "act"))
    _show("result", r.result)

    print("\nAnti-pattern - token passthrough")
    _show("reporting", naive_passthrough(w, "reporting", alice).body)
    _show("field-service", naive_passthrough(w, "field-service", alice).body)

    print("\nAudit trail")
    for record in w.gateway.audit:
        print("  " + json.dumps(_pick(record, "requestId", "api", "tool", "mode", "oid", "backendUserId",
                                       "actor", "outcome", "reason")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backend identity propagation simulator (test only).")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--self-test", action="store_true", help="run every scenario and print PASS/FAIL")
    group.add_argument("--demo", action="store_true", help="narrate each option without printing tokens")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.demo:
        return run_demo()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
