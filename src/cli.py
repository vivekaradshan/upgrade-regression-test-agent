"""CLI entry point for the upgrade regression testing agent.

    python -m src.cli run --manifest manifests/spark-3.5-to-4.0.yaml
    python -m src.cli run --manifest manifests/spark-3.5-to-4.0.yaml --target aws
    python -m src.cli status --run-id <run_id> [--target aws]
    python -m src.cli cleanup --run-id <run_id> [--target aws]

--target local (default) runs the LangGraph orchestrator in this process;
status/cleanup then read workspace/state/<run_id>.json snapshots (the same
files the dashboard reads) rather than DynamoDB directly, since moto's
mocked DynamoDB only lives in-memory within the process that started it.

--target aws POSTs to the deployed API Gateway endpoint instead, which
starts a real Step Functions execution (see
src/aws_lambda/start_run_handler.py); status/cleanup then read the run's
state from real DynamoDB via StateStore, since an AWS-triggered run has
no local snapshot file to read.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import structlog

from src.config.manifest import ManifestLoader
from src.config.settings import Settings
from src.mock_infra.aws_clients import AWSClientFactory
from src.orchestrator.graph import PROJECT_ROOT, run_upgrade_test
from src.tools.github_client import GitHubAPIError, GitHubClient
from src.tools.signed_http import NoCredentialsError, SignedRequestError, signed_post
from src.tools.state_store import StateStore

STATE_DIR = PROJECT_ROOT / "workspace" / "state"


def _load_snapshot(run_id: str) -> dict:
    snapshot_path = STATE_DIR / f"{run_id}.json"
    if not snapshot_path.exists():
        print(f"No snapshot found for run_id={run_id} at {snapshot_path}", file=sys.stderr)
        sys.exit(1)
    return json.loads(snapshot_path.read_text())


def _get_state_store() -> StateStore:
    factory = AWSClientFactory(use_mocks=False)
    return StateStore(factory.get_dynamodb_resource())


def cmd_run(args: argparse.Namespace) -> None:
    if args.target == "aws":
        settings = Settings()
        if not settings.api_endpoint:
            print("UPGRADE_AGENT_API_ENDPOINT is not set", file=sys.stderr)
            sys.exit(1)

        manifest = ManifestLoader.load_from_file(args.manifest)
        try:
            result = signed_post(
                f"{settings.api_endpoint}/runs",
                {"manifest": manifest.model_dump(mode="json")},
                settings.aws_region,
            )
        except (NoCredentialsError, SignedRequestError) as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2))
        return

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
    if args.target == "aws":
        state_store = _get_state_store()
        try:
            metadata = state_store.get_run_metadata(args.run_id)
        except KeyError:
            print(f"No run found for run_id={args.run_id}", file=sys.stderr)
            sys.exit(1)
        pipelines = state_store.get_all_pipelines(args.run_id)
        print(json.dumps({"run_id": args.run_id, "metadata": metadata, "pipelines": pipelines}, indent=2, default=str))
        return

    print(json.dumps(_load_snapshot(args.run_id), indent=2))


def _delete_branches(source_control: dict, run_id: str) -> None:
    settings = Settings()
    github_client = GitHubClient(
        token=settings.github_token,
        owner=source_control["owner"],
        repo=source_control["repo"],
    )

    prefix = source_control["target"]["branch_prefix"]
    branches = [f"{prefix}/{run_id}-baseline", f"{prefix}/{run_id}-target"]

    for branch in branches:
        try:
            github_client.delete_branch(branch)
            print(f"Deleted branch {branch}")
        except GitHubAPIError as e:
            print(f"Could not delete {branch}: {e}", file=sys.stderr)

    github_client.close()


def cmd_cleanup(args: argparse.Namespace) -> None:
    if args.target == "aws":
        state_store = _get_state_store()
        try:
            metadata = state_store.get_run_metadata(args.run_id)
        except KeyError:
            print(f"No run found for run_id={args.run_id}", file=sys.stderr)
            sys.exit(1)
        source_control = metadata["config"]["source_control"]
        _delete_branches(source_control, args.run_id)
        return

    snapshot = _load_snapshot(args.run_id)
    source_control = snapshot["metadata"]["config"]["source_control"]
    _delete_branches(source_control, args.run_id)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="upgrade-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Run an upgrade test")
    run_parser.add_argument("--manifest", required=True, help="Path to the test manifest YAML")
    run_parser.add_argument("--target", choices=["local", "aws"], default="local")
    run_parser.set_defaults(func=cmd_run)

    status_parser = subparsers.add_parser("status", help="Show a run's current status")
    status_parser.add_argument("--run-id", required=True)
    status_parser.add_argument("--target", choices=["local", "aws"], default="local")
    status_parser.set_defaults(func=cmd_status)

    cleanup_parser = subparsers.add_parser("cleanup", help="Delete a run's GitHub branches")
    cleanup_parser.add_argument("--run-id", required=True)
    cleanup_parser.add_argument("--target", choices=["local", "aws"], default="local")
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
