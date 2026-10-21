#!/usr/bin/env python3
"""Evidence ledger and run certificate: a stdlib-only reference sample.

An answer that only sounds right is not a finished task. This sample shows the
mechanism a harness uses to prove what happened during an agent run:

* an append-only, hash-chained evidence ledger that stores digests and
  references instead of raw content;
* a signed run certificate that binds the final artifact digest to the ledger
  head, the verification receipt, the approval decision, the policy profile,
  and the model and agent versions;
* offline verification that anyone holding a trusted key can run.

Everything runs in memory. The Azure adapters (Cosmos DB ledger store, Blob
Storage archive, Key Vault signers) are stubs that raise NotImplementedError.
Their docstrings describe what a production adapter must do.

Usage:
    python evidence_ledger.py --self-test
    python evidence_ledger.py --list-domains
    python evidence_ledger.py --domain prior-auth
    python evidence_ledger.py --domain financial-report --fail-check recompute.totals
    python evidence_ledger.py --domain case-decision --no-approval --json
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import hmac
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol, Sequence, runtime_checkable

LEDGER_ENTRY_SCHEMA = "ledger-entry/v1"
CERTIFICATE_SCHEMA = "run-certificate/v1"
RECEIPT_SCHEMA = "verification-receipt/v1"
APPROVAL_SCHEMA = "approval-decision/v1"
ARTIFACT_SCHEMA = "artifact/v1"
GENESIS_HASH = "sha256:" + "0" * 64

HARNESS_ACTOR = "harness://evidence-sample"
POLICY_ACTOR = "policy://bounded-autonomy"
VERIFIER_VERSION = "1.0.0"
VERIFIER_ACTOR = f"verifier://sample-verifier/{VERIFIER_VERSION}"
APPROVAL_ACTOR = "approval://verify-before-approve"
PROFILE_VERSION = "2026-10-01"
AGENT_VERSION = "1.4.0"
MODEL_DEPLOYMENT = "<model-deployment>"
MODEL_VERSION = "<model-version>"


# ---------------------------------------------------------------------------
# Canonical JSON and digests
# ---------------------------------------------------------------------------


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise TypeError(f"floats are not allowed in canonical JSON ({path}); encode decimals as strings")
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"object keys must be strings ({path})")
            _reject_floats(item, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _reject_floats(item, f"{path}[{index}]")


def canonical(value: Any) -> bytes:
    """Sorted keys, compact separators, UTF-8, no floats, no NaN."""
    _reject_floats(value)
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def digest_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def digest_obj(value: Any) -> str:
    return digest_bytes(canonical(value))


def short(value: str | None) -> str:
    if not value:
        return "-"
    return value.split(":", 1)[-1][:12]


def _hex16(digest: str) -> str:
    return digest.split(":", 1)[-1][:16]


# ---------------------------------------------------------------------------
# Ledger entries
# ---------------------------------------------------------------------------

# Only these payload fields may be kept in an entry's redacted body. Everything
# else (tool arguments, tool results, model text, source content, requester
# identity) is represented only by the payload digest.
REDACTION_ALLOWLIST: dict[str, frozenset[str]] = {
    "run_started": frozenset({"profile_id", "profile_version", "agent_id", "agent_version"}),
    "policy_decision": frozenset({"action", "outcome", "rule_id", "profile_version"}),
    "tool_call_request": frozenset({"action", "policy_decision_seq"}),
    "tool_call_response": frozenset({"action", "status"}),
    "source_retrieved": frozenset({"source_id", "uri", "content_digest", "classification"}),
    "model_output": frozenset({"model_deployment"}),
    "artifact_produced": frozenset({"artifact_digest", "media_type", "artifact_ref"}),
    "verifier_result": frozenset({"schema", "verifier_version", "artifact_sha256", "verdict", "receipt_id"}),
    "approval_decision": frozenset({"schema", "decision_id", "decision", "artifact_sha256", "approver"}),
    "run_closed": frozenset({"status"}),
}

HEADER_FIELDS = (
    "schema",
    "run_id",
    "seq",
    "type",
    "at",
    "actor",
    "trace_id",
    "payload_digest",
    "payload_ref",
    "redacted_digest",
    "prev_hash",
)


class LedgerError(Exception):
    """The ledger rejected an append."""


class StaleHeadError(LedgerError):
    """Another writer moved the head; re-read and retry."""


class LedgerSealedError(LedgerError):
    """The run is closed and sealed; no further appends are allowed."""


class ImmutableWriteError(Exception):
    """Write-once storage refused to overwrite an existing object."""


@dataclass(frozen=True)
class Finding:
    code: str
    seq: int | None = None
    detail: str = ""

    def __str__(self) -> str:
        where = f" seq={self.seq}" if self.seq is not None else ""
        return f"{self.code}{where}: {self.detail}"


def redact(entry_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    allowed = REDACTION_ALLOWLIST[entry_type]
    return {key: copy.deepcopy(value) for key, value in payload.items() if key in allowed}


def entry_header(entry: Mapping[str, Any]) -> dict[str, Any]:
    return {name: entry.get(name) for name in HEADER_FIELDS}


def build_entry(
    *,
    run_id: str,
    seq: int,
    entry_type: str,
    at: str,
    actor: str,
    trace_id: str,
    payload: Mapping[str, Any],
    payload_ref: str | None,
    prev_hash: str,
) -> dict[str, Any]:
    """Build one ledger entry. The full payload is hashed, never stored."""
    if entry_type not in REDACTION_ALLOWLIST:
        raise LedgerError(f"unknown entry type {entry_type!r}")
    redacted = redact(entry_type, payload)
    header = {
        "schema": LEDGER_ENTRY_SCHEMA,
        "run_id": run_id,
        "seq": seq,
        "type": entry_type,
        "at": at,
        "actor": actor,
        "trace_id": trace_id,
        "payload_digest": digest_obj(payload),
        "payload_ref": payload_ref,
        "redacted_digest": digest_obj(redacted),
        "prev_hash": prev_hash,
    }
    entry = dict(header)
    entry["entry_hash"] = digest_obj(header)
    # The redacted body is outside entry_hash and bound through redacted_digest,
    # so it can be purged for retention or erasure without breaking the chain.
    entry["redacted"] = redacted
    return entry


def strip_bodies(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Return copies with redacted bodies purged. The chain stays verifiable."""
    stripped = copy.deepcopy([dict(entry) for entry in entries])
    for entry in stripped:
        entry["redacted"] = None
    return stripped


def verify_chain(entries: Sequence[Mapping[str, Any]], run_id: str | None = None) -> list[Finding]:
    """Recompute the chain. Tail truncation needs the certificate's head and count."""
    if not entries:
        return [Finding("ledger.empty", None, "ledger has no entries")]
    findings: list[Finding] = []
    prev_claimed = GENESIS_HASH
    prev_at: str | None = None
    for index, entry in enumerate(entries):
        seq = entry.get("seq")
        entry_type = entry.get("type")
        if entry.get("schema") != LEDGER_ENTRY_SCHEMA:
            findings.append(Finding("ledger.schema", seq, f"unexpected schema {entry.get('schema')!r}"))
        if seq != index + 1:
            findings.append(Finding("ledger.sequence", seq, f"expected seq {index + 1}"))
        if run_id is not None and entry.get("run_id") != run_id:
            findings.append(Finding("ledger.run_id", seq, "entry belongs to a different run"))
        if entry_type not in REDACTION_ALLOWLIST:
            findings.append(Finding("ledger.type", seq, f"unknown entry type {entry_type!r}"))
        if entry.get("prev_hash") != prev_claimed:
            findings.append(Finding("ledger.prev_hash", seq, "does not link to the previous entry"))
        try:
            recomputed = digest_obj(entry_header(entry))
        except TypeError as exc:
            recomputed = f"invalid header: {exc}"
        if recomputed != entry.get("entry_hash"):
            findings.append(Finding("ledger.entry_hash", seq, "header does not match entry_hash"))
        body = entry.get("redacted")
        if body is not None:
            if not isinstance(body, Mapping):
                findings.append(Finding("ledger.redacted_digest", seq, "redacted body is not an object"))
            else:
                if digest_obj(body) != entry.get("redacted_digest"):
                    findings.append(Finding("ledger.redacted_digest", seq, "body does not match redacted_digest"))
                extra = sorted(set(body) - REDACTION_ALLOWLIST.get(entry_type, frozenset()))
                if extra:
                    findings.append(Finding("ledger.redaction_policy", seq, f"fields not allowlisted: {extra}"))
        at = entry.get("at")
        if isinstance(at, str):
            if prev_at is not None and at < prev_at:
                findings.append(Finding("ledger.timestamp", seq, "timestamp moves backwards"))
            prev_at = at
        prev_claimed = entry.get("entry_hash")
    if entries[0].get("type") != "run_started":
        findings.append(Finding("ledger.first_entry", entries[0].get("seq"), "first entry must be run_started"))
    for entry in entries[:-1]:
        if entry.get("type") == "run_closed":
            findings.append(Finding("ledger.closed_not_last", entry.get("seq"), "entries follow run_closed"))
    return findings


