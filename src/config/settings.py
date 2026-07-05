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
