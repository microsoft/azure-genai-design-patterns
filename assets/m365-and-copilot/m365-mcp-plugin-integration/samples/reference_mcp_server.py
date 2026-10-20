#!/usr/bin/env python3
"""Reference MCP server for the Contoso account workbench sample (Server A, namespace servicecrm).

Standard library only (Python 3.9+). This is teaching code, not a production server.

Transport  Streamable HTTP, protocol revision 2025-06-18, JSON responses (no SSE stream).
Methods    initialize, notifications/initialized, ping, tools/list, tools/call,
           resources/list, resources/read (one ui:// MCP App view).
Security   validate_bearer() checks the Bearer token (Path A1 claims). SideEffectGate enforces the
           R and W1-W4 action classes on the server, independent of any confirmation card.

Usage
  python reference_mcp_server.py                             offline self-test, exit code 0 on success
  python reference_mcp_server.py --serve --port 8080         listen on http://127.0.0.1:8080/mcp
  python reference_mcp_server.py --serve --allow-anonymous   R tools only, no token (dev only)

Before production: replace the HS256 dev signature check with RS256 validation against the
issuer's JWKS, move drafts, caps, and audit rows to durable storage, load keys from a secret
store, and run behind TLS.
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
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "contoso-crm", "version": "1.4.0"}
WIDGET_URI = "ui://servicecrm/account-dashboard"
WIDGET_MIME = "text/html;profile=mcp-app"
CATALOG_PATH = Path(__file__).with_name("mcp-tools-catalog.json")

# Path A1 token expectations; see docs/authentication-paths.md, section 3.6.
EXPECTED_ISS = "https://auth.crm.contoso.com"
EXPECTED_AUD = "https://crm-mcp.contoso.com"
ALLOWED_TENANTS = {"contoso-org-0001"}
RESOURCE_METADATA_URL = EXPECTED_AUD + "/.well-known/oauth-protected-resource"
LOCAL_ORIGIN_HOSTS = {"localhost", "127.0.0.1"}  # browser callers (for example MCP Inspector)
DEV_SIGNING_KEY = secrets.token_bytes(32)   # dev only; regenerated for every process
DEV_APPROVAL_KEY = secrets.token_bytes(32)  # dev only; stands in for a separate approval service
DAILY_WRITE_CAP = 20
MAX_RESULT_BYTES = 20_000
MAX_ITEMS = 25
DEV_USER = "user-0001"


# --------------------------------------------------------------------------- tool definitions
def _account_id() -> dict:
    return {"type": "string", "pattern": "^ACC-[0-9]{4}$", "description": "Account ID, for example ACC-1001."}


def _hints(read_only: bool, destructive: bool = False, idempotent: bool = False, open_world: bool = False) -> dict:
    if read_only:
        return {"readOnlyHint": True, "openWorldHint": open_world}
    return {"readOnlyHint": False, "destructiveHint": destructive,
            "idempotentHint": idempotent, "openWorldHint": open_world}


def _tool(name, title, description, properties, required, hints, ui=None) -> dict:
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    tool = {"name": name, "title": title, "description": description,
            "inputSchema": schema, "annotations": {"title": title, **hints}}
    if ui:
        tool["_meta"] = {"ui": ui}
    return tool


# Kept identical to mcp-tools-catalog.json. The self-test fails if the two drift apart.
TOOLS = [
    _tool("get_account_summary", "Get account summary",
          "Return the health, pipeline, and recent orders for one customer account. Use when the user "
          "names an account or asks how an account is doing. Do not use to search across accounts.",
          {"account_id": _account_id()}, ["account_id"], _hints(True),
          {"resourceUri": WIDGET_URI, "visibility": ["model", "app"]}),
    _tool("search_opportunities", "Search opportunities",
          "Find open opportunities by stage, minimum amount, and close date. Returns up to 25 rows and a "
          "next_cursor. Use for lists of deals. Do not use for one account's summary.",
          {"stage": {"type": "string", "enum": ["prospecting", "qualification", "proposal", "negotiation"],
                     "description": "Sales stage filter. Omit for all open stages."},
           "min_amount": {"type": "number", "minimum": 0,
                          "description": "Minimum opportunity amount in USD, as a plain number."},
           "close_before": {"type": "string", "format": "date",
                            "description": "Only opportunities expected to close on or before this date (YYYY-MM-DD)."},
           "owner": {"type": "string", "enum": ["me", "anyone"],
                     "description": "Use me for the signed-in user's opportunities. Defaults to me."},
           "limit": {"type": "integer", "minimum": 1, "maximum": 25,
                     "description": "Maximum rows to return. Defaults to 10."},
           "cursor": {"type": "string",
                      "description": "The next_cursor value from a previous call, to get more rows."}},
          [], _hints(True)),
    _tool("save_shortlist", "Save to shortlist",
          "Save one account and a short note to the signed-in user's shortlist. Saving the same account "
          "again updates the note. Use when the user asks to save, pin, or note an account.",
          {"account_id": _account_id(),
           "note": {"type": "string", "maxLength": 280, "description": "Short reason for saving the account."}},
          ["account_id", "note"], _hints(False, idempotent=True), {"visibility": ["model", "app"]}),
    _tool("draft_order", "Draft an order",
          "Create an order draft for one SKU and quantity. Returns a draft_id, line items, and an estimated "
          "total. Nothing is sent to fulfillment. Use before submit_order.",
          {"account_id": _account_id(),
           "sku": {"type": "string", "pattern": "^SKU-[0-9]{3}$",
                   "description": "Product SKU, for example SKU-778. Confirm with the catalog if unsure."},
           "quantity": {"type": "integer", "minimum": 1, "maximum": 1000, "description": "Number of units."}},
          ["account_id", "sku", "quantity"], _hints(False, idempotent=True)),
    _tool("submit_order", "Submit an order",
          "Submit an existing order draft to fulfillment. Requires the draft_id returned by draft_order. "
          "Each draft can be submitted once. Use only when the user explicitly asks to submit.",
          {"draft_id": {"type": "string", "pattern": "^DRF-[0-9a-f]{8}$",
                        "description": "The draft_id returned by draft_order."}},
          ["draft_id"], _hints(False, destructive=True)),
    _tool("notify_account_team", "Notify the account team",
          "Send one message to every member of an account team. Requires an approval_token from the separate "
          "approval step. Use only when the user asks and provides the token.",
          {"account_id": _account_id(),
           "message": {"type": "string", "maxLength": 1000, "description": "Message text to send."},
           "approval_token": {"type": "string",
                              "description": "Approval token issued by the approval step for this account."}},
          ["account_id", "message", "approval_token"], _hints(False, destructive=True, open_world=True)),
]
TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}
TOOL_CLASS = {"get_account_summary": "R", "search_opportunities": "R", "save_shortlist": "W1",
              "draft_order": "W2", "submit_order": "W3", "notify_account_team": "W4"}
REQUIRED_SCOPE = {"R": "crm.read", "W1": "crm.write", "W2": "crm.write", "W3": "crm.write", "W4": "crm.write"}

# --------------------------------------------------------------------------- sample data
ACCOUNTS = {
    "ACC-1001": {"account_id": "ACC-1001", "name": "Fabrikam Retail", "health_score": 82,
                 "open_opportunities": 3, "pipeline_value": 410000, "currency": "USD",
                 "recent_orders": [{"order_id": "ORD-5531", "date": "2026-10-14", "total": 18250}]},
    "ACC-1002": {"account_id": "ACC-1002", "name": "Northwind Traders", "health_score": 64,
                 "open_opportunities": 1, "pipeline_value": 65000, "currency": "USD",
                 "recent_orders": [{"order_id": "ORD-5409", "date": "2026-09-30", "total": 7400}]},
}
OPPORTUNITIES = [
    {"opportunity_id": "OPP-2001", "account_id": "ACC-1001", "name": "Store refresh wave 2",
     "stage": "negotiation", "amount": 250000, "close_date": "2026-11-28", "owner": DEV_USER},
    {"opportunity_id": "OPP-2002", "account_id": "ACC-1001", "name": "Handheld scanners",
     "stage": "proposal", "amount": 120000, "close_date": "2026-12-15", "owner": DEV_USER},
    {"opportunity_id": "OPP-2003", "account_id": "ACC-1001", "name": "Loyalty analytics pilot",
     "stage": "qualification", "amount": 40000, "close_date": "2027-01-29", "owner": "user-0002"},
    {"opportunity_id": "OPP-2004", "account_id": "ACC-1002", "name": "Warehouse Wi-Fi",
     "stage": "prospecting", "amount": 65000, "close_date": "2027-02-26", "owner": "user-0002"},
]
SKU_PRICES = {"SKU-778": 125.0, "SKU-310": 42.5}

# --------------------------------------------------------------------------- MCP App view (ui://)
# Data is inserted with textContent only. The view talks to the host over postMessage JSON-RPC.
WIDGET_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Account dashboard</title>
<style>
:root{--wb-bg:var(--color-background-primary,#ffffff);--wb-text:var(--color-text-primary,#242424);--wb-muted:#616161;--wb-border:#d1d1d1;--wb-accent:#0f6cbd}
:root[data-theme=dark]{--wb-bg:var(--color-background-primary,#1f1f1f);--wb-text:var(--color-text-primary,#ffffff);--wb-muted:#adadad;--wb-border:#3d3d3d;--wb-accent:#479ef5}
@media (forced-colors:active){:root{--wb-bg:Canvas;--wb-text:CanvasText;--wb-muted:CanvasText;--wb-border:CanvasText;--wb-accent:LinkText}}
body{margin:0;padding:12px;font:14px/1.4 system-ui,sans-serif;background:var(--wb-bg);color:var(--wb-text)}
h1{font-size:16px;margin:0 0 8px}h2{font-size:14px;margin:12px 0 4px}.kpi{margin-right:16px}.muted{color:var(--wb-muted)}
button{border:1px solid var(--wb-border);background:transparent;color:var(--wb-accent);padding:4px 10px;cursor:pointer}
button:focus-visible{outline:2px solid var(--wb-accent);outline-offset:2px}
</style></head>
<body><main aria-labelledby="title">
<h1 id="title">Account</h1>
<p><span class="kpi">Health <strong id="health">-</strong>/100</span><span class="kpi">Pipeline <strong id="pipeline">-</strong></span><span class="kpi">Open opportunities <strong id="opps">-</strong></span></p>
<h2>Recent orders</h2><ul id="orders"></ul>
<button id="save" type="button" hidden>Save to shortlist</button>
<p id="status" class="muted" role="status" aria-live="polite"></p>
</main>
<script>
const pending = new Map(); let nextId = 1, current = null, canCallTools = false;
const $ = (id) => document.getElementById(id);
function send(msg) { window.parent.postMessage(msg, "*"); }
function request(method, params) {
  const id = nextId++; send({ jsonrpc: "2.0", id, method, params });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
function applyTheme(ctx) { if (ctx && ctx.theme) document.documentElement.dataset.theme = ctx.theme; }
function render(d) {
  if (!d || !d.account_id) return; current = d;
  $("title").textContent = d.name + " (" + d.account_id + ")";
  $("health").textContent = String(d.health_score);
  $("pipeline").textContent = d.pipeline_value.toLocaleString() + " " + d.currency;
  $("opps").textContent = String(d.open_opportunities);
  const list = $("orders"); list.replaceChildren();
  for (const o of d.recent_orders || []) {
    const li = document.createElement("li");
    li.textContent = o.order_id + ", " + o.date + ", " + o.total.toLocaleString() + " " + d.currency;
    list.appendChild(li);
  }
  $("save").hidden = !canCallTools;
}
window.addEventListener("message", (event) => {
  if (event.source !== window.parent) return;
  const m = event.data; if (!m || m.jsonrpc !== "2.0") return;
  if (m.id !== undefined && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(m.error) : p.resolve(m.result);
  } else if (m.method === "ui/notifications/tool-result") { render(m.params && m.params.structuredContent); }
  else if (m.method === "ui/notifications/host-context-changed") { applyTheme(m.params); }
});
$("save").addEventListener("click", async () => {
  if (!current) return; $("status").textContent = "Saving...";
  try {
    const r = await request("tools/call", { name: "save_shortlist",
      arguments: { account_id: current.account_id, note: "Saved from the account dashboard" } });
    $("status").textContent = r.isError ? "Not saved" : "Saved to your shortlist";
  } catch (err) { $("status").textContent = "Not saved"; }
});
request("ui/initialize", { appInfo: { name: "account-dashboard", version: "1.4.0" }, appCapabilities: {} })
  .then((r) => { canCallTools = !!(r.hostCapabilities && r.hostCapabilities.serverTools);
    applyTheme(r.hostContext); send({ jsonrpc: "2.0", method: "ui/notifications/initialized", params: {} }); })
  .catch(() => { $("status").textContent = "Ask the agent for details about this account."; });
</script></body></html>
"""
WIDGET_META = {"ui": {"csp": {"connectDomains": [], "resourceDomains": ["https://cdn.contoso.com"]},
                      "prefersBorder": True}}