# ---------------------------------------------------------------------------
# Seams: ledger store, evidence archive, signer
# ---------------------------------------------------------------------------


@runtime_checkable
class LedgerStore(Protocol):
    def append(self, run_id: str, entry: Mapping[str, Any], expected_head: str) -> None: ...

    def read(self, run_id: str) -> list[dict[str, Any]]: ...

    def head(self, run_id: str) -> tuple[str, int]: ...

    def seal(self, run_id: str) -> None: ...

    def is_sealed(self, run_id: str) -> bool: ...


class InMemoryLedgerStore:
    """Append-only store with optimistic concurrency on the head hash."""

    def __init__(self) -> None:
        self._entries: dict[str, list[dict[str, Any]]] = {}
        self._sealed: set[str] = set()

    def append(self, run_id: str, entry: Mapping[str, Any], expected_head: str) -> None:
        if run_id in self._sealed:
            raise LedgerSealedError(f"run {run_id} is sealed")
        current_head, count = self.head(run_id)
        if expected_head != current_head:
            raise StaleHeadError(f"expected head {short(expected_head)}, current head {short(current_head)}")
        if entry.get("run_id") != run_id:
            raise LedgerError("entry run_id does not match the target run")
        if entry.get("prev_hash") != current_head:
            raise LedgerError("entry prev_hash does not match the current head")
        if entry.get("seq") != count + 1:
            raise LedgerError("entry seq is not the next sequence number")
        self._entries.setdefault(run_id, []).append(copy.deepcopy(dict(entry)))

    def read(self, run_id: str) -> list[dict[str, Any]]:
        return copy.deepcopy(self._entries.get(run_id, []))

    def head(self, run_id: str) -> tuple[str, int]:
        entries = self._entries.get(run_id, [])
        if not entries:
            return GENESIS_HASH, 0
        return entries[-1]["entry_hash"], len(entries)

    def seal(self, run_id: str) -> None:
        self._sealed.add(run_id)

    def is_sealed(self, run_id: str) -> bool:
        return run_id in self._sealed


class CosmosDbLedgerStore:
    """Production adapter sketch for Azure Cosmos DB for NoSQL. Not implemented.

    Suggested shape (confirm against the current azure-cosmos SDK documentation):

    * one container with partition key ``/run_id`` so a run's entries share a
      logical partition;
    * entry items with id ``{run_id}:{seq:08d}`` plus one ``head`` item per run
      holding ``head_hash``, ``count``, and ``sealed``;
    * append as a transactional batch in the run's partition: create the entry
      item and replace the head item with an ETag if-match condition. A failed
      precondition means another writer moved the head: raise StaleHeadError;
    * authenticate with Microsoft Entra ID (for example DefaultAzureCredential)
      and disable key-based auth on the account;
    * the writer identity belongs to the harness, never to the model or a tool.
    """

    def __init__(self, endpoint: str, database: str, container: str) -> None:
        self.endpoint = endpoint
        self.database = database
        self.container = container

    def _not_implemented(self) -> NotImplementedError:
        return NotImplementedError("CosmosDbLedgerStore is a production adapter sketch; see its docstring")

    def append(self, run_id: str, entry: Mapping[str, Any], expected_head: str) -> None:
        raise self._not_implemented()

    def read(self, run_id: str) -> list[dict[str, Any]]:
        raise self._not_implemented()

    def head(self, run_id: str) -> tuple[str, int]:
        raise self._not_implemented()

    def seal(self, run_id: str) -> None:
        raise self._not_implemented()

    def is_sealed(self, run_id: str) -> bool:
        raise self._not_implemented()


@runtime_checkable
class EvidenceArchive(Protocol):
    def put(self, name: str, data: bytes) -> str: ...

    def get(self, ref: str) -> bytes: ...


class InMemoryEvidenceArchive:
    """Write-once archive. A second put to the same name is refused."""

    PREFIX = "memory://evidence/"

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, name: str, data: bytes) -> str:
        if name in self._objects:
            raise ImmutableWriteError(f"{name} already exists and is immutable")
        self._objects[name] = bytes(data)
        return self.PREFIX + name

    def get(self, ref: str) -> bytes:
        return self._objects[ref.removeprefix(self.PREFIX)]


class BlobEvidenceArchive:
    """Production adapter sketch for Azure Blob Storage. Not implemented.

    Suggested shape (confirm against current Azure Storage immutability docs):

    * enable blob versioning and version-level immutability (WORM) on the
      container; apply a time-based retention policy and use legal hold for
      disputes or investigations;
    * upload with overwrite disabled and record the returned version id next to
      the reference, so readers fetch exactly the sealed version;
    * keep the retention policy unlocked while testing. A locked policy cannot
      be shortened or removed, only extended;
    * the sealer identity needs write access; auditors get read-only access.
    """

    def __init__(self, account_url: str, container: str) -> None:
        self.account_url = account_url
        self.container = container

    def put(self, name: str, data: bytes) -> str:
        raise NotImplementedError("BlobEvidenceArchive is a production adapter sketch; see its docstring")

    def get(self, ref: str) -> bytes:
        raise NotImplementedError("BlobEvidenceArchive is a production adapter sketch; see its docstring")


@runtime_checkable
class Signer(Protocol):
    alg: str
    key_id: str

    def sign(self, message: bytes) -> str: ...

    def verify(self, message: bytes, signature: str) -> bool: ...


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class InMemoryHmacSigner:
    """HS256 over the signing input. The default key is a sample-only constant."""

    alg = "HS256"

    def __init__(self, key: bytes | None = None, key_id: str = "memory://keys/run-certificate/v1") -> None:
        self._key = key if key is not None else hashlib.sha256(b"sample-only-not-for-production").digest()
        self.key_id = key_id

    def sign(self, message: bytes) -> str:
        return b64url(hmac.new(self._key, message, hashlib.sha256).digest())

    def verify(self, message: bytes, signature: str) -> bool:
        if not isinstance(signature, str) or not signature.isascii():
            return False
        return hmac.compare_digest(self.sign(message).encode("ascii"), signature.encode("ascii"))


class KeyVaultSecretHmacSigner:
    """HMAC with a key stored as an Azure Key Vault secret. Not implemented.

    The adapter would fetch the secret with ``SecretClient`` and compute HMAC
    locally. The key material leaves the vault and every verifier needs the
    same secret, so verification is not public. Prefer KeyVaultKeySigner when
    auditors or downstream gates must verify certificates independently.
    """

    alg = "HS256"

    def __init__(self, vault_url: str, secret_name: str) -> None:
        self.vault_url = vault_url
        self.key_id = f"{vault_url.rstrip('/')}/secrets/{secret_name}"

    def sign(self, message: bytes) -> str:
        raise NotImplementedError("KeyVaultSecretHmacSigner is a production adapter sketch; see its docstring")

    def verify(self, message: bytes, signature: str) -> bool:
        raise NotImplementedError("KeyVaultSecretHmacSigner is a production adapter sketch; see its docstring")


class KeyVaultKeySigner:
    """Asymmetric signing with an Azure Key Vault EC or RSA key. Not implemented.

    Sketch (confirm against the current azure-keyvault-keys documentation):
    ``CryptographyClient(key_id, DefaultAzureCredential()).sign(
    SignatureAlgorithm.es256, hashlib.sha256(message).digest())``. The private
    key never leaves the vault. Verifiers fetch the public key once (for
    example with ``KeyClient.get_key``), publish it, and verify offline. Use a
    versioned ``key_id`` so rotation never invalidates older certificates.
    """

    alg = "ES256"

    def __init__(self, key_id: str) -> None:
        self.key_id = key_id

    def sign(self, message: bytes) -> str:
        raise NotImplementedError("KeyVaultKeySigner is a production adapter sketch; see its docstring")

    def verify(self, message: bytes, signature: str) -> bool:
        raise NotImplementedError("KeyVaultKeySigner is a production adapter sketch; see its docstring")


# ---------------------------------------------------------------------------
# Clock and ledger writer
# ---------------------------------------------------------------------------


class SampleClock:
    """Deterministic UTC clock so sample output is reproducible."""

    def __init__(self, start: datetime | None = None, step: timedelta = timedelta(seconds=1)) -> None:
        self._now = start or datetime(2026, 10, 21, 9, 0, 0, tzinfo=timezone.utc)
        self._step = step

    def now(self) -> str:
        value = self._now.strftime("%Y-%m-%dT%H:%M:%SZ")
        self._now += self._step
        return value


