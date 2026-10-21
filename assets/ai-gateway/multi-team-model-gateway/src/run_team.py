"""Call the shared model through the gateway as one team.

Examples:

    python run_team.py --team alpha
    python run_team.py --team beta --prompt "Summarize our on-call policy in one line."
    python run_team.py --team alpha --list-connections

Settings come from the environment or a local ``.env`` file (see ``.env.sample``).
No key is read from the environment: the gateway key comes from the team's Foundry
project connection, which only that team's members can read.
"""

from __future__ import annotations

import argparse
import sys

from openai import APIStatusError, RateLimitError

from team_client import (
    GatewayConnectionError,
    TeamAccessError,
    TeamGatewayClient,
    TeamSettings,
    gateway_headers,
    list_project_connections,
)

try:
    from dotenv import load_dotenv
except ImportError:  # python-dotenv is optional
    load_dotenv = None


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--team", required=True, help="Team name, for example alpha or beta.")
    parser.add_argument(
        "--prompt",
        default="In one sentence, what does an AI gateway do?",
        help="Prompt to send to the shared deployment.",
    )
    parser.add_argument("--max-tokens", type=int, default=200, help="Completion token cap for the call.")
    parser.add_argument(
        "--list-connections",
        action="store_true",
        help="List the connections in the team's project (names and targets only, no credentials) and exit.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if load_dotenv is not None:
        load_dotenv()
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        settings = TeamSettings.from_env(args.team)
    except ValueError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    if args.list_connections:
        try:
            rows = list_project_connections(settings)
        except Exception as exc:  # surfaced as a readable message for the CLI
            print(f"could not list connections for team '{settings.team}': {exc}", file=sys.stderr)
            return 1
        for row in rows:
            print(f"{row['name']}\t{row['type']}\t{row['target']}")
        return 0

    client = TeamGatewayClient(settings)
    try:
        result = client.chat(args.prompt, max_tokens=args.max_tokens)
    except TeamAccessError as exc:
        print(f"access denied: {exc}", file=sys.stderr)
        return 3
    except GatewayConnectionError as exc:
        print(f"connection problem: {exc}", file=sys.stderr)
        return 1
    except RateLimitError as exc:
        headers = gateway_headers(exc.response.headers)
        print(
            f"team '{settings.team}' is over its token budget; retry after "
            f"{headers.get('retry-after', 'the interval in retry-after')} seconds.",
            file=sys.stderr,
        )
        return 4
    except APIStatusError as exc:
        print(f"gateway returned {exc.status_code}: {exc.message}", file=sys.stderr)
        return 1

    print(f"team:       {settings.team}")
    print(f"deployment: {settings.deployment}")
    print(f"status:     {result.status_code}")
    for name, value in result.gateway_headers.items():
        print(f"{name + ':':<20}{value}")
    print()
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
