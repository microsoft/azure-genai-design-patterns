"""Execution isolation probe for agents that run generated code (Gate 5).

Run this script through the same path the agent uses to execute code: the code
tool, the broker and the session pool. It reports what the code can actually
see and reach, so that "this runs in a sandbox" is a measured fact rather than
a name.

Checks (read-only; nothing is sent anywhere):

    secret_env         Names of environment variables that look like secrets or
                       credentials. Values are never read into the report.
    identity_endpoint  Whether a managed identity endpoint is advertised.
    metadata_address   Whether a TCP connection to the instance metadata
                       address succeeds.
    public_internet    Whether a TCP connection to a public host succeeds.
    packages           Optional. Whether each listed package imports, compared
                       with what its installed metadata claims.

The hostname and Python version are reported for context only. A hostname that
says "sandbox" is not evidence of isolation.

Zero external dependencies: standard library only.

Usage:
    python execution_isolation_probe.py [--packages pandas,numpy,scikit-learn=sklearn]
                                        [--public-host example.com]
                                        [--timeout 2.0]
    python execution_isolation_probe.py --self-test

Exit code is 0 when every check passes, 1 when any check fails and 2 on a usage
error. The report is printed as JSON on standard output.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import json
import os
import platform
import socket
import sys
from typing import Any, Callable, Iterable, Mapping, Optional

SECRET_NAME_MARKERS = (
    "KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "CONNECTION_STRING",
    "CONNSTR",
    "SAS",
    "CREDENTIAL",
    "PRIVATE",
)
# Common variables whose names contain a marker but hold no secret.
BENIGN_ENV_NAMES = frozenset({"PYTHONHASHSEED", "SSH_AUTH_SOCK", "GPG_AGENT_INFO"})
IDENTITY_ENV_NAMES = (
    "IDENTITY_ENDPOINT",
    "IDENTITY_HEADER",
    "MSI_ENDPOINT",
    "MSI_SECRET",
    "IMDS_ENDPOINT",
    "AZURE_FEDERATED_TOKEN_FILE",
)
METADATA_ADDRESS = ("169.254.169.254", 80)
DEFAULT_PUBLIC_HOST = "example.com"
DEFAULT_TIMEOUT_SECONDS = 2.0

ConnectFn = Callable[[str, int, float], bool]
ImportFn = Callable[[str], Optional[str]]
MetadataFn = Callable[[str], Optional[str]]


def tcp_reachable(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection succeeds. Sends no application data."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def import_version(name: str) -> Optional[str]:
    """Import a module and return its reported version, "unknown" or None on failure."""
    try:
        module = importlib.import_module(name)
    except Exception:  # noqa: BLE001 - any import failure means "not usable"
        return None
    return str(getattr(module, "__version__", "unknown"))


def metadata_version(name: str) -> Optional[str]:
    """Return the version recorded in installed package metadata, or None."""
    if name in getattr(sys, "stdlib_module_names", ()):
        return "stdlib"
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def secret_like_names(environ: Mapping[str, str]) -> list[str]:
    names = []
    for name in environ:
        upper = name.upper()
        if upper in BENIGN_ENV_NAMES:
            continue
        if any(marker in upper for marker in SECRET_NAME_MARKERS):
            names.append(name)
    return sorted(names)


def collect(
    environ: Mapping[str, str],
    connect: ConnectFn,
    import_fn: ImportFn,
    metadata_fn: MetadataFn,
    packages: Iterable[str] = (),
    public_host: str = DEFAULT_PUBLIC_HOST,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    hostname: Optional[str] = None,
) -> dict[str, Any]:
    """Collect a snapshot. Only variable names are taken from the environment."""
    package_rows = []
    for spec in packages:
        # "distribution=module" when the two names differ, for example "scikit-learn=sklearn".
        dist, _, module = spec.partition("=")
        module = module or dist
        package_rows.append(
            {"name": spec, "metadata_version": metadata_fn(dist), "import_version": import_fn(module)}
        )
    return {
        "hostname": hostname if hostname is not None else socket.gethostname(),
        "python_version": platform.python_version(),
        "secret_env_names": secret_like_names(environ),
        "identity_env_names": sorted(n for n in IDENTITY_ENV_NAMES if n in environ),
        "metadata_address_reachable": connect(METADATA_ADDRESS[0], METADATA_ADDRESS[1], timeout),
        "public_host": public_host,
        "public_internet_reachable": connect(public_host, 443, timeout),
        "packages": package_rows,
    }


def evaluate(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Turn a snapshot into pass or fail checks and an overall verdict."""
    checks = [
        {
            "check": "secret_env",
            "passed": not snapshot["secret_env_names"],
            "detail": snapshot["secret_env_names"] or "none found",
        },
        {
            "check": "identity_endpoint",
            "passed": not snapshot["identity_env_names"],
            "detail": snapshot["identity_env_names"] or "not advertised",
        },
        {
            "check": "metadata_address",
            "passed": not snapshot["metadata_address_reachable"],
            "detail": "reachable" if snapshot["metadata_address_reachable"] else "not reachable",
        },
        {
            "check": "public_internet",
            "passed": not snapshot["public_internet_reachable"],
            "detail": ("reachable" if snapshot["public_internet_reachable"] else "blocked")
            + f" ({snapshot['public_host']}:443)",
        },
    ]
    mismatches = [
        row["name"]
        for row in snapshot["packages"]
        if (row["metadata_version"] is None) != (row["import_version"] is None)
    ]
    missing = [
        row["name"]
        for row in snapshot["packages"]
        if row["metadata_version"] is None and row["import_version"] is None
    ]
    if snapshot["packages"]:
        problems = []
        if mismatches:
            problems.append(f"metadata and import disagree for: {', '.join(mismatches)}")
        if missing:
            problems.append(f"not installed: {', '.join(missing)}")
        checks.append(
            {
                "check": "packages",
                "passed": not problems,
                "detail": "; ".join(problems) or "every listed package is installed and imports",
            }
        )
    failed = [c["check"] for c in checks if not c["passed"]]
    return {
        "verdict": "FAIL" if failed else "PASS",
        "failed_checks": failed,
        "checks": checks,
        "context": {
            "hostname": snapshot["hostname"],
            "python_version": snapshot["python_version"],
            "note": "The hostname is context only; it is not evidence of isolation.",
        },
    }