class EvidenceLedger:
    """Harness-owned writer. The model never holds this object or its identity."""

    def __init__(self, store: LedgerStore, run_id: str, trace_id: str, clock: SampleClock) -> None:
        self.store = store
        self.run_id = run_id
        self.trace_id = trace_id
        self.clock = clock

    def append(
        self,
        entry_type: str,
        actor: str,
        payload: Mapping[str, Any],
        payload_ref: str | None = None,
    ) -> dict[str, Any]:
        head, count = self.store.head(self.run_id)
        entry = build_entry(
            run_id=self.run_id,
            seq=count + 1,
            entry_type=entry_type,
            at=self.clock.now(),
            actor=actor,
            trace_id=self.trace_id,
            payload=payload,
            payload_ref=payload_ref,
            prev_hash=head,
        )
        self.store.append(self.run_id, entry, expected_head=head)
        return entry


# ---------------------------------------------------------------------------
# Domain Packs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CheckSpec:
    code: str
    kind: str  # recompute | grounding | required_field | no_marker
    field_name: str | None = None


@dataclass(frozen=True)
class DomainPack:
    key: str
    industry: str
    organization: str
    title: str
    marker: str
    agent_id: str
    profile_id: str
    initiated_by: str
    approver: str
    sources: tuple[dict[str, str], ...]
    claims: tuple[dict[str, Any], ...]
    lines: tuple[str, ...]
    total: str
    fields: tuple[tuple[str, str], ...]
    checks: tuple[CheckSpec, ...]
    blocked_action: str


DOMAIN_PACKS: dict[str, DomainPack] = {
    "financial-report": DomainPack(
        key="financial-report",
        industry="Financial services",
        organization="Contoso Bank",
        title="Quarterly liquidity risk summary",
        marker="ACCT-SAMPLE-0042",
        agent_id="risk-summary-writer",
        profile_id="profile:financial-reporting",
        initiated_by="principal:analyst-01",
        approver="principal:risk-reviewer-07",
        sources=(
            {
                "source_id": "src-liquidity-extract",
                "uri": "evidence://contoso-bank/finance/liquidity-extract-q3.csv",
                "classification": "confidential",
                "content": "segment,outflow\ncorporate-a,1250000.00\ncorporate-b,830000.50\nACCT-SAMPLE-0042,415000.25",
            },
            {
                "source_id": "src-liquidity-policy",
                "uri": "evidence://contoso-bank/policy/liquidity-policy-v7.pdf",
                "classification": "internal",
                "content": "Liquidity coverage must stay above the internal floor at every month end.",
            },
        ),
        claims=(
            {"text": "Liquidity coverage stayed above the internal floor.", "source_ids": ["src-liquidity-extract", "src-liquidity-policy"]},
            {"text": "Net outflows were concentrated in two corporate segments.", "source_ids": ["src-liquidity-extract"]},
        ),
        lines=("1250000.00", "830000.50", "415000.25"),
        total="2495000.75",
        fields=(("distribution_list", "risk-committee@contoso.com"), ("reporting_period", "2026-Q3")),
        checks=(
            CheckSpec("recompute.totals", "recompute"),
            CheckSpec("grounding.citations", "grounding"),
            CheckSpec("disclosure.distribution_list", "required_field", "distribution_list"),
        ),
        blocked_action="send_external_email",
    ),
    "prior-auth": DomainPack(
        key="prior-auth",
        industry="Healthcare",
        organization="Fabrikam Health",
        title="Prior authorization summary",
        marker="MBR-SAMPLE-0042",
        agent_id="prior-auth-summarizer",
        profile_id="profile:utilization-review",
        initiated_by="principal:intake-nurse-03",
        approver="principal:medical-reviewer-11",
        sources=(
            {
                "source_id": "src-pa-request",
                "uri": "evidence://fabrikam-health/requests/pa-request-0042.json",
                "classification": "phi",
                "content": "member=MBR-SAMPLE-0042; service=MRI lumbar spine; therapy_weeks=4+2",
            },
            {
                "source_id": "src-imaging-criteria",
                "uri": "evidence://fabrikam-health/criteria/imaging-criteria-v12.pdf",
                "classification": "internal",
                "content": "Advanced imaging requires six weeks of documented conservative therapy.",
            },
        ),
        claims=(
            {"text": "The requested service matches the imaging criteria.", "source_ids": ["src-pa-request", "src-imaging-criteria"]},
            {"text": "Conservative therapy duration meets the criteria threshold.", "source_ids": ["src-pa-request", "src-imaging-criteria"]},
        ),
        lines=("4", "2"),
        total="6",
        fields=(("service_date", "2026-11-03"), ("requested_service", "MRI lumbar spine")),
        checks=(
            CheckSpec("eligibility.service_date", "required_field", "service_date"),
            CheckSpec("grounding.criteria_cited", "grounding"),
            CheckSpec("phi.minimum_necessary", "no_marker"),
        ),
        blocked_action="write_claims_system",
    ),
    "quality-deviation": DomainPack(
        key="quality-deviation",
        industry="Manufacturing",
        organization="Northwind Manufacturing",
        title="Quality deviation report",
        marker="SUPPLIER-SAMPLE-0042",
        agent_id="deviation-reporter",
        profile_id="profile:quality-management",
        initiated_by="principal:quality-engineer-05",
        approver="principal:quality-manager-02",
        sources=(
            {
                "source_id": "src-inspection-results",
                "uri": "evidence://northwind-mfg/inspection/lot-7731-results.csv",
                "classification": "confidential",
                "content": "shift,nonconforming\nA,3\nB,5\nC,1\nsupplier=SUPPLIER-SAMPLE-0042",
            },
            {
                "source_id": "src-part-spec",
                "uri": "evidence://northwind-mfg/specs/part-4410-rev-c.pdf",
                "classification": "internal",
                "content": "Bore diameter upper specification limit applies to all lots of part 4410.",
            },
        ),
        claims=(
            {"text": "Bore diameter exceeded the upper specification limit on lot 7731.", "source_ids": ["src-inspection-results", "src-part-spec"]},
            {"text": "Nonconforming units were found on all three shifts.", "source_ids": ["src-inspection-results"]},
        ),
        lines=("3", "5", "1"),
        total="9",
        fields=(("capa_owner", "principal:quality-engineer-05"), ("lot_id", "LOT-7731")),
        checks=(
            CheckSpec("spec.limits_recomputed", "recompute"),
            CheckSpec("traceability.lot_genealogy", "grounding"),
            CheckSpec("capa.owner_assigned", "required_field", "capa_owner"),
        ),
        blocked_action="release_quarantined_lot",
    ),
    "case-decision": DomainPack(
        key="case-decision",
        industry="Public sector",
        organization="Northwind County",
        title="Benefit case decision notice",
        marker="CASE-SAMPLE-0042",
        agent_id="case-notice-drafter",
        profile_id="profile:benefits-adjudication",
        initiated_by="principal:caseworker-14",
        approver="principal:supervisor-06",
        sources=(
            {
                "source_id": "src-application",
                "uri": "evidence://northwind-county/cases/application-0042.json",
                "classification": "confidential",
                "content": "case=CASE-SAMPLE-0042; wages=1820.00; other_income=640.00",
            },
            {
                "source_id": "src-benefit-code",
                "uri": "evidence://northwind-county/statute/benefit-code-2026.html",
                "classification": "public",
                "content": "Eligibility uses gross monthly household income; appeals are allowed within the statutory window.",
            },
        ),
        claims=(
            {"text": "Household income is below the program threshold.", "source_ids": ["src-application", "src-benefit-code"]},
            {"text": "The applicant may appeal within the statutory window.", "source_ids": ["src-benefit-code"]},
        ),
        lines=("1820.00", "640.00"),
        total="2460.00",
        fields=(("appeal_deadline", "2026-11-20"), ("program", "Energy assistance")),
        checks=(
            CheckSpec("eligibility.recompute", "recompute"),
            CheckSpec("statute.references_valid", "grounding"),
            CheckSpec("notice.appeal_deadline", "required_field", "appeal_deadline"),
        ),
        blocked_action="send_notice_to_applicant",
    ),
}


# ---------------------------------------------------------------------------
# Artifact, verifier, approval (shapes follow verify-before-approve)
# ---------------------------------------------------------------------------


def _check_spec(pack: DomainPack, code: str) -> CheckSpec:
    for spec in pack.checks:
        if spec.code == code:
            return spec
    raise ValueError(f"{code!r} is not a check in domain {pack.key!r}; choose from {[c.code for c in pack.checks]}")


def build_artifact(pack: DomainPack) -> dict[str, Any]:
    return {
        "schema": ARTIFACT_SCHEMA,
        "domain": pack.key,
        "title": pack.title,
        "claims": [{"text": c["text"], "source_ids": list(c["source_ids"])} for c in pack.claims],
        "figures": {"lines": list(pack.lines), "total": pack.total},
        "fields": dict(pack.fields),
    }


