"""Regex matching against the manifest's known Spark 4.0 failure signatures.

Runs before any LLM call: instant, free, and deterministic. Only unmatched
failures fall through to llm_analyzer.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from src.config.manifest import KnownFailurePattern


@dataclass
class MatchResult:
    pattern: str
    diagnosis: str
    action: str
    auto_fix: bool
    fix_config: Optional[dict]


class PatternMatcher:
    def __init__(self, known_failure_patterns: list[KnownFailurePattern]):
        self._patterns = known_failure_patterns

    def match(self, log_content: str) -> MatchResult | None:
        for pattern in self._patterns:
            if re.search(pattern.pattern, log_content, re.IGNORECASE):
                return MatchResult(
                    pattern=pattern.pattern,
                    diagnosis=pattern.diagnosis,
                    action=pattern.action,
                    auto_fix=pattern.auto_fix,
                    fix_config=pattern.fix_config.model_dump() if pattern.fix_config else None,
                )
        return None
