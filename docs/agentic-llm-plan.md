# Agentic LLM Decisioning Plan (Step 15)

## Status

Not started. Depends on Step 14 (AWS production deployment) being
complete first — see [`docs/aws-deployment-plan.md`](aws-deployment-plan.md)
for that status. This document expands on several items already listed in
the main [README](../README.md)'s Future Enhancements section (HITL
approval, self-improving pattern library, mitigation vs. remediation
labeling) into a concrete, phased build plan.

## Context

The system today has exactly one LLM call: `LLMAnalyzer.analyze()`
(`src/analysis/llm_analyzer.py`), invoked only when the manifest's
regex-based `known_failure_patterns` don't match a target-job failure. It
returns a `Diagnosis` dataclass via OpenAI's `response_format:
json_object` mode (no schema enforcement, no tool-calling). Critically,
whatever it diagnoses is *never* auto-applied — `analyze_node.py` always
hardcodes `auto_fix=False` for the LLM path (unlike the pattern-matcher
path, which can auto-fix and retry) because `fix_suggestion` is free text,
not a structured `{key, value}` pair. Every LLM diagnosis today is a dead
end that escalates straight to a human.

The goal of Step 15 is to make the LLM path load-bearing instead of a
dead end, without weakening safety — by leaning on something this system
already has that most agentic apps don't: the ability to *actually
re-run the pipeline and check the output data* before anything reaches a
human or a real branch. The organizing principle for every phase below:
an LLM proposal earns autonomy only where its result can be empirically
verified (re-execute the job, diff the data) before it takes effect;
everywhere else, it proposes and a human decides. This mirrors Step 14's
own discipline (verify against real state, not just "it applied without
erroring") applied to model output instead of infrastructure.

**LLM provider decision**: stay on OpenAI (`gpt-4o-mini`, already wired
via Secrets Manager in Lambda) rather than migrating to Bedrock or adding
a LiteLLM abstraction layer — avoids a second AWS service, new IAM
surface, and a provider-agnostic shim for a decision that can be
revisited later without blocking this work.

## What doesn't change

- `PatternMatcher` stays the first-line, free, deterministic path —
  nothing here replaces it, the LLM path only gets smarter.
- The retry loop's shape (`analyze_node.py`'s `RETRY`/`VALIDATE`/`REPORT`
  routing, `manifest.log_analysis.retry.max_retries`) is unchanged — new
  LLM-driven fixes flow through the exact same
  `_apply_fix_to_target_branch` → retry → re-execute → `validate_data`
  cycle that already exists and is already the empirical verification
  gate for config fixes.
- Blast radius stays exactly as-built: fixes only ever land on
  `auto/upgrade-test/*` branches, never `main`; `pr.auto_merge` stays
  `false`; retries stay capped by the manifest.

## Phase 15.0 — Eval harness + LLM call tracing (build first, not last)

You can't safely raise autonomy on a system you can't measure. Add
`tests/evals/fixtures/` — real or realistic failure logs (start from the
existing `tests/fixtures/spark_ansi_error.log` pattern) each paired with a
golden diagnosis (`{expected_classification, expected_fix_config_or_null,
expected_auto_fixable}`). A pytest suite
(`tests/evals/test_llm_analyzer_eval.py`) runs `LLMAnalyzer.analyze()`
against each fixture and scores classification accuracy — this is the
"promotion policy" gate every later phase's autonomy claims get checked
against, and it's the first thing to build because Phases 15.1+ are
meaningless to trust without it.

Alongside: minimal LLM call tracing, no new SaaS dependency. Extend
`state_store.record_event()` (already accepts arbitrary `**details`, no
schema change needed) to log `model`, `prompt_tokens`, `completion_tokens`,
and estimated cost on every `LLMAnalyzer.analyze()` call — surfaced in the
existing run report (`report_generator.py`) as a new "LLM usage" section.

**Verify**: `pytest tests/evals/` scores and prints a pass rate; a real
run's report shows token/cost figures for any LLM call it made.

## Phase 15.1 — Structured fix output + confidence-gated auto-apply

Replace `Diagnosis` (free-text `fix_suggestion`) with a Pydantic
`ProposedFix` model, requested via OpenAI's tool-calling (function-calling
schema), not the current manual `json.loads` on `json_object` mode —
removes an entire class of malformed-response bugs the current code has
no guard against:

```python
class ProposedFix(BaseModel):
    fix_type: Literal["config", "escalate"]  # "code_patch" deferred to 15.4
    config_key: str | None
    config_value: str | None
    rationale: str
    confidence: float
    is_mitigation: bool  # defers real migration vs. actually fixes it
```

`analyze_node.py`'s LLM branch changes from always `auto_fix=False` to:
`fix_type == "config" and confidence >= manifest-configurable threshold`
→ auto-apply via the *existing* `_apply_fix_to_target_branch`, exactly
the same code path pattern-matcher fixes already use, so the retry loop's
empirical verification (re-run, does it actually pass?) applies
unchanged. Below threshold, or `fix_type == "escalate"` → same escalation
path as today. `is_mitigation` gets surfaced in the PR body — this phase
is what implements the README's existing "mitigation vs. remediation
labeling" backlog item.

Manifest gets one new optional field:
`log_analysis.llm_auto_fix_confidence_threshold` (default e.g. `0.85`),
schema-validated in `src/config/manifest.py` alongside the existing
`RetryConfig`.

**Verify**: extend the Phase 15.0 eval harness with confidence-threshold
cases; a real manifest run where the pattern matcher is deliberately
disabled for one known failure confirms the LLM path now auto-fixes and
the retry loop actually resolves it, end-to-end, same as a
pattern-matcher fix does today.

## Phase 15.2 — ReAct debugging subgraph (tools + migration-guide RAG)

When *neither* the pattern matcher nor a single-shot LLM call resolves a
failure confidently, give the LLM tools and let it investigate over
multiple turns instead of one log blob. Implemented as a small LangGraph
subgraph invoked from inside `analyze_node.py` (not a new top-level
orchestrator node — keeps the outer Step Functions/LangGraph shape
untouched, "outer determinism, inner agency"), with a hard cap on
iterations (e.g. 5) and a token budget enforced in code, not just
prompted.

Tools, each a thin wrapper around code that already exists:

- `read_log(path, tail_n)` / `grep_log(pattern)` — wraps `LogReader`
  (`src/analysis/log_reader.py`).
- `read_file(path)` — wraps `github_client.get_file_content` against the
  target branch (already used by `_find_spark_config_file`).
- `search_migration_guide(query)` — new: a small local corpus (chunk the
  official Spark 3.5→4.0 migration guide, one markdown/text file
  committed to the repo — no need for a hosted vector DB at this scale),
  embedded with OpenAI embeddings, queried via cosine similarity in plain
  Python (no FAISS/Chroma dependency needed for a corpus this small —
  matches the project's "no dependency until you need it" pattern, e.g.
  how `pyyaml_pyfiles` was hand-packaged rather than adding a build
  tool).
- `get_run_history(pipeline_id)` — wraps `state_store.get_events`/
  `get_all_pipelines`, already exists.
- `propose_fix(ProposedFix)` — terminal action, reuses the Phase 15.1
  schema.

**Verify**: eval harness gets cases the single-shot LLM call gets wrong
but the tool-using loop should get right (e.g. a failure whose real cause
is only found by reading the actual failing line via `read_file`, not
guessable from the log alone); a real run against a deliberately
novel/unpatterned failure shows the multi-turn trace in
`record_event`-logged tool calls.

## Phase 15.3 — Human-in-the-loop approval gate

For anything below the auto-fix confidence threshold or explicitly
`fix_type: "escalate"`, add a real approval gate instead of just writing
`status=FAILED` and stopping:

- **Local**: LangGraph's `interrupt()` + a checkpointer
  (`langgraph-checkpoint` is already a pinned dependency, unused —
  `SqliteSaver` is the natural local choice). `cli.py` gets an
  `approve --run-id <id> --approve/--reject [--fix-override ...]`
  subcommand that resumes the interrupted graph.
- **AWS**: Step Functions' native `waitForTaskToken` callback pattern —
  `analyze_logs_handler.py`'s escalation path pauses at a new
  `AwaitApproval` state holding a task token, and
  `cli.py --target aws approve` (or a dashboard button, if Phase 14.7 is
  done by then) calls `send_task_success` with the human's decision,
  resuming the same execution rather than starting a new one.

Both paths reuse the exact same `ProposedFix` payload and the exact same
resume-into-retry-loop logic — the local/AWS split here is a genuinely
good demonstration of the same HITL pattern in two different execution
substrates.

**Verify**: a real local run interrupts and waits; `cli.py approve` with
`--approve` resumes and completes; `--reject` resumes into escalation.
Same for a real AWS execution via `--target aws`.

## Phase 15.4 — Verified code-patch generation

Extends `ProposedFix` with `fix_type: "code_patch"` and a `patch: str`
(unified diff) field. A patch is never committed straight to the target
branch — it's applied to a scratch worktree (mirroring how
`workspace/runs/<run_id>/` local checkouts already work locally, or a
throwaway EMR job with the patched entry script uploaded to a scratch S3
prefix on AWS), re-executed via the *same* baseline/target job dispatch
already built, and only on a passing re-execution **and** a passing
`validate_data` run does it get committed to the real target branch (via
`github_client.update_file`, same call other fixes already use) and noted
in the PR body. This is explicitly the highest-risk fix type, so it's
built last, after the eval harness (15.0), confidence gating (15.1), and
HITL (15.3) all already exist to bound it — a code-patch proposal always
requires human approval regardless of confidence score, at least
initially, promoted to auto-apply only if 15.0's eval harness later shows
it warrants that.

**Verify**: a deliberately-seeded code-level Spark 4.0 break (not just a
config flag) in the fixture pipeline, confirm the patch is generated,
verified in a scratch execution, and only committed after that
verification passes — and that a patch which *fails* re-execution never
touches the real branch.

## Phase 15.5 — Self-improving pattern library + validation-diff triage

Two independent, lower-risk additions:

- **Validation-diff triage**: when `validate_data`'s row/schema/column
  checks fail, feed the diff summary + sample mismatched rows + this
  run's applied fixes to the LLM and have it classify
  `benign | regression` with reasoning, surfaced in the report
  (`report_generator.py`). Pure annotation, never gates or acts — safe to
  run fully autonomously from day one, no eval-harness gating needed
  since it never controls flow.
- **Pattern library growth**: when a human resolves an escalation (via
  15.3's approval flow), an LLM drafts a candidate
  `known_failure_patterns` entry (regex + `fix_config`) from the resolved
  failure's log signature, written to a PR against the *manifest* file
  itself for human review (reuses `github_client`, same review discipline
  as a code change) — never auto-merged into the manifest. Once merged,
  that failure class auto-fixes via the existing pattern-matcher path
  forever after — the flywheel is the escalation count trending down over
  successive runs, directly observable via `state_store.get_events`
  across runs.

**Verify**: seed two similar-but-not-identical failures, confirm the
first escalates and produces a draft pattern-library PR on human
resolution, confirm the second (after that PR is merged) now auto-fixes
via the ordinary pattern-matcher path with no LLM call at all.

## Suggested build order and why

15.0 (eval harness) must come first — it's the only thing that makes any
later "this can be trusted to auto-apply" claim falsifiable rather than
asserted. 15.1 (structured output + confidence gating) is the smallest
change that converts the LLM path from dead-end to load-bearing, and
should ship before the more ambitious 15.2 (multi-turn tool use) so the
single-shot baseline it's compared against in evals actually exists. 15.3
(HITL) can be built in parallel with 15.2 once 15.1's `ProposedFix` schema
exists, since escalation already needs *somewhere* to go. 15.4 (code
patches) depends on 15.0/15.1/15.3 all existing since it's the highest
blast-radius fix type. 15.5 is intentionally last and lowest-priority —
both its pieces are pure value-adds with no dependency the rest of the
system needs.

## Verification approach throughout

Same discipline as Step 14: every phase's "done" bar is a real run
(local, and once relevant, `--target aws`) actually exercising the new
path against real state — not unit tests with everything mocked. Phase
15.0's eval harness is the one exception that's *allowed* to be
fixture-based rather than a live run, since scoring diagnosis quality
against a fixed corpus is the point of an eval harness in the first
place.
