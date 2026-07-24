"""Phase 15.2: multi-turn ReAct debugging loop.

Invoked from analyze_node.py only when a single-shot LLMAnalyzer.analyze()
call already came back low-confidence or "escalate" - the pattern matcher
found nothing, and one blind look at a log excerpt wasn't enough either.
Instead of giving up, this gives the model tools to go investigate
(src/analysis/react_tools.py) over multiple turns, capped at
MAX_ITERATIONS so a confused model can't loop or spend unboundedly.

This does not change the safety story: whatever diagnosis comes out still
flows through analyze_node.py's existing AWAIT_APPROVAL path exactly like
a single-shot LLM diagnosis does - it never auto-applies.
"""

from __future__ import annotations

import json

from openai import OpenAI

from src.analysis.llm_analyzer import DEFAULT_MODEL, Diagnosis, _PRICING_PER_MILLION_TOKENS_USD
from src.analysis.react_tools import build_tools

try:
    from langsmith import traceable
except ImportError:  # pragma: no cover - langsmith is a pinned dependency

    def traceable(*args, **kwargs):
        def _decorator(fn):
            return fn

        return _decorator if not (args and callable(args[0])) else args[0]

MAX_ITERATIONS = 5

_PROPOSE_FIX_TOOL = {
    "type": "function",
    "function": {
        "name": "propose_fix",
        "description": "Terminal action - call this once you have enough evidence to give a final diagnosis. "
        "Ends the investigation.",
        "parameters": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "classification": {
                    "type": "string",
                    "enum": ["config_fix", "dependency_fix", "code_change", "escalate"],
                },
                "fix_suggestion": {"type": ["string", "null"]},
                "confidence": {"type": "number"},
                "fix_key": {
                    "type": ["string", "null"],
                    "description": "Spark config key, only if fix is expressible as one key/value pair",
                },
                "fix_value": {"type": ["string", "null"]},
                "is_mitigation": {
                    "type": "boolean",
                    "description": "true if this works around the new Spark behavior rather than adapting to it",
                },
            },
            "required": ["root_cause", "classification", "confidence"],
        },
    },
}


class ReactAnalyzer:
    def __init__(
        self,
        api_key: str,
        github_client,
        state_store,
        model: str = DEFAULT_MODEL,
        migration_notes: list[str] | None = None,
        client=None,
    ):
        self.model = model
        self.migration_notes = migration_notes or []
        self.github_client = github_client
        self.state_store = state_store
        self._client = client if client is not None else (OpenAI(api_key=api_key) if api_key else None)

    @traceable(name="react_loop.analyze", run_type="chain")
    def analyze(
        self,
        log_content: str,
        upgrade_context: dict,
        run_id: str,
        target_branch: str,
        pipeline_id: str,
        single_shot_root_cause: str,
    ) -> Diagnosis:
        if self._client is None:
            return Diagnosis(
                root_cause="ReAct analysis unavailable: no API key configured",
                classification="escalate",
                fix_suggestion=None,
                confidence=0.0,
            )

        tool_specs, dispatch = build_tools(
            log_content=log_content,
            github_client=self.github_client,
            state_store=self.state_store,
            run_id=run_id,
            target_branch=target_branch,
        )
        tools = tool_specs + [_PROPOSE_FIX_TOOL]

        messages = [
            {"role": "system", "content": self._build_system_prompt(upgrade_context, pipeline_id)},
            {
                "role": "user",
                "content": (
                    "A single-shot analysis of this failure was inconclusive:\n"
                    f"{single_shot_root_cause}\n\n"
                    "Investigate further using the available tools, then call propose_fix with your "
                    f"final diagnosis. Log excerpt:\n\n{log_content[:3000]}"
                ),
            },
        ]

        tools_used: list[str] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0

        for _ in range(MAX_ITERATIONS):
            response = self._client.chat.completions.create(
                model=self.model, messages=messages, tools=tools, tool_choice="auto"
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                total_prompt_tokens += int(getattr(usage, "prompt_tokens", 0) or 0)
                total_completion_tokens += int(getattr(usage, "completion_tokens", 0) or 0)

            msg = response.choices[0].message
            tool_calls = msg.tool_calls or []
            if not tool_calls:
                # Model gave up without calling propose_fix - treat as escalate.
                return self._finalize(
                    Diagnosis(
                        root_cause=msg.content or "Investigation ended without a conclusive diagnosis.",
                        classification="escalate",
                        fix_suggestion=None,
                        confidence=0.1,
                    ),
                    tools_used,
                    total_prompt_tokens,
                    total_completion_tokens,
                )

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in tool_calls
                    ],
                }
            )

            for tc in tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                if tc.function.name == "propose_fix":
                    return self._finalize(
                        Diagnosis(
                            root_cause=args.get("root_cause", ""),
                            classification=args.get("classification", "escalate"),
                            fix_suggestion=args.get("fix_suggestion"),
                            confidence=float(args.get("confidence", 0.0)),
                            fix_key=args.get("fix_key"),
                            fix_value=args.get("fix_value"),
                            is_mitigation=bool(args.get("is_mitigation", False)),
                        ),
                        tools_used,
                        total_prompt_tokens,
                        total_completion_tokens,
                    )

                tools_used.append(tc.function.name)
                result = dispatch(tc.function.name, args)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

        return self._finalize(
            Diagnosis(
                root_cause=f"Investigation hit the {MAX_ITERATIONS}-iteration cap without a conclusive diagnosis.",
                classification="escalate",
                fix_suggestion=None,
                confidence=0.1,
            ),
            tools_used,
            total_prompt_tokens,
            total_completion_tokens,
        )

    def _finalize(self, diagnosis, tools_used, prompt_tokens, completion_tokens) -> Diagnosis:
        rates = _PRICING_PER_MILLION_TOKENS_USD.get(self.model, {})
        cost = (
            prompt_tokens * rates.get("prompt", 0.0) + completion_tokens * rates.get("completion", 0.0)
        ) / 1_000_000
        diagnosis.model = self.model
        diagnosis.prompt_tokens = prompt_tokens
        diagnosis.completion_tokens = completion_tokens
        diagnosis.estimated_cost_usd = cost
        diagnosis.tools_used = tools_used
        return diagnosis

    def _build_system_prompt(self, upgrade_context: dict, pipeline_id: str) -> str:
        notes = "\n".join(f"- {note}" for note in self.migration_notes)
        baseline = upgrade_context.get("baseline_spark_version", "unknown")
        target = upgrade_context.get("target_spark_version", "unknown")
        return (
            "You are a Spark upgrade diagnostics assistant investigating a PySpark job failure after "
            f"upgrading from Spark {baseline} to Spark {target}, pipeline {pipeline_id!r}. "
            "Known breaking changes for this upgrade:\n"
            f"{notes}\n\n"
            "You have tools to investigate further: grep_log to search the full log for evidence not "
            "shown in the excerpt, read_file to check the actual source line that failed, "
            "get_run_history to see if this pipeline hit a similar failure before, and search_web to "
            "look up how others have diagnosed or fixed a similar error. Use as many as are useful, up "
            f"to {MAX_ITERATIONS} tool calls, then call propose_fix with your final diagnosis.\n\n"
            "IMPORTANT: only diagnose a root cause you can point to specific evidence for - from the "
            "log, a file you read, or a search result. If after investigating you still don't have "
            "clear evidence, call propose_fix with classification \"escalate\" and confidence 0.1 or "
            "lower rather than guessing. A wrong but confident-sounding guess is worse than admitting "
            "there isn't enough evidence."
        )