def _self_test() -> int:
    """Offline self-test with simulated in-process and sandboxed snapshots."""
    planted_value = "planted-value-must-not-appear"
    in_process_env = {
        "MODEL_API_KEY": planted_value,
        "STORAGE_CONNECTION_STRING": planted_value,
        "IDENTITY_ENDPOINT": "http://localhost.test/msi/token",
        "IDENTITY_HEADER": planted_value,
        "PATH": "/usr/bin",
        "PYTHONHASHSEED": "0",
    }
    sandbox_env = {"PATH": "/usr/bin", "HOME": "/home/session", "PYTHONHASHSEED": "0"}

    def open_network(host: str, port: int, timeout: float) -> bool:
        return True

    def closed_network(host: str, port: int, timeout: float) -> bool:
        return False

    # In the in-process case, one package has metadata but does not import.
    in_process_imports = {"examplepkg": "1.0", "ghostpkg": None}
    in_process_metadata = {"examplepkg": "1.0", "ghostpkg": "2.0"}
    sandbox_versions = {"examplepkg": "1.0", "ghostpkg": "2.0"}

    cases = [
        (
            "in-process 'sandbox'",
            collect(
                in_process_env,
                open_network,
                in_process_imports.get,
                in_process_metadata.get,
                packages=("examplepkg", "ghostpkg"),
                hostname="agent-sandbox-01",
            ),
            "FAIL",
            {"secret_env", "identity_endpoint", "metadata_address", "public_internet", "packages"},
        ),
        (
            "sandboxed session",
            collect(
                sandbox_env,
                closed_network,
                sandbox_versions.get,
                sandbox_versions.get,
                packages=("examplepkg", "ghostpkg"),
                hostname="session-host",
            ),
            "PASS",
            set(),
        ),
    ]

    failures = 0
    for label, snapshot, expected_verdict, expected_failed in cases:
        report = evaluate(snapshot)
        rendered = json.dumps(report)
        problems = []
        if report["verdict"] != expected_verdict:
            problems.append(f"verdict {report['verdict']}, expected {expected_verdict}")
        if set(report["failed_checks"]) != expected_failed:
            problems.append(f"failed checks {sorted(report['failed_checks'])}, expected {sorted(expected_failed)}")
        if planted_value in rendered:
            problems.append("an environment variable value appeared in the report")
        if "PYTHONHASHSEED" in rendered:
            problems.append("a benign variable was reported as secret-like")
        status = "ok" if not problems else "FAILED: " + "; ".join(problems)
        print(f"[self-test] {label}: {report['verdict']} {sorted(report['failed_checks'])} -> {status}")
        failures += bool(problems)

    print(f"[self-test] {len(cases) - failures}/{len(cases)} cases passed")
    return 0 if failures == 0 else 1


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Report what generated code can see and reach.")
    parser.add_argument("--self-test", action="store_true", help="run the offline self-test and exit")
    parser.add_argument(
        "--packages",
        default="",
        help="comma-separated packages to check; use distribution=module when the names differ",
    )
    parser.add_argument("--public-host", default=DEFAULT_PUBLIC_HOST, help="public host for the egress check")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS, help="connect timeout in seconds")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    packages = [p.strip() for p in args.packages.split(",") if p.strip()]
    snapshot = collect(
        os.environ,
        tcp_reachable,
        import_version,
        metadata_version,
        packages=packages,
        public_host=args.public_host,
        timeout=args.timeout,
    )
    report = evaluate(snapshot)
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