# --------------------------------------------------------------------------- bearer validation
class AuthError(Exception):
    """Token rejected. reason is a log-safe code; status is 401, or 403 for a missing scope."""

    def __init__(self, reason: str, status: int = 401, scope: str = "crm.read"):
        super().__init__(reason)
        self.reason, self.status, self.scope = reason, status, scope

    def www_authenticate(self) -> str:
        if self.status == 403:
            return f'Bearer error="insufficient_scope", scope="{self.scope}", resource_metadata="{RESOURCE_METADATA_URL}"'
        error = "" if self.reason == "missing" else 'error="invalid_token", '
        return f'Bearer {error}resource_metadata="{RESOURCE_METADATA_URL}"'


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def mint_dev_token(scopes: str = "crm.read crm.write", **claims) -> str:
    """Issue an HS256 token signed with the per-process dev key. Local runs and tests only."""
    now = int(time.time())
    body = {"iss": EXPECTED_ISS, "aud": EXPECTED_AUD, "tid": "contoso-org-0001", "sub": DEV_USER,
            "azp": "dev-client", "scp": scopes, "iat": now, "exp": now + 3600}
    body.update(claims)
    signing_input = (_b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode()) + "."
                     + _b64url(json.dumps(body).encode()))
    signature = hmac.new(DEV_SIGNING_KEY, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


def verify_signature(signing_input: bytes, signature: bytes, header: dict) -> bool:
    """Dev HS256 check. Production: verify RS256 with the issuer's JWKS key chosen by header['kid']."""
    if header.get("alg") != "HS256":
        return False
    expected = hmac.new(DEV_SIGNING_KEY, signing_input, hashlib.sha256).digest()
    return hmac.compare_digest(expected, signature)


def require_scope(claims: dict, scope: str) -> None:
    if scope not in str(claims.get("scp", "")).split():
        raise AuthError("scope", status=403, scope=scope)


def validate_bearer(authorization: str | None, required_scope: str = "crm.read") -> dict:
    """Return validated claims, or raise AuthError with a reason code. Never log the token itself."""
    if not authorization or not authorization.startswith("Bearer "):
        raise AuthError("missing")
    try:
        encoded_header, encoded_body, encoded_signature = authorization[7:].strip().split(".")
        header = json.loads(_b64url_decode(encoded_header))
        claims = json.loads(_b64url_decode(encoded_body))
        signature = _b64url_decode(encoded_signature)
    except (ValueError, TypeError):
        raise AuthError("bad_signature") from None
    if not verify_signature(f"{encoded_header}.{encoded_body}".encode(), signature, header):
        raise AuthError("bad_signature")
    if claims.get("exp", 0) <= time.time():
        raise AuthError("expired")
    audiences = claims.get("aud") if isinstance(claims.get("aud"), list) else [claims.get("aud")]
    if EXPECTED_AUD not in audiences:
        raise AuthError("bad_aud")
    if claims.get("iss") != EXPECTED_ISS:
        raise AuthError("bad_iss")
    if claims.get("tid") not in ALLOWED_TENANTS:
        raise AuthError("bad_tid")
    require_scope(claims, required_scope)
    return claims


# --------------------------------------------------------------------------- side-effect gate
REFUSALS = {  # code: (text for the user, detail for the model and logs)
    "writes_disabled": ("Changes are paused by an administrator. Read-only questions still work.",
                        "The write kill switch is on."),
    "daily_cap_exceeded": ("You reached today's limit for changes made through the agent. Try again tomorrow.",
                           "Per-user daily write cap reached."),
    "draft_required": ("Create a draft with draft_order first, then submit that draft.",
                       "submit_order needs a valid draft_id."),
    "draft_already_used": ("That draft was already submitted. Create a new draft to order again.",
                           "Each draft_id can be submitted once."),
    "approval_required": ("Messaging the account team needs an approval token from the approval step.",
                          "notify_account_team needs a valid approval_token for this account."),
}


def issue_approval_token(account_id: str) -> str:
    """Stands in for a separate human approval service. The agent never issues these."""
    return "APR-" + hmac.new(DEV_APPROVAL_KEY, account_id.encode(), hashlib.sha256).hexdigest()[:32]


def approval_valid(account_id: str, token: str) -> bool:
    return hmac.compare_digest(issue_approval_token(account_id).encode(), str(token).encode())


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:16]


