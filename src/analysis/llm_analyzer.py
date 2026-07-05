"""LLM-based diagnosis for failures the pattern matcher doesn't recognize.

Only invoked as a fallback - pattern_matcher.py handles every known Spark
4.0 breaking change via regex first. This exists for failures nobody
anticipated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

from openai import OpenAI

DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class Diagnosis:
    root_cause: str
    classification: str  # config_fix | dependency_fix | code_change | escalate
    fix_suggestion: Optional[str]
    confidence: float


class LLMAnalyzer:
    def __init__(
        self,
        api_key: str,
        model: str = DEFAULT_MODEL,
        migration_notes: list[str] | None = None,
        client=None,
    ):
        self.model = model
        self.migration_notes = migration_notes or []

        if client is not None:
            self._client = client
        elif api_key:
            self._client = OpenAI(api_key=api_key)
        else:
            self._client = None

    def analyze(self, log_content: str, upgrade_context: dict) -> Diagnosis:
        if self._client is None:
            return Diagnosis(
                root_cause="LLM analysis unavailable: no API key configured",
                classification="escalate",
                fix_suggestion=None,
                confidence=0.0,
            )

        response = self._client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self._build_system_prompt(upgrade_context)},
                {"role": "user", "content": f"Analyze this Spark job failure log:\n\n{log_content}"},
            ],
            response_format={"type": "json_object"},
        )

        data = json.loads(response.choices[0].message.content)
        return Diagnosis(
            root_cause=data.get("root_cause", ""),
            classification=data.get("classification", "escalate"),
            fix_suggestion=data.get("fix_suggestion"),
            confidence=float(data.get("confidence", 0.0)),
        )

    def _build_system_prompt(self, upgrade_context: dict) -> str:
        notes = "\n".join(f"- {note}" for note in self.migration_notes)
        baseline = upgrade_context.get("baseline_spark_version", "unknown")
        target = upgrade_context.get("target_spark_version", "unknown")

        return (
            "You are a Spark upgrade diagnostics assistant. A PySpark job "
            f"failed after upgrading from Spark {baseline} to Spark {target}. "
            "Known breaking changes for this upgrade:\n"
            f"{notes}\n\n"
            "Given a job failure log, identify the root cause and classify it "
            "as exactly one of: config_fix, dependency_fix, code_change, escalate. "
            "Respond with a JSON object with keys: root_cause (string), "
            "classification (string), fix_suggestion (string or null), "
            "confidence (number from 0 to 1)."
        )