def corrupt_artifact(artifact: Mapping[str, Any], pack: DomainPack, code: str) -> dict[str, Any]:
    """Introduce the defect that the named check exists to catch."""
    spec = _check_spec(pack, code)
    broken = copy.deepcopy(dict(artifact))
    if spec.kind == "recompute":
        broken["figures"]["total"] = str(Decimal(broken["figures"]["total"]) + Decimal("1.00"))
    elif spec.kind == "grounding":
        broken["claims"].append({"text": "An unsupported statement added while drafting.", "source_ids": ["src-unretrieved"]})
    elif spec.kind == "required_field":
        broken["fields"][spec.field_name] = ""
    elif spec.kind == "no_marker":
        broken["claims"][0]["text"] += f" (reference {pack.marker})"
    return broken


def run_check(spec: CheckSpec, artifact: Mapping[str, Any], retrieved: set[str], marker: str) -> tuple[bool, str]:
    if spec.kind == "recompute":
        try:
            recomputed = sum((Decimal(line) for line in artifact["figures"]["lines"]), Decimal("0"))
            reported = Decimal(artifact["figures"]["total"])
        except (InvalidOperation, KeyError, TypeError) as exc:
            return False, f"figures are not recomputable: {exc}"
        if recomputed == reported:
            return True, "sum of lines equals the reported total"
        return False, f"recomputed {recomputed} but the artifact reports {reported}"
    if spec.kind == "grounding":
        for index, claim in enumerate(artifact.get("claims", [])):
            cited = set(claim.get("source_ids") or [])
            if not cited:
                return False, f"claim {index} cites no source"
            missing = sorted(cited - retrieved)
            if missing:
                return False, f"claim {index} cites sources that were never retrieved: {missing}"
        return True, "every claim cites at least one retrieved source"
    if spec.kind == "required_field":
        value = artifact.get("fields", {}).get(spec.field_name)
        if isinstance(value, str) and value.strip():
            return True, f"{spec.field_name} is present"
        return False, f"{spec.field_name} is missing or empty"
    if spec.kind == "no_marker":
        if marker in canonical(artifact).decode("utf-8"):
            return False, "a restricted identifier appears in the artifact"
        return True, "no restricted identifier in the artifact"
    raise ValueError(f"unknown check kind {spec.kind!r}")


def verify_artifact(
    pack: DomainPack,
    artifact: Mapping[str, Any],
    artifact_digest: str,
    retrieved: set[str],
    issued_at: str,
    skip_check: str | None = None,
) -> dict[str, Any]:
    checks = []
    for spec in pack.checks:
        if spec.code == skip_check:
            continue
        passed, detail = run_check(spec, artifact, retrieved, pack.marker)
        checks.append(
            {"code": spec.code, "gating": True, "status": "passed" if passed else "failed", "passed": passed, "detail": detail}
        )
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "artifact_sha256": artifact_digest,
        "verifier_version": VERIFIER_VERSION,
        "checks": checks,
        "verdict": "verified" if all(c["passed"] for c in checks) else "failed",
        "issued_at": issued_at,
    }
    receipt["receipt_id"] = "vr-" + _hex16(digest_obj(receipt))
    return receipt


def make_approval(
    *, run_id: str, artifact_digest: str, receipt: Mapping[str, Any], approver: str, decision: str, decided_at: str
) -> dict[str, Any]:
    approval = {
        "schema": APPROVAL_SCHEMA,
        "request_id": "apr-" + _hex16(artifact_digest),
        "nonce": _hex16(digest_obj({"run_id": run_id, "purpose": "approval-nonce"})),
        "artifact_sha256": artifact_digest,
        "receipt_id": receipt["receipt_id"],
        "approver": approver,
        "decision": decision,
        "decided_at": decided_at,
    }
    approval["decision_id"] = "dec-" + _hex16(digest_obj(approval))
    return approval


# ---------------------------------------------------------------------------
# Run certificate
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunContext:
    run_id: str
    trace_id: str
    agent_id: str
    agent_version: str
    initiated_by: str
    model_deployment: str
    model_version: str
    profile_id: str
    profile_version: str
    policy_digest: str
    required_checks: tuple[str, ...]


def make_context(pack: DomainPack) -> RunContext:
    run_id = f"run-{pack.key}-0001"
    profile = {
        "profile_id": pack.profile_id,
        "profile_version": PROFILE_VERSION,
        "allowed_actions": ["search_sources"],
        "denied_actions": [pack.blocked_action],
    }
    return RunContext(
        run_id=run_id,
        trace_id=hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:32],
        agent_id=pack.agent_id,
        agent_version=AGENT_VERSION,
        initiated_by=pack.initiated_by,
        model_deployment=MODEL_DEPLOYMENT,
        model_version=MODEL_VERSION,
        profile_id=pack.profile_id,
        profile_version=PROFILE_VERSION,
        policy_digest=digest_obj(profile),
        required_checks=tuple(spec.code for spec in pack.checks),
    )


def signing_input(cert: Mapping[str, Any]) -> bytes:
    """Canonical certificate with the signature value removed. alg and key_id stay bound."""
    body = copy.deepcopy(dict(cert))
    signature = body.get("signature") or {}
    body["signature"] = {"alg": signature.get("alg"), "key_id": signature.get("key_id")}
    return canonical(body)


def _of_type(entries: Sequence[Mapping[str, Any]], entry_type: str) -> list[Mapping[str, Any]]:
    return [entry for entry in entries if entry.get("type") == entry_type]


def _policy_reasons(ctx: RunContext, entries: Sequence[Mapping[str, Any]]) -> list[str]:
    reasons = []
    decisions = {entry.get("seq"): entry for entry in _of_type(entries, "policy_decision")}
    for request in _of_type(entries, "tool_call_request"):
        body = request.get("redacted") or {}
        decision = decisions.get(body.get("policy_decision_seq"))
        decision_body = (decision or {}).get("redacted") or {}
        if (
            decision is None
            or decision.get("seq", 0) >= request.get("seq", 0)
            or decision_body.get("outcome") != "allow"
            or decision_body.get("action") != body.get("action")
        ):
            reasons.append(f"policy.unauthorized_tool_call:{request.get('seq')}")
    versions = {
        (entry.get("redacted") or {}).get("profile_version")
        for entry in entries
        if entry.get("type") in ("run_started", "policy_decision")
    }
    if versions != {ctx.profile_version}:
        reasons.append("policy.profile_mismatch")
    return reasons