class SideEffectGate:
    """Server-side control for W1-W4 tools. Confirmation cards are user experience; this is the control."""

    def __init__(self, daily_cap: int = DAILY_WRITE_CAP):
        self.daily_cap = daily_cap
        self.writes_enabled = True   # kill switch
        self.drafts: dict = {}       # draft_id -> draft record with a used flag
        self.counts: dict = {}       # (tid, sub, UTC day) -> successful writes
        self.audit: list = []

    @staticmethod
    def _cap_key(user: dict) -> tuple:
        return user.get("tid"), user.get("sub"), time.strftime("%Y-%m-%d", time.gmtime())

    def check(self, tool: str, args: dict, user: dict) -> str | None:
        """Return None to allow the call, or a refusal code."""
        tool_class = TOOL_CLASS[tool]
        if tool_class == "R":
            return None
        if not self.writes_enabled:
            return "writes_disabled"
        if self.counts.get(self._cap_key(user), 0) >= self.daily_cap:
            return "daily_cap_exceeded"
        if tool_class == "W3":
            draft = self.drafts.get(args.get("draft_id", ""))
            if draft is None or draft["owner"] != user.get("sub"):
                return "draft_required"
            if draft["used"]:
                return "draft_already_used"
        if tool_class == "W4" and not approval_valid(args.get("account_id", ""), args.get("approval_token", "")):
            return "approval_required"
        return None

    def record(self, tool, args, user, decision, correlation_id, receipt_id=None, outcome=None) -> None:
        if TOOL_CLASS[tool] == "R":
            return
        if decision == "allowed" and outcome == "ok":
            key = self._cap_key(user)
            self.counts[key] = self.counts.get(key, 0) + 1
        self.audit.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "tid": user.get("tid"),
                           "oid_hash": _hash(user.get("sub")), "tool": tool, "class": TOOL_CLASS[tool],
                           "args_sha256": _hash(args), "decision": decision, "outcome": outcome,
                           "receipt_id": receipt_id, "correlation_id": correlation_id})


