"""Prove per-team budget isolation on a deployed gateway.

The test:

1. Sends requests as the "burst" team (default alpha) until the gateway answers 429.
2. Checks that the 429 came from the gateway's token limit (it carries ``retry-after``
   and the gateway's JSON shape), not from the model deployment running out of capacity.
3. Immediately sends one request as the "control" team (default beta) and expects it to
   succeed, because each team product has its own token counter.

Run it as an operator who is a member of both teams' projects, in a sandbox, with
small team budgets. It prints a qualitative verdict and exits:

* 0 - isolation shown: burst team throttled by the gateway, control team served
* 1 - inconclusive: the burst team was never throttled (raise --max-requests or lower its budget)
* 2 - isolation failed: the control team was also refused
* 3 - setup problem: settings, sign-in or project access

The SDK's automatic retries are disabled so the first 429 is visible.
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Optional

from openai import APIStatusError, RateLimitError

from team_client import (
    GatewayConnectionError,
    TeamAccessError,
    TeamGatewayClient,
    TeamSettings,
    gateway_headers,
)

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional
    load_dotenv = None

BURST_PROMPT = (
    "List twenty practical guidelines for writing clear incident postmortems. "
    "Give each guideline a short title and a two-sentence explanation."
)


@dataclass
class BurstOutcome:
    successes: int = 0
    throttled: bool = False
    throttle_source: str = ""
    retry_after: Optional[str] = None
    other_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


def classify_429(exc: RateLimitError) -> str:
    """Tell a gateway throttle apart from a model capacity throttle.

    The token limit policy answers with a top-level ``statusCode``/``message`` JSON body.
    A capacity 429 from the model deployment uses the Azure OpenAI ``error`` envelope.
    Confirm the shapes in your tenant; the gateway logs are the authoritative source.
    """
    try:
        body = json.loads(exc.response.text or "{}")
    except ValueError:
        body = {}
    if isinstance(body, dict) and "statusCode" in body and "error" not in body:
        return "gateway token limit"
    if isinstance(body, dict) and "error" in body:
        return "model deployment capacity"
    return "unknown"


def burst(client: TeamGatewayClient, max_requests: int, workers: int, max_tokens: int) -> BurstOutcome:
    outcome = BurstOutcome()
    stop = threading.Event()

    def one_call(index: int) -> None:
        if stop.is_set():
            return
        try:
            result = client.chat(BURST_PROMPT, max_tokens=max_tokens)
        except RateLimitError as exc:
            with outcome.lock:
                if not outcome.throttled:
                    outcome.throttled = True
                    outcome.throttle_source = classify_429(exc)
                    outcome.retry_after = gateway_headers(exc.response.headers).get("retry-after")
            stop.set()
            return
        except APIStatusError as exc:
            with outcome.lock:
                outcome.other_errors.append(f"request {index}: HTTP {exc.status_code}")
            stop.set()
            return
        with outcome.lock:
            outcome.successes += 1
        remaining = result.gateway_headers.get("x-remaining-tokens", "n/a")
        print(f"  {client.settings.team} request {index}: {result.status_code} (x-remaining-tokens {remaining})")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for index in range(1, max_requests + 1):
            if stop.is_set():
                break
            pool.submit(one_call, index)
    return outcome


def main(argv: list[str] | None = None) -> int:
    if load_dotenv is not None:
        load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--burst-team", default="alpha")
    parser.add_argument("--control-team", default="beta")
    parser.add_argument("--max-requests", type=int, default=100, help="Upper bound on burst requests.")
    parser.add_argument("--workers", type=int, default=4, help="Concurrent burst requests.")
    parser.add_argument("--max-tokens", type=int, default=400, help="Completion token cap per burst request.")
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    if args.burst_team == args.control_team:
        print("--burst-team and --control-team must differ", file=sys.stderr)
        return 3

    try:
        burst_client = TeamGatewayClient(TeamSettings.from_env(args.burst_team), max_retries=0)
        control_client = TeamGatewayClient(TeamSettings.from_env(args.control_team), max_retries=0)
        # Read both connections up front so access problems surface before the burst.
        burst_client.refresh()
        control_client.refresh()
    except (ValueError, TeamAccessError, GatewayConnectionError) as exc:
        print(f"setup problem: {exc}", file=sys.stderr)
        return 3

    print(f"Bursting team '{args.burst_team}' until the gateway throttles it...")
    outcome = burst(burst_client, args.max_requests, args.workers, args.max_tokens)

    if outcome.other_errors:
        print("Burst stopped on a non-429 error:", "; ".join(outcome.other_errors), file=sys.stderr)
        return 3
    if not outcome.throttled:
        print(
            f"INCONCLUSIVE: team '{args.burst_team}' was never throttled. Lower its tokensPerMinute "
            "in a sandbox or raise --max-requests."
        )
        return 1

    print(
        f"Team '{args.burst_team}' received 429 after {outcome.successes} successful call(s); "
        f"source: {outcome.throttle_source}; retry-after: {outcome.retry_after or 'not set'}."
    )
    if outcome.throttle_source == "model deployment capacity":
        print(
            "INCONCLUSIVE: the 429 came from the model deployment, not the team budget. The shared "
            "deployment capacity is smaller than this team's budget; see docs/evaluation-and-troubleshooting.md."
        )
        return 1

    print(f"Calling as team '{args.control_team}' while '{args.burst_team}' is throttled...")
    try:
        control = control_client.chat("Reply with the single word: ok", max_tokens=10)
    except RateLimitError as exc:
        print(
            f"FAIL: team '{args.control_team}' was also throttled ({classify_429(exc)}). Check that each "
            "team has its own product and that the token limit counter key is the product."
        )
        return 2
    except APIStatusError as exc:
        print(f"FAIL: team '{args.control_team}' call returned HTTP {exc.status_code}.")
        return 2

    print(f"Team '{args.control_team}' call succeeded with status {control.status_code}.")
    print(
        f"PASS: the gateway throttled '{args.burst_team}' on its own budget while "
        f"'{args.control_team}' kept being served from the same deployment."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
