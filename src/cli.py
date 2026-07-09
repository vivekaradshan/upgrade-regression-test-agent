"""CLI entry point for the upgrade regression testing agent.

    python -m src.cli run --manifest manifests/spark-3.5-to-4.0.yaml
    python -m src.cli status --run-id <run_id>
    python -m src.cli cleanup --run-id <run_id>

status/cleanup read workspace/state/<run_id>.json snapshots (the same
files the dashboard reads) rather than DynamoDB directly, since the CLI
runs in a separate process from whichever run started the orchestrator
and moto's mocked DynamoDB only lives in-memory within that process.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog

from src.config.settings import Settings
from src.orchestrator.graph import PROJECT_ROOT, run_upgrade_test
from src.tools.github_client import GitHubAPIError, GitHubClient

STATE_DIR = PROJECT_ROOT / "workspace" / "state"


def _load_snapshot(run_id: str) -> dict:
    snapshot_path = STATE_DIR / f"{run_id}.json"
    if not snapshot_path.exists():
        print(f"No snapshot found for run_id={run_id} at {snapshot_path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(snapshot_path.read_text())


def cmd_run(args: argparse.Namespace) -> None:
    final_state = run_upgrade_test(args.manifest)
    print(
        json.dumps(
            {
                "run_id": final_state["run_id"],
                "phase": final_state.get("phase"),
                "status": final_state.get("status"),
                "retry_count": final_state.get("retry_count"),
                "report_path": final_state.get("report_path"),
                "pr_url": final_state.get("pr_url"),
            },
            indent=2,
        )
    )


def cmd_status(args: argparse.Namespace) -> None:
    print(json.dumps(_load_snapshot(args.run_id), indent=2))


def cmd_cleanup(args: argparse.Namespace) -> None:
    snapshot = _load_snapshot(args.run_id)
    config = snapshot["metadata"]["config"]
    source_control = config["source_control"]

    settings = Settings()
    github_client = GitHubClient(
        token=settings.github_token,
        owner=source_control["owner"],
        repo=source_control["repo"],
    )

    prefix = source_control["target"]["branch_prefix"]
    branches = [f"{prefix}/{args.run_id}-baseline", f"{prefix}/{args.run_id}-target"]

    for branch in branches:
        try:
            github_client.delete_branch(branch)
            print(f"Deleted branch {branch}")
        except GitHubAPIError as e:
            print(f"Could not delete {branch}: {e}", file=sys.stderr)

    github_client.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upgrade-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an upgrade test")
    run_parser.add_argument("--manifest", required=True, help="Path to the test manifest YAML")
    run_parser.set_defaults(func=cmd_run)

    status_parser = subparsers.add_parser("status", help="Show a run's current status")
    status_parser.add_argument("--run-id", required=True)
    status_parser.set_defaults(func=cmd_status)

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete a run's GitHub branches")
    cleanup_parser.add_argument("--run-id", required=True)
    cleanup_parser.set_defaults(func=cmd_cleanup)

    return parser


def main() -> None:
    # structlog defaults to stdout, which would corrupt the JSON this CLI
    # prints there (e.g. `run`'s summary, `status`'s snapshot) - logs belong
    # on stderr so stdout stays machine-parseable.
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))

    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