# --------------------------------------------------------------------------- tools
def argument_problems(schema: dict, args: dict) -> list:
    """Minimal checks for the flat schemas this server publishes. Production: use a full validator."""
    properties = schema.get("properties", {})
    problems = [f"{name} is required" for name in schema.get("required", []) if name not in args]
    problems += [f"{name} is not a known argument" for name in args if name not in properties]
    for name, value in args.items():
        rule = properties.get(name, {})
        kind = rule.get("type")
        if kind == "string":
            if not isinstance(value, str):
                problems.append(f"{name} must be a string")
                continue
            if "pattern" in rule and not re.search(rule["pattern"], value):
                problems.append(f"{name} must match {rule['pattern']}")
            if len(value) > rule.get("maxLength", len(value)):
                problems.append(f"{name} must be at most {rule['maxLength']} characters")
            if rule.get("format") == "date" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                problems.append(f"{name} must be a date in YYYY-MM-DD format")
        elif kind in ("integer", "number"):
            numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
            if not numeric or (kind == "integer" and not float(value).is_integer()):
                problems.append(f"{name} must be {'an integer' if kind == 'integer' else 'a number'}")
                continue
            if value < rule.get("minimum", value) or value > rule.get("maximum", value):
                problems.append(f"{name} is out of range")
        if "enum" in rule and value not in rule["enum"]:
            problems.append(f"{name} must be one of: {', '.join(rule['enum'])}")
    return problems


def _not_found(detail: str):
    return "Not found. Check the ID and try again.", {"error": "not_found", "detail": detail}, True


class CrmTools:
    """Business logic. Each method returns (text, structured_data, is_error); user is the validated claims."""

    def __init__(self, gate: SideEffectGate):
        self.gate = gate
        self.shortlists: dict = {}

    def get_account_summary(self, args, user):
        account = ACCOUNTS.get(args["account_id"])
        if account is None:
            return _not_found(f"No account {args['account_id']}.")
        last = account["recent_orders"][0]["date"] if account["recent_orders"] else "none"
        text = (f"{account['name']} ({account['account_id']}): health {account['health_score']}/100, "
                f"{account['open_opportunities']} open opportunities worth {account['pipeline_value']:,} "
                f"{account['currency']}, last order {last}.")
        return text, json.loads(json.dumps(account)), False

    def search_opportunities(self, args, user):
        cursor = args.get("cursor", "0")
        if not cursor.isdigit():
            return "That cursor is not valid. Search again without a cursor.", {"error": "bad_cursor"}, True
        rows = [o for o in OPPORTUNITIES
                if (args.get("owner", "me") == "anyone" or o["owner"] == user.get("sub"))
                and o["stage"] == args.get("stage", o["stage"])
                and o["amount"] >= args.get("min_amount", 0)
                and o["close_date"] <= args.get("close_before", "9999-12-31")]
        start, limit = int(cursor), min(args.get("limit", 10), MAX_ITEMS)
        page = rows[start:start + limit]
        next_cursor = str(start + limit) if start + limit < len(rows) else None
        items = [{k: o[k] for k in ("opportunity_id", "account_id", "name", "stage", "amount", "close_date")}
                 for o in page]
        lines = [f"- {o['opportunity_id']} {o['name']} ({o['account_id']}), {o['stage']}, "
                 f"{o['amount']:,} USD, closes {o['close_date']}" for o in items]
        more = " More results are available with next_cursor." if next_cursor else ""
        text = "\n".join([f"{len(items)} of {len(rows)} matching opportunities.{more}"] + lines)
        return text, {"items": items, "total": len(rows), "next_cursor": next_cursor}, False

    def save_shortlist(self, args, user):
        if args["account_id"] not in ACCOUNTS:
            return _not_found(f"No account {args['account_id']}.")
        self.shortlists.setdefault((user.get("tid"), user.get("sub")), {})[args["account_id"]] = args["note"]
        return (f"Saved {args['account_id']} to your shortlist.",
                {"saved": True, "account_id": args["account_id"], "note": args["note"]}, False)

    def draft_order(self, args, user):
        if args["account_id"] not in ACCOUNTS or args["sku"] not in SKU_PRICES:
            return _not_found("Unknown account_id or sku. Confirm the SKU with the catalog.")
        for draft_id, draft in self.gate.drafts.items():  # idempotent: the same request returns the open draft
            if not draft["used"] and draft["owner"] == user.get("sub") and all(
                    draft[k] == args[k] for k in ("account_id", "sku", "quantity")):
                return self._draft_view(draft_id, draft)
        draft_id = "DRF-" + secrets.token_hex(4)
        draft = {"owner": user.get("sub"), "account_id": args["account_id"], "sku": args["sku"],
                 "quantity": int(args["quantity"]), "total": SKU_PRICES[args["sku"]] * args["quantity"],
                 "used": False}
        self.gate.drafts[draft_id] = draft
        return self._draft_view(draft_id, draft)

    @staticmethod
    def _draft_view(draft_id: str, draft: dict):
        data = {"draft_id": draft_id, "account_id": draft["account_id"], "status": "draft", "currency": "USD",
                "line_items": [{"sku": draft["sku"], "quantity": draft["quantity"],
                                "unit_price": SKU_PRICES[draft["sku"]]}],
                "estimated_total": draft["total"]}
        text = (f"Draft {draft_id}: {draft['quantity']} x {draft['sku']} for {draft['account_id']}, "
                f"estimated {draft['total']:,.2f} USD. Nothing has been sent to fulfillment.")
        return text, data, False

    def submit_order(self, args, user):
        draft = self.gate.drafts[args["draft_id"]]  # the gate already proved it exists and is unused
        draft["used"] = True
        order_id = f"ORD-{6000 + sum(1 for d in self.gate.drafts.values() if d['used'])}"
        return (f"Submitted draft {args['draft_id']} as order {order_id}.",
                {"order_id": order_id, "draft_id": args["draft_id"], "status": "submitted"}, False)

    def notify_account_team(self, args, user):
        account = ACCOUNTS.get(args["account_id"])
        if account is None:
            return _not_found(f"No account {args['account_id']}.")
        return (f"Message queued for the {account['name']} account team (4 members).",
                {"account_id": args["account_id"], "recipients": 4, "status": "queued"}, False)


