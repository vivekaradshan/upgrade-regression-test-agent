"""Environment-based settings for the upgrade testing agent."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    github_token: str = field(default_factory=lambda: os.environ.get("GITHUB_TOKEN", ""))
    llm_api_key: str = field(default_factory=lambda: os.environ.get("OPENAI_API_KEY", ""))
    workspace_dir: str = field(
        default_factory=lambda: os.environ.get("WORKSPACE_DIR", "/tmp/upgrade-agent")
    )
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    aws_region: str = field(default_factory=lambda: os.environ.get("AWS_REGION", "us-east-1"))
    # POST {api_endpoint}/runs triggers a run via Step Functions instead of
    # running the LangGraph orchestrator in-process - see cli.py's
    # --target aws.
    api_endpoint: str = field(default_factory=lambda: os.environ.get("UPGRADE_AGENT_API_ENDPOINT", ""))