def issue_certificate(
    *,
    ctx: RunContext,
    entries: Sequence[Mapping[str, Any]],
    signer: Signer,
    issued_at: str,
    artifact_bytes: bytes | None,
    artifact_meta: Mapping[str, Any] | None,
    receipt: Mapping[str, Any] | None,
    approval: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Fail closed: any gap produces a signed certificate with status 'withheld'."""
    reasons: list[str] = []
    if verify_chain(entries, ctx.run_id):
        reasons.append("ledger.chain_invalid")
    closed = entries[-1] if entries and entries[-1].get("type") == "run_closed" else None
    if closed is None:
        reasons.append("ledger.not_closed")
    elif (closed.get("redacted") or {}).get("status") != "completed":
        reasons.append("run.not_completed")

    subject = None
    artifact_digest = None
    if artifact_bytes is None or artifact_meta is None:
        reasons.append("artifact.missing")
    else:
        artifact_digest = digest_bytes(artifact_bytes)
        subject = {
            "artifact_digest": artifact_digest,
            "media_type": artifact_meta.get("media_type"),
            "artifact_ref": artifact_meta.get("artifact_ref"),
        }
        produced = _of_type(entries, "artifact_produced")
        if not produced or produced[-1].get("payload_digest") != digest_obj(subject):
            reasons.append("artifact.digest_mismatch")

    required = list(ctx.required_checks)
    verification: dict[str, Any] = {
        "receipt_digest": None,
        "receipt_id": None,
        "verifier_version": None,
        "required_checks": required,
        "all_required_passed": False,
        "checks": [],
    }
    if not required:
        reasons.append("verification.no_required_checks")
    if receipt is None:
        reasons.append("verification.missing")
    else:
        receipt_digest = digest_obj(receipt)
        checks = [
            {"code": c.get("code"), "passed": c.get("passed") is True and c.get("status") == "passed"}
            for c in receipt.get("checks", [])
        ]
        verification.update(
            receipt_digest=receipt_digest,
            receipt_id=receipt.get("receipt_id"),
            verifier_version=receipt.get("verifier_version"),
            checks=checks,
        )
        if not any(e.get("payload_digest") == receipt_digest for e in _of_type(entries, "verifier_result")):
            reasons.append("verification.report_not_in_ledger")
        if artifact_digest is None or receipt.get("artifact_sha256") != artifact_digest:
            reasons.append("verification.stale_artifact")
        outcome: dict[str, bool] = {}
        for check in checks:
            outcome[check["code"]] = outcome.get(check["code"], True) and check["passed"]
        for code in required:
            if code not in outcome:
                reasons.append(f"verification.required_check_missing:{code}")
            elif not outcome[code]:
                reasons.append(f"verification.required_check_failed:{code}")
        verification["all_required_passed"] = bool(required) and all(outcome.get(code) is True for code in required)

    approval_block = None
    if approval is None:
        reasons.append("approval.missing")
    else:
        decision_digest = digest_obj(approval)
        approval_block = {
            "decision_digest": decision_digest,
            "decision_id": approval.get("decision_id"),
            "approver": approval.get("approver"),
            "decision": approval.get("decision"),
            "decided_at": approval.get("decided_at"),
        }
        if not any(e.get("payload_digest") == decision_digest for e in _of_type(entries, "approval_decision")):
            reasons.append("approval.not_in_ledger")
        if approval.get("decision") != "approve":
            reasons.append("approval.rejected")
        if artifact_digest is None or approval.get("artifact_sha256") != artifact_digest:
            reasons.append("approval.artifact_mismatch")
        if receipt is not None and approval.get("receipt_id") != receipt.get("receipt_id"):
            reasons.append("approval.receipt_mismatch")
        if approval.get("approver") in {ctx.agent_id, ctx.initiated_by}:
            reasons.append("approval.self_approval")

    reasons.extend(_policy_reasons(ctx, entries))
    head_hash, count = (entries[-1].get("entry_hash"), len(entries)) if entries else (GENESIS_HASH, 0)
    cert: dict[str, Any] = {
        "schema": CERTIFICATE_SCHEMA,
        "status": "withheld" if reasons else "issued",
        "run_id": ctx.run_id,
        "trace_id": ctx.trace_id,
        "subject": subject,
        "ledger": {"head_hash": head_hash, "entry_count": count},
        "verification": verification,
        "approval": approval_block,
        "policy": {"profile_id": ctx.profile_id, "profile_version": ctx.profile_version, "policy_digest": ctx.policy_digest},
        "execution": {
            "agent_id": ctx.agent_id,
            "agent_version": ctx.agent_version,
            "model_deployment": ctx.model_deployment,
            "model_version": ctx.model_version,
        },
        "timestamps": {
            "run_started_at": entries[0].get("at") if entries else None,
            "run_closed_at": closed.get("at") if closed else None,
            "issued_at": issued_at,
        },
        "withheld_reasons": list(dict.fromkeys(reasons)),
    }
    cert["certificate_id"] = "cert-" + _hex16(digest_obj(cert))
    cert["signature"] = {"alg": signer.alg, "key_id": signer.key_id, "value": ""}
    cert["signature"]["value"] = signer.sign(signing_input(cert))
    return cert


def verify_offline(
    cert: Mapping[str, Any],
    entries: Sequence[Mapping[str, Any]],
    trusted: Mapping[str, Signer],
    *,
    artifact_bytes: bytes | None = None,
    receipt: Mapping[str, Any] | None = None,
    approval: Mapping[str, Any] | None = None,
) -> list[Finding]:
    """Independent check of a certificate against a ledger copy and optional documents."""
    findings: list[Finding] = []
    if cert.get("schema") != CERTIFICATE_SCHEMA:
        findings.append(Finding("certificate.schema", None, f"unexpected schema {cert.get('schema')!r}"))
    signature = cert.get("signature") or {}
    signer = trusted.get(signature.get("key_id"))
    if signer is None:
        findings.append(Finding("certificate.untrusted_key", None, f"key {signature.get('key_id')!r} is not trusted"))
    elif signature.get("alg") != signer.alg:
        findings.append(Finding("certificate.alg_mismatch", None, f"{signature.get('alg')!r} is not {signer.alg!r}"))
    elif not signer.verify(signing_input(cert), signature.get("value") or ""):
        findings.append(Finding("certificate.signature_invalid", None, "signature does not match the certificate"))

    findings.extend(verify_chain(entries, cert.get("run_id")))
    bound = cert.get("ledger") or {}
    head_hash, count = (entries[-1].get("entry_hash"), len(entries)) if entries else (GENESIS_HASH, 0)
    if head_hash != bound.get("head_hash"):
        findings.append(Finding("certificate.head_mismatch", None, "ledger head differs from the certified head"))
    if count != bound.get("entry_count"):
        findings.append(Finding("certificate.entry_count_mismatch", None, f"ledger has {count} entries"))

    def ledgered(entry_type: str) -> set[Any]:
        return {e.get("payload_digest") for e in _of_type(entries, entry_type)}

    subject = cert.get("subject")
    if subject is not None:
        if digest_obj(subject) not in ledgered("artifact_produced"):
            findings.append(Finding("certificate.artifact_not_in_ledger", None, "subject was never ledgered"))
        if artifact_bytes is not None and digest_bytes(artifact_bytes) != subject.get("artifact_digest"):
            findings.append(Finding("certificate.artifact_mismatch", None, "artifact bytes do not match the subject"))
    elif artifact_bytes is not None:
        findings.append(Finding("certificate.artifact_mismatch", None, "certificate has no subject"))

    receipt_digest = (cert.get("verification") or {}).get("receipt_digest")
    if receipt_digest is not None and receipt_digest not in ledgered("verifier_result"):
        findings.append(Finding("certificate.receipt_not_in_ledger", None, "receipt digest was never ledgered"))
    if receipt is not None and digest_obj(receipt) != receipt_digest:
        findings.append(Finding("certificate.receipt_mismatch", None, "receipt does not match the certified digest"))

    decision_digest = (cert.get("approval") or {}).get("decision_digest")
    if decision_digest is not None and decision_digest not in ledgered("approval_decision"):
        findings.append(Finding("certificate.approval_not_in_ledger", None, "decision digest was never ledgered"))
    if approval is not None and digest_obj(approval) != decision_digest:
        findings.append(Finding("certificate.approval_mismatch", None, "decision does not match the certified digest"))
    return findings


def is_done(cert: Mapping[str, Any], findings: Sequence[Finding]) -> bool:
    """The only definition of done: an issued, verifiable certificate."""
    return cert.get("status") == "issued" and not cert.get("withheld_reasons") and not findings


# ---------------------------------------------------------------------------
# Scenario runner
# ---------------------------------------------------------------------------


@dataclass
class RunResult:
    pack: DomainPack
    ctx: RunContext
    store: InMemoryLedgerStore
    archive: InMemoryEvidenceArchive
    signer: InMemoryHmacSigner
    entries: list[dict[str, Any]]
    artifact_bytes: bytes
    artifact_meta: dict[str, Any]
    receipt: dict[str, Any]
    approval: dict[str, Any] | None
    certificate: dict[str, Any]
    archive_refs: dict[str, str]
    model_text: str

    def trusted(self) -> dict[str, Signer]:
        return {self.signer.key_id: self.signer}

    def offline_findings(self) -> list[Finding]:
        return verify_offline(
            self.certificate,
            self.entries,
            self.trusted(),
            artifact_bytes=self.artifact_bytes,
            receipt=self.receipt,
            approval=self.approval,
        )


APPROVAL_MODES = ("approve", "none", "reject", "other_artifact", "self")


def run_scenario(
    pack: DomainPack,
    *,
    fail_check: str | None = None,
    skip_check: str | None = None,
    approval_mode: str = "approve",
    unauthorized_tool_call: bool = False,
) -> RunResult:
    if approval_mode not in APPROVAL_MODES:
        raise ValueError(f"approval_mode must be one of {APPROVAL_MODES}")
    for code in (fail_check, skip_check):
        if code is not None:
            _check_spec(pack, code)
    ctx = make_context(pack)
    clock = SampleClock()
    store = InMemoryLedgerStore()
    archive = InMemoryEvidenceArchive()
    signer = InMemoryHmacSigner()
    ledger = EvidenceLedger(store, ctx.run_id, ctx.trace_id, clock)
    agent_actor = f"agent://{ctx.agent_id}"

    ledger.append(
        "run_started",
        HARNESS_ACTOR,
        {
            "profile_id": ctx.profile_id,
            "profile_version": ctx.profile_version,
            "agent_id": ctx.agent_id,
            "agent_version": ctx.agent_version,
            "initiated_by": ctx.initiated_by,
            "task": f"Draft the {pack.title.lower()} for {pack.organization}.",
        },
    )
    allow = ledger.append(
        "policy_decision",
        POLICY_ACTOR,
        {"action": "search_sources", "outcome": "allow", "rule_id": "allow-read-only-retrieval", "profile_version": ctx.profile_version},
    )
    ledger.append(
        "tool_call_request",
        agent_actor,
        {"action": "search_sources", "policy_decision_seq": allow["seq"], "args": {"query": f"{pack.title} {pack.marker}"}},
    )
    ledger.append(
        "tool_call_response",
        "tool://search_sources",
        {"action": "search_sources", "status": "ok", "result": [source["content"] for source in pack.sources]},
    )
    retrieved: set[str] = set()
    for source in pack.sources:
        ledger.append(
            "source_retrieved",
            "tool://search_sources",
            {
                "source_id": source["source_id"],
                "uri": source["uri"],
                "content_digest": digest_bytes(source["content"].encode("utf-8")),
                "classification": source["classification"],
            },
            payload_ref=source["uri"],
        )
        retrieved.add(source["source_id"])
    if unauthorized_tool_call:
        # Models a defect where an enforcement point was bypassed. The ledger
        # still records it, and certification catches it after the fact.
        deny = ledger.append(
            "policy_decision",
            POLICY_ACTOR,
            {"action": pack.blocked_action, "outcome": "deny", "rule_id": "deny-irreversible-side-effect", "profile_version": ctx.profile_version},
        )
        ledger.append(
            "tool_call_request",
            agent_actor,
            {"action": pack.blocked_action, "policy_decision_seq": deny["seq"], "args": {"reference": pack.marker}},
        )
        ledger.append(
            "tool_call_response",
            f"tool://{pack.blocked_action}",
            {"action": pack.blocked_action, "status": "executed", "result": f"side effect for {pack.marker}"},
        )
    model_text = f"Draft {pack.title.lower()} for reference {pack.marker}. All checks look fine to me."
    ledger.append("model_output", f"model://{ctx.model_deployment}", {"model_deployment": ctx.model_deployment, "text": model_text})

    artifact = build_artifact(pack)
    if fail_check is not None:
        artifact = corrupt_artifact(artifact, pack, fail_check)
    artifact_bytes = canonical(artifact)
    artifact_digest = digest_bytes(artifact_bytes)
    artifact_ref = f"evidence://artifacts/{ctx.run_id}/artifact.json"
    artifact_meta = {"artifact_digest": artifact_digest, "media_type": "application/json", "artifact_ref": artifact_ref}
    ledger.append("artifact_produced", HARNESS_ACTOR, artifact_meta, payload_ref=artifact_ref)

    receipt = verify_artifact(pack, artifact, artifact_digest, retrieved, clock.now(), skip_check)
    ledger.append("verifier_result", VERIFIER_ACTOR, receipt)

    approval = None
    if approval_mode != "none" and receipt["verdict"] == "verified":
        approver = ctx.initiated_by if approval_mode == "self" else pack.approver
        decision = "reject" if approval_mode == "reject" else "approve"
        approved_digest = digest_obj({"revision": "earlier-draft"}) if approval_mode == "other_artifact" else artifact_digest
        approval = make_approval(
            run_id=ctx.run_id,
            artifact_digest=approved_digest,
            receipt=receipt,
            approver=approver,
            decision=decision,
            decided_at=clock.now(),
        )
        ledger.append("approval_decision", APPROVAL_ACTOR, approval)

    # "completed" means execution finished. Only the certificate says the work is done.
    ledger.append("run_closed", HARNESS_ACTOR, {"status": "completed"})
    entries = store.read(ctx.run_id)
    cert = issue_certificate(
        ctx=ctx,
        entries=entries,
        signer=signer,
        issued_at=clock.now(),
        artifact_bytes=artifact_bytes,
        artifact_meta=artifact_meta,
        receipt=receipt,
        approval=approval,
    )
    store.seal(ctx.run_id)
    refs = {
        "artifact": archive.put(f"{ctx.run_id}/artifact.json", artifact_bytes),
        "ledger": archive.put(f"{ctx.run_id}/ledger.json", canonical(strip_bodies(entries))),
        "certificate": archive.put(f"{ctx.run_id}/certificate.json", canonical(cert)),
    }
    return RunResult(
        pack=pack,
        ctx=ctx,
        store=store,
        archive=archive,
        signer=signer,
        entries=entries,
        artifact_bytes=artifact_bytes,
        artifact_meta=artifact_meta,
        receipt=receipt,
        approval=approval,
        certificate=cert,
        archive_refs=refs,
        model_text=model_text,
    )


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, Callable[[], None]]] = []


def scenario(name: str) -> Callable[[Callable[[], None]], Callable[[], None]]:
    def register(fn: Callable[[], None]) -> Callable[[], None]:
        SCENARIOS.append((name, fn))
        return fn

    return register


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def codes(findings: Sequence[Finding]) -> set[str]:
    return {finding.code for finding in findings}


def rechain(entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """What a tamperer with write access would do: recompute every hash."""
    out = copy.deepcopy([dict(entry) for entry in entries])
    prev = GENESIS_HASH
    for entry in out:
        if entry.get("redacted") is not None:
            entry["redacted_digest"] = digest_obj(entry["redacted"])
        entry["prev_hash"] = prev
        entry["entry_hash"] = digest_obj(entry_header(entry))
        prev = entry["entry_hash"]
    return out


def _seq_of(entries: Sequence[Mapping[str, Any]], entry_type: str) -> int:
    return next(entry["seq"] for entry in entries if entry["type"] == entry_type)


def _reissue(r: RunResult, **overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "ctx": r.ctx,
        "entries": r.entries,
        "signer": r.signer,
        "issued_at": "2026-10-21T10:00:00Z",
        "artifact_bytes": r.artifact_bytes,
        "artifact_meta": r.artifact_meta,
        "receipt": r.receipt,
        "approval": r.approval,
    }
    kwargs.update(overrides)
    return issue_certificate(**kwargs)


@scenario("build-chain-and-verify")
def _test_build_chain_and_verify() -> None:
    for pack in DOMAIN_PACKS.values():
        r = run_scenario(pack)
        expect(verify_chain(r.entries, r.ctx.run_id) == [], f"{pack.key}: chain should verify")
        expect(r.certificate["status"] == "issued", f"{pack.key}: {r.certificate['withheld_reasons']}")
        expect(r.certificate["verification"]["all_required_passed"], f"{pack.key}: required checks should pass")
        findings = r.offline_findings()
        expect(findings == [], f"{pack.key}: {[str(f) for f in findings]}")
        expect(is_done(r.certificate, findings), f"{pack.key}: run should be done")
        archived = json.loads(r.archive.get(r.archive_refs["ledger"]))
        expect(all(entry["redacted"] is None for entry in archived), "archived ledger keeps headers only")
        expect(verify_offline(r.certificate, archived, r.trusted()) == [], f"{pack.key}: archived ledger verifies")


@scenario("detect-tampered-entry")
def _test_detect_tampered_entry() -> None:
    r = run_scenario(DOMAIN_PACKS["financial-report"])
    tampered = copy.deepcopy(r.entries)
    source_seq = _seq_of(tampered, "source_retrieved")
    tampered[source_seq - 1]["redacted"]["uri"] = "evidence://elsewhere/unvetted.csv"
    verdict_seq = _seq_of(tampered, "verifier_result")
    tampered[verdict_seq - 1]["actor"] = "verifier://someone-else"
    found = verify_chain(tampered, r.ctx.run_id)
    expect(Finding("ledger.redacted_digest", source_seq, "body does not match redacted_digest") in found, "body edit")
    expect(any(f.code == "ledger.entry_hash" and f.seq == verdict_seq for f in found), "header edit")


@scenario("detect-tampered-entry-with-rehash")
def _test_detect_tampered_entry_with_rehash() -> None:
    r = run_scenario(DOMAIN_PACKS["prior-auth"])
    tampered = copy.deepcopy(r.entries)
    target = tampered[_seq_of(tampered, "source_retrieved") - 1]
    target["redacted"]["classification"] = "public"
    target["redacted_digest"] = digest_obj(target["redacted"])
    target["entry_hash"] = digest_obj(entry_header(target))
    found = verify_chain(tampered, r.ctx.run_id)
    expect(any(f.code == "ledger.prev_hash" and f.seq == target["seq"] + 1 for f in found), str(found))


@scenario("detect-full-chain-rewrite-via-certificate")
def _test_detect_full_chain_rewrite() -> None:
    r = run_scenario(DOMAIN_PACKS["quality-deviation"])
    tampered = copy.deepcopy(r.entries)
    tampered[_seq_of(tampered, "source_retrieved") - 1]["redacted"]["uri"] = "evidence://elsewhere/other-lot.csv"
    rewritten = rechain(tampered)
    expect(verify_chain(rewritten, r.ctx.run_id) == [], "a full rewrite is internally consistent")
    expect("certificate.head_mismatch" in codes(verify_offline(r.certificate, rewritten, r.trusted())), "head pin")


@scenario("detect-reordered-entry")
def _test_detect_reordered_entry() -> None:
    r = run_scenario(DOMAIN_PACKS["case-decision"])
    reordered = copy.deepcopy(r.entries)
    reordered[2], reordered[3] = reordered[3], reordered[2]
    found = codes(verify_chain(reordered, r.ctx.run_id))
    expect({"ledger.sequence", "ledger.prev_hash"} <= found, str(found))


@scenario("detect-removed-entry")
def _test_detect_removed_entry() -> None:
    r = run_scenario(DOMAIN_PACKS["financial-report"])
    removed = [e for e in r.entries if e["seq"] != _seq_of(r.entries, "policy_decision")]
    found = codes(verify_chain(removed, r.ctx.run_id))
    expect({"ledger.sequence", "ledger.prev_hash"} <= found, str(found))
    # The head entry is untouched, so the certified entry count is what catches it.
    offline = codes(verify_offline(r.certificate, removed, r.trusted()))
    expect("certificate.entry_count_mismatch" in offline, str(offline))


@scenario("detect-truncated-tail-via-certificate")
def _test_detect_truncated_tail() -> None:
    r = run_scenario(DOMAIN_PACKS["prior-auth"])
    truncated = r.entries[:-2]
    expect(verify_chain(truncated, r.ctx.run_id) == [], "a truncated prefix still chains")
    offline = codes(verify_offline(r.certificate, truncated, r.trusted()))
    expect({"certificate.head_mismatch", "certificate.entry_count_mismatch"} <= offline, str(offline))


@scenario("withhold-when-required-check-fails")
def _test_withhold_when_check_fails() -> None:
    for pack in DOMAIN_PACKS.values():
        for spec in pack.checks:
            r = run_scenario(pack, fail_check=spec.code)
            reasons = r.certificate["withheld_reasons"]
            expect(r.certificate["status"] == "withheld", f"{pack.key}/{spec.code} should be withheld")
            expect(f"verification.required_check_failed:{spec.code}" in reasons, f"{spec.code}: {reasons}")
            other = [x for x in reasons if x.startswith("verification.required_check_failed:") and not x.endswith(spec.code)]
            expect(not other, f"{spec.code} corruption tripped other checks: {other}")
            expect("approval.missing" in reasons, "no approval is requested for a failed verification")
            expect("All checks look fine" in r.model_text, "the model still sounded confident")
            findings = r.offline_findings()
            expect(findings == [], f"a withheld certificate is still valid evidence: {findings}")
            expect(not is_done(r.certificate, findings), "withheld means not done")


@scenario("withhold-when-required-check-missing")
def _test_withhold_when_check_missing() -> None:
    pack = DOMAIN_PACKS["case-decision"]
    r = run_scenario(pack, skip_check="notice.appeal_deadline")
    expect(r.receipt["verdict"] == "verified", "the receipt's own verdict looks fine")
    expect(r.certificate["withheld_reasons"] == ["verification.required_check_missing:notice.appeal_deadline"],
           str(r.certificate["withheld_reasons"]))
    try:
        run_scenario(pack, fail_check="not.a.check")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown check codes must be rejected")


@scenario("withhold-when-approval-missing")
def _test_withhold_when_approval_missing() -> None:
    for pack in DOMAIN_PACKS.values():
        r = run_scenario(pack, approval_mode="none")
        expect(r.certificate["status"] == "withheld", pack.key)
        expect(r.certificate["withheld_reasons"] == ["approval.missing"], str(r.certificate["withheld_reasons"]))
        expect(r.certificate["approval"] is None, "no approval block")


@scenario("withhold-when-approval-is-invalid")
def _test_withhold_when_approval_invalid() -> None:
    pack = DOMAIN_PACKS["quality-deviation"]
    cases = {"reject": "approval.rejected", "other_artifact": "approval.artifact_mismatch", "self": "approval.self_approval"}
    for mode, reason in cases.items():
        reasons = run_scenario(pack, approval_mode=mode).certificate["withheld_reasons"]
        expect(reason in reasons, f"{mode}: {reasons}")
    r = run_scenario(pack)
    forged = dict(r.approval or {}, approver="principal:someone-else")
    forged_cert = _reissue(r, approval=forged)
    expect("approval.not_in_ledger" in forged_cert["withheld_reasons"], str(forged_cert["withheld_reasons"]))


@scenario("withhold-on-unauthorized-tool-call")
def _test_withhold_on_unauthorized_tool_call() -> None:
    for pack in DOMAIN_PACKS.values():
        r = run_scenario(pack, unauthorized_tool_call=True)
        blocked = [
            e["seq"] for e in r.entries
            if e["type"] == "tool_call_request" and e["redacted"]["action"] == pack.blocked_action
        ]
        expect(len(blocked) == 1, "one blocked call recorded")
        expect(r.receipt["verdict"] == "verified", "a clean artifact does not redeem a bad run")
        unauthorized = [x for x in r.certificate["withheld_reasons"] if x.startswith("policy.unauthorized_tool_call:")]
        expect(unauthorized == [f"policy.unauthorized_tool_call:{blocked[0]}"], str(unauthorized))


@scenario("verify-certificate-signature-offline")
def _test_verify_signature_offline() -> None:
    r = run_scenario(DOMAIN_PACKS["financial-report"])
    key_id = r.signer.key_id
    independent = {key_id: InMemoryHmacSigner()}
    expect(verify_offline(r.certificate, r.entries, independent, artifact_bytes=r.artifact_bytes) == [], "valid")
    expect("certificate.signature_invalid" in codes(
        verify_offline(r.certificate, r.entries, {key_id: InMemoryHmacSigner(key=b"a-different-key")})), "wrong key")
    expect("certificate.untrusted_key" in codes(verify_offline(r.certificate, r.entries, {})), "untrusted key")
    edited = copy.deepcopy(r.certificate)
    edited["execution"]["model_version"] = "<some-other-version>"
    expect("certificate.signature_invalid" in codes(verify_offline(edited, r.entries, independent)), "edited")
    downgraded = copy.deepcopy(r.certificate)
    downgraded["signature"]["alg"] = "none"
    expect("certificate.alg_mismatch" in codes(verify_offline(downgraded, r.entries, independent)), "alg")
    archived_cert = json.loads(r.archive.get(r.archive_refs["certificate"]))
    archived_ledger = json.loads(r.archive.get(r.archive_refs["ledger"]))
    archived_artifact = r.archive.get(r.archive_refs["artifact"])
    expect(verify_offline(archived_cert, archived_ledger, independent, artifact_bytes=archived_artifact) == [],
           "archived copies verify")


@scenario("detect-artifact-digest-mismatch")
def _test_detect_artifact_digest_mismatch() -> None:
    pack = DOMAIN_PACKS["case-decision"]
    r = run_scenario(pack)
    swapped = canonical(corrupt_artifact(build_artifact(pack), pack, "eligibility.recompute"))
    offline = codes(verify_offline(r.certificate, r.entries, r.trusted(), artifact_bytes=swapped))
    expect("certificate.artifact_mismatch" in offline, str(offline))
    reasons = _reissue(r, artifact_bytes=swapped)["withheld_reasons"]
    for reason in ("artifact.digest_mismatch", "verification.stale_artifact", "approval.artifact_mismatch"):
        expect(reason in reasons, f"{reason} missing from {reasons}")


@scenario("withheld-certificate-is-not-done")
def _test_withheld_is_not_done() -> None:
    r = run_scenario(DOMAIN_PACKS["prior-auth"], approval_mode="none")
    findings = r.offline_findings()
    expect(findings == [] and not is_done(r.certificate, findings), "valid evidence of an unfinished run")
    promoted = copy.deepcopy(r.certificate)
    promoted["status"] = "issued"
    promoted["withheld_reasons"] = []
    promoted_findings = verify_offline(promoted, r.entries, r.trusted())
    expect("certificate.signature_invalid" in codes(promoted_findings), "promotion breaks the signature")
    expect(not is_done(promoted, promoted_findings), "a promoted certificate is not done")


@scenario("raw-content-stays-out-of-ledger")
def _test_raw_content_stays_out() -> None:
    for pack in DOMAIN_PACKS.values():
        r = run_scenario(pack)
        text = canonical(r.entries).decode("utf-8")
        secrets = [pack.marker, r.model_text, pack.initiated_by] + [s["content"] for s in pack.sources]
        leaked = [value for value in secrets if value in text]
        expect(not leaked, f"{pack.key}: raw values in ledger: {leaked}")
    entry = build_entry(
        run_id="run-redaction",
        seq=1,
        entry_type="source_retrieved",
        at="2026-10-21T09:00:00Z",
        actor=HARNESS_ACTOR,
        trace_id="0" * 32,
        payload={"source_id": "s1", "uri": "evidence://x", "content": "raw-body-text", "content_digest": "sha256:x"},
        payload_ref=None,
        prev_hash=GENESIS_HASH,
    )
    expect("content" not in entry["redacted"] and b"raw-body-text" not in canonical(entry), "content dropped")
    smuggled = copy.deepcopy(run_scenario(DOMAIN_PACKS["financial-report"]).entries)
    smuggled[0]["redacted"]["initiated_by"] = "principal:analyst-01"
    expect("ledger.redaction_policy" in codes(verify_chain(rechain(smuggled))), "allowlist enforced on read")


@scenario("purge-redacted-bodies-keeps-chain-valid")
def _test_purge_bodies() -> None:
    r = run_scenario(DOMAIN_PACKS["quality-deviation"])
    stripped = strip_bodies(r.entries)
    expect(verify_chain(stripped, r.ctx.run_id) == [], "chain survives purge")
    findings = verify_offline(
        r.certificate, stripped, r.trusted(), artifact_bytes=r.artifact_bytes, receipt=r.receipt, approval=r.approval
    )
    expect(findings == [] and is_done(r.certificate, findings), "certificate survives purge")
    reissued = _reissue(r, entries=stripped)
    expect(reissued["status"] == "withheld" and "run.not_completed" in reissued["withheld_reasons"],
           "issue before purging; bodies are needed to certify")


@scenario("reject-append-on-stale-head-and-sealed")
def _test_reject_stale_and_sealed() -> None:
    store = InMemoryLedgerStore()
    ledger = EvidenceLedger(store, "run-concurrency", "1" * 32, SampleClock())
    first = ledger.append("run_started", HARNESS_ACTOR, {"profile_version": PROFILE_VERSION})
    racer = build_entry(
        run_id="run-concurrency", seq=1, entry_type="run_started", at="2026-10-21T09:00:00Z", actor=HARNESS_ACTOR,
        trace_id="1" * 32, payload={"profile_version": PROFILE_VERSION}, payload_ref=None, prev_hash=GENESIS_HASH,
    )
    for exc_type, action in (
        (StaleHeadError, lambda: store.append("run-concurrency", racer, expected_head=GENESIS_HASH)),
        (LedgerError, lambda: ledger.append("not_a_type", HARNESS_ACTOR, {})),
    ):
        try:
            action()
        except exc_type:
            pass
        else:
            raise AssertionError(f"expected {exc_type.__name__}")
    expect(store.head("run-concurrency") == (first["entry_hash"], 1), "head unchanged after rejected appends")
    r = run_scenario(DOMAIN_PACKS["case-decision"])
    expect(r.store.is_sealed(r.ctx.run_id), "run sealed after certification")
    for exc_type, action in (
        (LedgerSealedError, lambda: EvidenceLedger(r.store, r.ctx.run_id, r.ctx.trace_id, SampleClock()).append(
            "run_closed", HARNESS_ACTOR, {"status": "completed"})),
        (ImmutableWriteError, lambda: r.archive.put(f"{r.ctx.run_id}/certificate.json", b"{}")),
    ):
        try:
            action()
        except exc_type:
            pass
        else:
            raise AssertionError(f"expected {exc_type.__name__}")


@scenario("canonical-json-is-order-independent-and-rejects-floats")
def _test_canonical_json() -> None:
    expect(canonical({"b": 1, "a": {"y": 2, "x": 3}}) == b'{"a":{"x":3,"y":2},"b":1}', "sorted and compact")
    expect(digest_obj({"a": 1, "b": 2}) == digest_obj({"b": 2, "a": 1}), "order independent digest")
    expect(canonical({"name": "caf\u00e9"}) == '{"name":"caf\u00e9"}'.encode("utf-8"), "UTF-8, not escaped")
    for bad in ({"amount": 1.5}, {"values": [float("nan")]}):
        try:
            canonical(bad)
        except TypeError:
            pass
        else:
            raise AssertionError("floats must be rejected")


@scenario("azure-adapters-are-stubs")
def _test_azure_stubs() -> None:
    cosmos = CosmosDbLedgerStore("https://<cosmos-account>.documents.azure.com:443/", "evidence", "ledger")
    blob = BlobEvidenceArchive("https://<storage-account>.blob.core.windows.net", "sealed-evidence")
    hmac_signer = KeyVaultSecretHmacSigner("https://<vault-name>.vault.azure.net", "run-certificate-hmac")
    key_signer = KeyVaultKeySigner("https://<vault-name>.vault.azure.net/keys/run-certificate/<version>")
    expect(isinstance(cosmos, LedgerStore) and isinstance(InMemoryLedgerStore(), LedgerStore), "store seam")
    expect(isinstance(blob, EvidenceArchive) and isinstance(InMemoryEvidenceArchive(), EvidenceArchive), "archive")
    expect(all(isinstance(s, Signer) for s in (hmac_signer, key_signer, InMemoryHmacSigner())), "signer seam")
    calls = [
        lambda: cosmos.read("run-x"),
        lambda: cosmos.append("run-x", {}, GENESIS_HASH),
        lambda: blob.put("x", b""),
        lambda: hmac_signer.sign(b""),
        lambda: key_signer.sign(b""),
        lambda: key_signer.verify(b"", ""),
    ]
    for call in calls:
        try:
            call()
        except NotImplementedError:
            continue
        raise AssertionError("production adapters must raise NotImplementedError")


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
    passed = len(SCENARIOS) - failures
    print(f"{passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if failures == 0 else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_demo(r: RunResult, findings: Sequence[Finding]) -> None:
    cert = r.certificate
    print(f"Domain: {r.pack.industry} / {r.pack.organization} / {r.pack.title}")
    print(f"Run: {r.ctx.run_id}  trace: {r.ctx.trace_id}")
    print()
    print(f"{'seq':>3}  {'type':<20} {'actor':<38} {'payload':<12} {'entry_hash':<12}")
    for entry in r.entries:
        print(
            f"{entry['seq']:>3}  {entry['type']:<20} {entry['actor'][:38]:<38} "
            f"{short(entry['payload_digest']):<12} {short(entry['entry_hash']):<12}"
        )
    print()
    print(f"Certificate {cert['certificate_id']}: {cert['status'].upper()}")
    print(f"  subject artifact: {short((cert['subject'] or {}).get('artifact_digest'))}")
    print(f"  ledger head:      {short(cert['ledger']['head_hash'])} ({cert['ledger']['entry_count']} entries)")
    for check in cert["verification"]["checks"]:
        print(f"  check {check['code']}: {'passed' if check['passed'] else 'FAILED'}")
    approval = cert["approval"]
    print(f"  approval:         {approval['decision'] + ' by ' + approval['approver'] if approval else 'none'}")
    print(f"  signature:        {cert['signature']['alg']} {cert['signature']['key_id']}")
    for reason in cert["withheld_reasons"]:
        print(f"  withheld: {reason}")
    print()
    print("Offline verification: " + ("no findings" if not findings else f"{len(findings)} finding(s)"))
    for finding in findings:
        print(f"  {finding}")
    print("Result: " + ("DONE" if is_done(cert, findings) else "NOT DONE (no issued certificate)"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evidence ledger and run certificate sample (stdlib only).")
    parser.add_argument("--self-test", action="store_true", help="run every scenario and exit")
    parser.add_argument("--list-domains", action="store_true", help="list the Domain Packs and their checks")
    parser.add_argument("--domain", default="financial-report", choices=sorted(DOMAIN_PACKS))
    parser.add_argument("--fail-check", metavar="CODE", help="corrupt the artifact so this check fails")
    parser.add_argument("--skip-check", metavar="CODE", help="leave this required check out of the receipt")
    parser.add_argument("--no-approval", action="store_true", help="do not record an approval decision")
    parser.add_argument("--unauthorized-tool-call", action="store_true", help="record a call that policy denied")
    parser.add_argument("--json", action="store_true", help="print the certificate, ledger headers, and findings")
    args = parser.parse_args(argv)

    if args.self_test:
        return run_self_test()
    if args.list_domains:
        for pack in DOMAIN_PACKS.values():
            checks = ", ".join(spec.code for spec in pack.checks)
            print(f"{pack.key:<18} {pack.industry:<20} {pack.title}  [{checks}]")
        return 0
    pack = DOMAIN_PACKS[args.domain]
    try:
        r = run_scenario(
            pack,
            fail_check=args.fail_check,
            skip_check=args.skip_check,
            approval_mode="none" if args.no_approval else "approve",
            unauthorized_tool_call=args.unauthorized_tool_call,
        )
    except ValueError as exc:
        parser.error(str(exc))
    findings = r.offline_findings()
    if args.json:
        print(json.dumps(
            {
                "certificate": r.certificate,
                "ledger": strip_bodies(r.entries),
                "offline_findings": [str(f) for f in findings],
                "done": is_done(r.certificate, findings),
            },
            indent=2,
            sort_keys=True,
        ))
    else:
        print_demo(r, findings)
    return 0 if is_done(r.certificate, findings) else 2


if __name__ == "__main__":
    sys.exit(main())