# --------------------------------------------------------------------------- JSON-RPC dispatch
class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def rpc_error(msg_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def tool_result(text: str, data: dict, correlation_id: str, is_error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": text}], "structuredContent": data,
            "isError": is_error, "_meta": {"correlation_id": correlation_id}}


ANONYMOUS_USER = {"tid": "anonymous", "sub": "anonymous", "scp": "crm.read"}
SUPPORTED_VERSIONS = {PROTOCOL_VERSION, "2025-03-26"}
RESOURCES = [{"uri": WIDGET_URI, "name": "account-dashboard", "title": "Account dashboard",
              "description": "Interactive health, pipeline, and orders view for one account.",
              "mimeType": WIDGET_MIME}]


class McpServer:
    """Transport-independent MCP core. handle() takes a POST body and lower-cased headers and
    returns (http_status, response_headers, json_payload_or_None)."""

    def __init__(self, allow_anonymous: bool = False, daily_cap: int = DAILY_WRITE_CAP, verbose: bool = False):
        self.allow_anonymous = allow_anonymous
        self.gate = SideEffectGate(daily_cap)
        self.tools = CrmTools(self.gate)
        self.sessions: dict = {}  # Mcp-Session-Id -> (tid, sub); a session is bound to one user
        self.log: list = []       # one structured record per request; never contains tokens
        self.verbose = verbose
        self._lock = threading.Lock()

    def visible_tools(self) -> list:
        """Anonymous (Path C) mode exposes read-only tools only."""
        if self.allow_anonymous:
            return [tool for tool in TOOLS if TOOL_CLASS[tool["name"]] == "R"]
        return TOOLS

    def end_session(self, headers: dict) -> int:
        """DELETE /mcp. Only the authenticated user who owns the session may end it."""
        with self._lock:
            if self.allow_anonymous:
                user = ANONYMOUS_USER
            else:
                try:
                    user = validate_bearer(headers.get("authorization"), "crm.read")
                except AuthError as err:
                    return err.status
            session_id = headers.get("mcp-session-id", "")
            if self.sessions.get(session_id) != (user.get("tid"), user.get("sub")):
                return 404
            del self.sessions[session_id]
            return 204

    def handle(self, body: bytes, headers: dict) -> tuple:
        with self._lock:
            started = time.perf_counter()
            record = {"correlation_id": uuid.uuid4().hex, "mcp_session_id": headers.get("mcp-session-id"),
                      "method": None, "tool": None, "tid": None, "oid_hash": None,
                      "auth_decision": None, "gate_decision": None}
            status, response_headers, payload = self._handle(body, headers, record)
            record.update(status=status, latency_ms=round((time.perf_counter() - started) * 1000, 2))
            self.log.append(record)
            if self.verbose:
                print(json.dumps(record), file=sys.stderr)
            return status, response_headers, payload

    def _handle(self, body: bytes, headers: dict, record: dict) -> tuple:
        try:
            message = json.loads(body or b"null")
        except (ValueError, UnicodeDecodeError):
            return 400, {}, rpc_error(None, -32700, "Parse error")
        if isinstance(message, list):
            return 400, {}, rpc_error(None, -32600, "JSON-RPC batching is not supported in protocol 2025-06-18.")
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(
                message.get("method"), str):
            return 400, {}, rpc_error(None, -32600, "Invalid Request")
        msg_id, method = message.get("id"), message["method"]
        record["method"] = method
        version = headers.get("mcp-protocol-version")
        if version and version not in SUPPORTED_VERSIONS:
            return 400, {}, rpc_error(msg_id, -32600, f"Unsupported MCP-Protocol-Version: {version}")

        if self.allow_anonymous:
            user = dict(ANONYMOUS_USER)
            record["auth_decision"] = "anonymous"
        else:
            try:
                user = validate_bearer(headers.get("authorization"), "crm.read")
            except AuthError as err:
                record["auth_decision"] = err.reason
                return err.status, {"WWW-Authenticate": err.www_authenticate()}, rpc_error(
                    msg_id, -32001, "Authorization required.")
            record["auth_decision"] = "ok"
        record["tid"], record["oid_hash"] = user.get("tid"), _hash(user.get("sub"))

        response_headers = {}
        if method == "initialize":
            session_id = uuid.uuid4().hex
            self.sessions[session_id] = (user.get("tid"), user.get("sub"))
            response_headers["Mcp-Session-Id"] = session_id
        else:
            session_id = headers.get("mcp-session-id")
            if not session_id:
                return 400, {}, rpc_error(msg_id, -32600, "Mcp-Session-Id header is required.")
            if self.sessions.get(session_id) != (user.get("tid"), user.get("sub")):
                return 404, {}, rpc_error(msg_id, -32001, "Session not found. Initialize again.")
        if "id" not in message:  # notification, for example notifications/initialized
            return 202, response_headers, None

        try:
            result = self._dispatch(method, message.get("params"), user, record)
        except RpcError as err:
            return 200, response_headers, rpc_error(msg_id, err.code, err.message)
        except AuthError as err:  # a write tool called with a read-only token
            record["auth_decision"] = err.reason
            return err.status, {"WWW-Authenticate": err.www_authenticate()}, rpc_error(
                msg_id, -32001, "Insufficient scope.")
        return 200, response_headers, {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def _dispatch(self, method: str, params, user: dict, record: dict) -> dict:
        if params is not None and not isinstance(params, dict):
            raise RpcError(-32602, "params must be an object")
        params = params or {}
        if method == "initialize":
            requested = params.get("protocolVersion")
            return {"protocolVersion": requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}, "resources": {}},
                    "serverInfo": SERVER_INFO,
                    "instructions": "CRM tools for account summaries, opportunities, and orders. "
                                    "Draft an order before submitting it."}
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.visible_tools()}
        if method == "tools/call":
            return self._call_tool(params, user, record)
        if method == "resources/list":
            return {"resources": RESOURCES}
        if method == "resources/read":
            if params.get("uri") != WIDGET_URI:
                raise RpcError(-32002, "Resource not found")
            return {"contents": [{"uri": WIDGET_URI, "mimeType": WIDGET_MIME, "text": WIDGET_HTML,
                                  "_meta": WIDGET_META}]}
        raise RpcError(-32601, f"Method not found: {method}")

    def _call_tool(self, params: dict, user: dict, record: dict) -> dict:
        name, args = params.get("name"), params.get("arguments")
        args = {} if args is None else args
        if not isinstance(name, str) or not isinstance(args, dict):
            raise RpcError(-32602, "tools/call needs a string name and an object of arguments")
        if name not in {tool["name"] for tool in self.visible_tools()}:
            raise RpcError(-32602, f"Unknown tool: {name}")
        record["tool"], cid = name, record["correlation_id"]
        tool_class = TOOL_CLASS[name]
        if not self.allow_anonymous:
            require_scope(user, REQUIRED_SCOPE[tool_class])

        problems = argument_problems(TOOLS_BY_NAME[name]["inputSchema"], args)
        if problems:
            record["gate_decision"] = "invalid_arguments"
            return tool_result("Some arguments are not valid: " + "; ".join(problems) + ".",
                               {"error": "invalid_arguments", "problems": problems}, cid, True)

        refusal = self.gate.check(name, args, user)
        if refusal:
            record["gate_decision"] = refusal
            self.gate.record(name, args, user, refusal, cid, outcome="refused")
            text, detail = REFUSALS[refusal]
            return tool_result(text, {"error": refusal, "detail": detail, "refused_by": "side_effect_gate"},
                               cid, True)

        record["gate_decision"] = "allowed"
        text, data, is_error = getattr(self.tools, name)(args, user)
        receipt_id = None
        if tool_class != "R" and not is_error:
            receipt_id = "RCP-" + secrets.token_hex(6)
            data["receipt_id"] = receipt_id
        self.gate.record(name, args, user, "allowed", cid, receipt_id, "error" if is_error else "ok")
        result = tool_result(text, data, cid, is_error)
        if len(json.dumps(result).encode()) > MAX_RESULT_BYTES:
            return tool_result("The result was too large. Narrow the filters or use a smaller limit.",
                               {"error": "result_too_large"}, cid, True)
        return result


