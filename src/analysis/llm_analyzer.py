"""LLM-based diagnosis for failures the pattern matcher doesn't recognize.

Only invoked as a fallback - pattern_matcher.py handles every known Spark
4.0 breaking change via regex first. This exists for failures nobody
anticipated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI

try:
    # Optional: only present if LANGSMITH_API_KEY is set (see .env.example).
    # traceable() is a no-op decorator when tracing isn't configured, so
    # this import is safe even with no LangSmith account at all.
    from langsmith import traceable
except ImportError:  # pragma: no cover - langsmith is a pinned dependency
    def traceable(*args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator if not (args and callable(args[0])) else args[0]

DEFAULT_MODEL = "gpt-4o-mini"

# Published per-token pricing (USD), used only to estimate cost for the
# run report / eval harness tracing - not fetched from OpenAI's billing
# API, so this can drift from actual billed cost if pricing changes.
# https://openai.com/api/pricing/ as of this model's cutoff.
_PRICING_PER_MILLION_TOKENS_USD = {
    "gpt-4o-mini": {"prompt": 0.150, "completion": 0.600},
}


@dataclass
class Diagnosis:
    root_cause: str
    classification: str  # config_fix | dependency_fix | code_change | escalate
    fix_suggestion: Optional[str]
    confidence: float
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float = 0.0
    # fix_suggestion alone is free text, useless for automation - fix_key/
    # fix_value are the same {key, value} shape a pattern-matcher fix_config
    # already uses, so a human-approved LLM fix can flow through the exact
    # same _apply_fix_to_target_branch() call (see src/tools/config_fix.py)
    # instead of needing a human to manually translate prose into a config
    # change. None when the LLM can't express its suggestion as a single
    # config key/value (e.g. classification is "code_change" or "escalate").
    fix_key: Optional[str] = None
    fix_value: Optional[str] = None
    # Distinguishes "actually fixes the new Spark version's behavior" from
    # "papers over it by reverting to the old behavior" (e.g. disabling ANSI
    # mode rather than fixing the arithmetic) - surfaced in the approval UI
    # and PR body so a human reviewer knows which one they're approving.
    is_mitigation: bool = False
    # Names of tools the ReAct loop (src/analysis/react_loop.py) called
    # before reaching this diagnosis, e.g. ["grep_log", "read_file"] -
    # empty for a plain single-shot analyze() call. Surfaced in the
    # approval UI so a reviewer knows how much investigation actually
    # backs a given confidence score.
    tools_used: list[str] = field(default_factory=list)


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

    @traceable(name="llm_analyzer.analyze", run_type="chain")
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
        prompt_tokens, completion_tokens = self._extract_usage(response)
        return Diagnosis(
            root_cause=data.get("root_cause", ""),
            classification=data.get("classification", "escalate"),
            fix_suggestion=data.get("fix_suggestion"),
            confidence=float(data.get("confidence", 0.0)),
            model=self.model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost_usd=self._estimate_cost(prompt_tokens, completion_tokens),
            fix_key=data.get("fix_key"),
            fix_value=data.get("fix_value"),
            is_mitigation=bool(data.get("is_mitigation", False)),
        )

    def _extract_usage(self, response) -> tuple[int, int]:
        """Real OpenAI responses always include `usage`; defensive
        fallback to 0 covers hand-built mocks in tests that don't bother
        setting it up (and would otherwise silently return a MagicMock
        instead of an int)."""
        usage = getattr(response, "usage", None)
        if usage is None:
            return 0, 0
        try:
            return int(usage.prompt_tokens), int(usage.completion_tokens)
        except (TypeError, ValueError):
            return 0, 0

    def _estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        rates = _PRICING_PER_MILLION_TOKENS_USD.get(self.model)
        if rates is None:
            return 0.0
        return (prompt_tokens * rates["prompt"] + completion_tokens * rates["completion"]) / 1_000_000

    def _build_system_prompt(self, upgrade_context: dict) -> str:
        notes = "\n".join(f"- {note}" for note in self.migration_notes)
        baseline = upgrade_context.get("baseline_spark_version", "unknown")
        target = upgrade_context.get("target_spark_version", "unknown")

        return (
            "You are a Spark upgrade diagnostics assistant. A PySpark job "
            f"failed after upgrading from Spark {baseline} to Spark {target}. "
            "Known breaking changes for this upgrade:\n"
            f"{notes}\n\n"
            "IMPORTANT: only diagnose a root cause you can point to specific "
            "evidence for in the log below. If the log does not contain a "
            "clear exception, stack trace, or error message that explains "
            "the failure - do not guess based on what's typically wrong with "
            "this kind of upgrade, even if something above looks like a "
            "plausible fit. Instead set classification to \"escalate\", give "
            "confidence 0.1 or lower, and say plainly in root_cause that the "
            "log does not contain enough information to diagnose this "
            "failure and a human needs to investigate directly (e.g. check "
            "additional log streams). A wrong but confident-sounding guess "
            "is worse than admitting there isn't enough evidence.\n\n"
            "Given a job failure log, identify the root cause and classify it "
            "as exactly one of: config_fix, dependency_fix, code_change, escalate. "
            "Respond with a JSON object with keys: root_cause (string), "
            "classification (string), fix_suggestion (string or null), "
            "confidence (number from 0 to 1), fix_key (string or null - the "
            "single Spark config key to change, ONLY if the fix is expressible "
            "as one config key/value pair; null otherwise), fix_value (string "
            "or null - the value to set fix_key to, as a string), is_mitigation "
            "(boolean - true if this fix works around the new Spark version's "
            "behavior rather than actually adapting the pipeline to it, e.g. "
            "reverting to old behavior via a legacy/compat flag)."
        )