# --------------------------------------------------------------------------- HTTP transport
def protected_resource_metadata() -> dict:
    """RFC 9728 document that tells clients which authorization server issues tokens for this resource."""
    return {"resource": EXPECTED_AUD, "authorization_servers": [EXPECTED_ISS],
            "scopes_supported": ["crm.read", "crm.write"], "bearer_methods_supported": ["header"]}


class McpHttpHandler(BaseHTTPRequestHandler):
    """Streamable HTTP endpoint at /mcp. JSON responses only; this server never opens SSE streams."""

    server_version = "contoso-crm-mcp/1.4"
    mcp: McpServer = None  # set by serve()

    def _send(self, status: int, headers: dict | None = None, payload=None) -> None:
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _origin_allowed(self) -> bool:
        """Block DNS-rebinding from browsers. Server-to-server callers send no Origin header."""
        origin = self.headers.get("Origin")
        return origin is None or urlparse(origin).hostname in LOCAL_ORIGIN_HOSTS

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/mcp":
            return self._send(404, payload={"error": "not_found"})
        if not self._origin_allowed():
            return self._send(403, payload={"error": "origin_not_allowed"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, payload={"error": "bad_content_length"})
        if length > 1_000_000:
            return self._send(413, payload={"error": "request_too_large"})
        headers = {key.lower(): value for key, value in self.headers.items()}
        status, response_headers, payload = self.mcp.handle(self.rfile.read(length), headers)
        self._send(status, response_headers, payload)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/.well-known/oauth-protected-resource":
            return self._send(200, payload=protected_resource_metadata())
        if path == "/mcp":
            return self._send(405, {"Allow": "POST, DELETE"})
        self._send(404, payload={"error": "not_found"})

    def do_DELETE(self) -> None:
        if urlparse(self.path).path != "/mcp" or not self._origin_allowed():
            return self._send(404, payload={"error": "not_found"})
        self._send(self.mcp.end_session({key.lower(): value for key, value in self.headers.items()}))

    def log_message(self, fmt, *args) -> None:
        pass  # McpServer writes one structured record per request instead


def serve(host: str, port: int, allow_anonymous: bool) -> None:
    handler = type("BoundHandler", (McpHttpHandler,), {"mcp": McpServer(allow_anonymous, verbose=True)})
    httpd = ThreadingHTTPServer((host, port), handler)
    print(f"MCP endpoint: http://{host}:{port}/mcp  (protocol {PROTOCOL_VERSION})")
    if allow_anonymous:
        print("Anonymous mode (Path C): read-only tools only. Never deploy this mode.")
    else:
        print("Dev bearer token, valid for one hour and for this process only:")
        print(mint_dev_token())
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


# --------------------------------------------------------------------------- offline self-test
BANNED = {"oneOf", "anyOf", "allOf", "$ref", "$defs", "definitions", "not", "if", "patternProperties"}


def lint(schema, path="inputSchema", depth=0):
    """Stage 3 schema lint, identical to troubleshooting-and-diagnostics.md section 4.3."""
    problems = [f"{path}: uses {k}" for k in BANNED & schema.keys()]
    if isinstance(schema.get("type"), list):
        problems.append(f"{path}: type array")
    if depth > 3:
        problems.append(f"{path}: nesting deeper than 3")
    for name, sub in schema.get("properties", {}).items():
        problems += lint(sub, f"{path}.{name}", depth + 1)
    if isinstance(schema.get("items"), dict):
        problems += lint(schema["items"], f"{path}[]", depth + 1)
    return problems


PASSED: list = []


def check(label: str, condition, detail="") -> None:
    if not condition:
        raise AssertionError(f"FAIL {label} {detail}".rstrip())
    PASSED.append(label)
    print(f"PASS {label}")


class _Client:
    """In-process MCP client that drives McpServer.handle() without a socket."""

    def __init__(self, server: McpServer, token: str | None = None):
        self.server, self.token, self.session, self.next_id = server, token, None, 0

    def post(self, method: str, params=None, notify: bool = False, headers: dict | None = None) -> tuple:
        message = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        if not notify:
            self.next_id += 1
            message["id"] = self.next_id
        sent = {"mcp-protocol-version": PROTOCOL_VERSION}
        if self.token:
            sent["authorization"] = "Bearer " + self.token
        if self.session:
            sent["mcp-session-id"] = self.session
        sent.update(headers or {})
        status, response_headers, payload = self.server.handle(json.dumps(message).encode(), sent)
        self.session = response_headers.get("Mcp-Session-Id", self.session)
        return status, response_headers, payload

    def connect(self) -> tuple:
        result = self.post("initialize", {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                                          "clientInfo": {"name": "self-test", "version": "1.0"}})
        self.post("notifications/initialized", notify=True)
        return result

    def call(self, name: str, **arguments) -> dict:
        payload = self.post("tools/call", {"name": name, "arguments": arguments})[2]
        return payload.get("result", payload)


def _error_code(result: dict):
    return (result.get("structuredContent") or {}).get("error")


def _test_protocol(server: McpServer, client: _Client) -> list:
    status, headers, init = client.connect()
    check("initialize negotiates 2025-06-18 and issues Mcp-Session-Id",
          status == 200 and init["result"]["protocolVersion"] == PROTOCOL_VERSION and client.session)
    check("notifications/initialized returns 202 with no body", server.log[-1]["status"] == 202)
    check("unknown session returns 404", client.post("ping", headers={"mcp-session-id": "x"})[0] == 404)
    check("unsupported MCP-Protocol-Version returns 400",
          client.post("ping", headers={"mcp-protocol-version": "1999-01-01"})[0] == 400)
    batch = server.handle(b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]', {})
    check("JSON-RPC batch is rejected with -32600", batch[0] == 400 and batch[2]["error"]["code"] == -32600)
    check("unknown method returns -32601", client.post("no/such/method")[2]["error"]["code"] == -32601)
    check("unknown tool returns -32602", client.post("tools/call", {"name": "nope"})[2]["error"]["code"] == -32602)

    tools = client.post("tools/list")[2]["result"]["tools"]
    problems = [p for tool in tools for p in lint(tool["inputSchema"], tool["name"])]
    check("tools/list passes the Stage 3 schema lint", len(tools) == 6 and not problems, problems)
    if CATALOG_PATH.exists():
        catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["tools"]
        check("tools/list matches mcp-tools-catalog.json exactly", catalog == tools)
    for tool in tools:
        hints = tool["annotations"]
        expect_read = TOOL_CLASS[tool["name"]] == "R"
        expect_destructive = TOOL_CLASS[tool["name"]] in ("W3", "W4")
        check(f"annotations match class {TOOL_CLASS[tool['name']]} for {tool['name']}",
              hints["readOnlyHint"] is expect_read and hints.get("destructiveHint", False) is expect_destructive)

    listed = {resource["uri"] for resource in client.post("resources/list")[2]["result"]["resources"]}
    bound = {tool["_meta"]["ui"]["resourceUri"] for tool in tools if "resourceUri" in tool.get("_meta", {}).get("ui", {})}
    check("every _meta.ui.resourceUri appears in resources/list", bound and bound <= listed)
    view = client.post("resources/read", {"uri": WIDGET_URI})[2]["result"]["contents"][0]
    check("resources/read returns the MCP App MIME type and a CSP",
          view["mimeType"] == WIDGET_MIME and "csp" in view["_meta"]["ui"] and "innerHTML" not in view["text"])
    check("unknown resource returns -32002",
          client.post("resources/read", {"uri": "ui://nope"})[2]["error"]["code"] == -32002)
    return tools


def _test_auth() -> None:
    def attempt(authorization):
        server = McpServer()
        headers = {"authorization": authorization} if authorization else {}
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}).encode()
        status, response_headers, _ = server.handle(body, headers)
        return status, server.log[-1]["auth_decision"], response_headers.get("WWW-Authenticate", "")

    unsigned = mint_dev_token().rsplit(".", 1)[0] + "." + _b64url(bytes(32))
    cases = [("missing", None), ("expired", mint_dev_token(exp=int(time.time()) - 60)),
             ("bad_aud", mint_dev_token(aud="https://other-api.contoso.com")),
             ("bad_iss", mint_dev_token(iss="https://login.example.com")),
             ("bad_tid", mint_dev_token(tid="other-org-0002")),
             ("bad_signature", unsigned), ("bad_signature", "not-a-jwt")]
    for reason, token in cases:
        status, decision, challenge = attempt("Bearer " + token if token else None)
        check(f"token rejected with reason {reason}",
              status == 401 and decision == reason and "resource_metadata=" in challenge, decision)

    reader = _Client(McpServer(), mint_dev_token(scopes="crm.read"))
    reader.connect()
    check("read-only token can call an R tool", not reader.call("get_account_summary", account_id="ACC-1001")["isError"])
    status, headers, _ = reader.post("tools/call", {"name": "save_shortlist",
                                                    "arguments": {"account_id": "ACC-1001", "note": "x"}})
    check("read-only token gets 403 insufficient_scope for a W tool",
          status == 403 and 'error="insufficient_scope"' in headers["WWW-Authenticate"])

    anonymous = _Client(McpServer(allow_anonymous=True))
    anonymous.connect()
    names = {tool["name"] for tool in anonymous.post("tools/list")[2]["result"]["tools"]}
    check("anonymous mode lists R tools only", names == {"get_account_summary", "search_opportunities"})
    refused = anonymous.post("tools/call", {"name": "save_shortlist", "arguments": {"account_id": "ACC-1001",
                                                                                     "note": "x"}})[2]
    check("anonymous mode cannot call W tools", refused["error"]["code"] == -32602)


def _test_tools_and_gate(server: McpServer, client: _Client) -> None:
    summary = client.call("get_account_summary", account_id="ACC-1001")
    check("account summary text", summary["content"][0]["text"] == "Fabrikam Retail (ACC-1001): health 82/100, "
          "3 open opportunities worth 410,000 USD, last order 2026-10-14.", summary["content"][0]["text"])
    check("result carries the request correlation_id",
          summary["_meta"]["correlation_id"] == server.log[-1]["correlation_id"])
    check("result stays under 20 KB", len(json.dumps(summary).encode()) < MAX_RESULT_BYTES)
    check("schema-invalid arguments return isError invalid_arguments",
          _error_code(client.call("get_account_summary", account_id="1001")) == "invalid_arguments")
    first = client.call("search_opportunities", owner="anyone", limit=3)["structuredContent"]
    rest = client.call("search_opportunities", owner="anyone", limit=3, cursor=first["next_cursor"])
    check("search pages with next_cursor", len(first["items"]) == 3 and first["next_cursor"] == "3"
          and rest["structuredContent"]["next_cursor"] is None and len(rest["structuredContent"]["items"]) == 1)

    t5 = client.call("submit_order", draft_id="DRF-00000000")
    check("T5 submit without a draft is refused with draft_required", _error_code(t5) == "draft_required"
          and t5["content"][0]["text"] == "Create a draft with draft_order first, then submit that draft.")
    draft = client.call("draft_order", account_id="ACC-1001", sku="SKU-778", quantity=10)["structuredContent"]
    again = client.call("draft_order", account_id="ACC-1001", sku="SKU-778", quantity=10)["structuredContent"]
    check("draft_order is idempotent for the same request", draft["draft_id"] == again["draft_id"])
    other = _Client(server, mint_dev_token(sub="user-0002"))
    other.connect()
    check("another user cannot submit someone else's draft",
          _error_code(other.call("submit_order", draft_id=draft["draft_id"])) == "draft_required")
    t6 = client.call("submit_order", draft_id=draft["draft_id"])
    receipt = t6["structuredContent"].get("receipt_id")
    check("T6 submit with a valid draft returns one receipt", not t6["isError"] and receipt
          and sum(1 for row in server.gate.audit if row["receipt_id"] == receipt) == 1)
    check("T7 replaying a draft is refused with draft_already_used",
          _error_code(client.call("submit_order", draft_id=draft["draft_id"])) == "draft_already_used")
    t8 = client.call("notify_account_team", account_id="ACC-1001", message="Renewal call moved.",
                     approval_token="APR-0000")
    check("T8 notify without approval is refused with approval_required",
          _error_code(t8) == "approval_required" and server.gate.audit[-1]["decision"] == "approval_required")
    approved = client.call("notify_account_team", account_id="ACC-1001", message="Renewal call moved.",
                           approval_token=issue_approval_token("ACC-1001"))
    check("notify with a valid approval token succeeds", not approved["isError"])

    capped = _Client(McpServer(daily_cap=2), mint_dev_token())
    capped.connect()
    for _ in range(2):
        capped.call("save_shortlist", account_id="ACC-1002", note="Follow up")
    check("T9 the per-user daily cap refuses with daily_cap_exceeded",
          _error_code(capped.call("save_shortlist", account_id="ACC-1002", note="Again")) == "daily_cap_exceeded")
    server.gate.writes_enabled = False
    check("T10 kill switch refuses W calls with writes_disabled",
          _error_code(client.call("save_shortlist", account_id="ACC-1001", note="x")) == "writes_disabled")
    check("T10 kill switch leaves R calls working",
          not client.call("get_account_summary", account_id="ACC-1002")["isError"])
    server.gate.writes_enabled = True
    check("logs and audit rows never contain the bearer token",
          client.token not in json.dumps(server.log) + json.dumps(server.gate.audit))


def _test_http() -> None:
    import urllib.request
    from urllib.error import HTTPError

    handler = type("TestHandler", (McpHttpHandler,), {"mcp": McpServer()})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, method="GET", body=None, headers=None):
        req = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
        try:
            with opener.open(req, timeout=5) as response:
                return response.status, response.headers, response.read()
        except HTTPError as err:
            return err.code, err.headers, err.read()

    init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
        "clientInfo": {"name": "http-test", "version": "1.0"}}}).encode()
    sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    authorized = {**sent, "Authorization": "Bearer " + mint_dev_token()}
    try:
        status, _, body = request("/.well-known/oauth-protected-resource")
        check("HTTP protected resource metadata names the issuer",
              status == 200 and json.loads(body)["authorization_servers"] == [EXPECTED_ISS])
        check("HTTP GET /mcp returns 405", request("/mcp")[0] == 405)
        status, headers, _ = request("/mcp", "POST", init, sent)
        check("HTTP POST without a token returns 401 with a challenge",
              status == 401 and "resource_metadata=" in (headers.get("WWW-Authenticate") or ""))
        status, headers, body = request("/mcp", "POST", init, authorized)
        session = headers.get("Mcp-Session-Id")
        check("HTTP initialize succeeds with a dev token",
              status == 200 and session and json.loads(body)["result"]["serverInfo"] == SERVER_INFO)
        check("HTTP foreign Origin returns 403",
              request("/mcp", "POST", init, {**authorized, "Origin": "https://attacker.example"})[0] == 403)
        check("HTTP DELETE without a token returns 401 and keeps the session",
              request("/mcp", "DELETE", headers={"Mcp-Session-Id": session})[0] == 401)
        check("HTTP DELETE ends the session",
              request("/mcp", "DELETE", headers={**authorized, "Mcp-Session-Id": session})[0] == 204)
    finally:
        httpd.shutdown()
        httpd.server_close()


def self_test() -> int:
    server = McpServer()
    client = _Client(server, mint_dev_token())
    try:
        _test_protocol(server, client)
        _test_auth()
        _test_tools_and_gate(server, client)
        _test_http()
    except (AssertionError, KeyError, TypeError) as err:
        print(err if isinstance(err, AssertionError) else f"FAIL unexpected {type(err).__name__}: {err}")
        return 1
    print(f"\nAll {len(PASSED)} checks passed.")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Reference MCP server for the M365 Copilot integration pattern.")
    parser.add_argument("--serve", action="store_true", help="run the HTTP server instead of the self-test")
    parser.add_argument("--host", default="127.0.0.1", help="bind address; keep 127.0.0.1 for local work")
    parser.add_argument("--port", type=int, default=8080, help="port for --serve (default 8080)")
    parser.add_argument("--allow-anonymous", action="store_true", help="Path C dev mode: R tools only, no token")
    options = parser.parse_args(argv)
    if options.serve:
        serve(options.host, options.port, options.allow_anonymous)
        return 0
    return self_test()


if __name__ == "__main__":
    sys.exit(main())
