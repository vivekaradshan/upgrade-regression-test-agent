# Agentic LLM Decisioning Plan (Step 15)

## Status

Phases 15.0 (tracing half only), 15.1 (simplified), 15.3 (AWS-only), and
15.5 (pattern-library-growth half only) are built and deployed against
real AWS, and have now been exercised through real seeded end-to-end
runs - which surfaced and fixed three real bugs (analyze_logs_handler.py
only fetching stderr and missing exceptions that land in stdout, no way
for a human to check an LLM diagnosis against real log evidence before
approving, and the pipeline-level `status` field never being updated on
any success path anywhere in the codebase - see git history around
commit `3e16472`). Phases 15.2 and 15.4 are not started. See "Deviations
from the original plan" below each built phase for exactly what shipped
differently than originally designed here, and why.

Depends on Step 14 (AWS production deployment) being complete first —
see [`docs/aws-deployment-plan.md`](aws-deployment-plan.md) for that
status. This document expands on several items already listed in the
main [README](../README.md)'s Future Enhancements section (HITL
approval, self-improving pattern library, mitigation vs. remediation
labeling) into a concrete, phased build plan.

## Consolidated open items, in build order

Decided this session: real LLM observability is now in scope, via
**LangSmith** (not Langfuse - Langfuse is free but self-hosted, requiring
a new Postgres + server to run and pay for, the same infra-cost tradeoff
that already ruled out App Runner/ECS Express Mode for the dashboard;
LangSmith is SaaS with a free tier, zero new AWS infrastructure).
DynamoDB's existing event-log tracking (`llm_call`, `awaiting_approval`,
`approval_approved`/`approval_rejected` events) is retained as-is, not
replaced - LangSmith adds call-level tracing/cost/latency visualization
on top of it, not a substitute for the run's own audit trail.

1. **Wire LangSmith into the existing single-shot `LLMAnalyzer.analyze()`
   call first**, before anything else - smallest possible surface to
   prove the integration works, and it retroactively gives real tracing
   to everything already built (15.0/15.1/15.3) immediately, rather than
   needing to be retrofitted into a more complex loop later.
2. **Eval harness** (the still-missing half of Phase 15.0) - built after
   observability is wired in, not before, so eval runs are traced too
   from day one.
3. **Phase 15.2, ReAct debugging loop** - `grep_log`, `read_file`,
   `get_run_history`, `propose_fix`, plus a `search_web` tool (via
   Tavily - a real addition beyond this document's original
   `search_migration_guide` RAG-over-local-corpus design, see Phase
   15.2's section below), triggered by a human rejecting a proposed fix
   with feedback text instead of just terminally rejecting it. Needs a
   state machine change: `AwaitApproval`'s `Next` is currently hardcoded
   to `CheckRetry`; it needs to become a `Choice` reading the resumed
   `phase` (`RETRY` → `CheckRetry`, `AWAIT_APPROVAL` → loop back to
   `AwaitApproval` with a new proposal, `REPORT` → `GenerateReport`).
4. **CloudWatch alarm on `AWAIT_APPROVAL`** - reuses the SNS topic Phase
   14.6 already built for execution failures; actively notifies when a
   run needs human review instead of requiring the dashboard to be
   polled. Cheap, independent, can build anytime.
5. **Dashboard "AI Decision Timeline"** - a read-only view joining the
   existing `llm_call`/`awaiting_approval`/`approval_*` DynamoDB events
   (via `state_store.get_events()`) into one chronological per-run
   timeline. No new instrumentation needed, purely presentation.
   Independent, can build anytime.
6. **A genuine, complete Test A run** - an LLM (now via the ReAct loop)
   proposes a real fix, a human clicks Approve in the dashboard, EMR
   actually re-executes with the fix applied, validation passes. Never
   cleanly finished during this session's testing - the codec scenario
   used to test this accidentally became a legitimate "no fix available"
   case (see Phase 15.2/15.3's notes on the real stdout/stderr bug this
   surfaced), and a follow-up attempt using the real ANSI failure got
   interrupted by the status-field bug hunt. `main` on the demo pipeline
   repo has been reset to the 3.5.4 baseline and is ready for this.
7. **Step 14.9** - final end-to-end validation + real cost check +
   cleanup, folded into the same run as item 6 rather than a separate one.
8. **Phase 15.5's remaining half** (validation-diff triage) and **Phase
   15.4** (verified code-patch generation) - both independent,
   lower-priority, order between them doesn't matter.
9. **Phase 14.8 (CI/CD)** - already deprioritized (see
   `docs/aws-deployment-plan.md`); stays last, optional.

## Context

The system today has exactly one LLM call: `LLMAnalyzer.analyze()`
(`src/analysis/llm_analyzer.py`), invoked only when the manifest's
regex-based `known_failure_patterns` don't match a target-job failure. It
returns a `Diagnosis` dataclass via OpenAI's `response_format:
json_object` mode (no schema enforcement, no tool-calling). Critically,
whatever it diagnoses is *never* auto-applied — `analyze_node.py` always
hardcodes `auto_fix=False` for the LLM path (unlike the pattern-matcher
path, which can auto-fix and retry) because `fix_suggestion` was free
text, not a structured `{key, value}` pair. As originally written, every
LLM diagnosis was a dead end that escalated straight to a human with
nothing they could act on beyond reading prose.

*(Updated after Phase 15.1/15.3 were built: this is no longer strictly
true — `Diagnosis` now carries a structured `fix_key`/`fix_value`, and a
human can act on it via the dashboard's approval flow. It's still true
that nothing auto-applies regardless of confidence, by explicit design —
see Phase 15.1's "Deviation" note below.)*

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
  routing, plus the new `AWAIT_APPROVAL` phase, `manifest.log_analysis.
  retry.max_retries`) is unchanged in its core mechanics — new
  LLM-driven fixes flow through the exact same
  `apply_fix_to_target_branch` (now in `src/tools/config_fix.py`) →
  retry → re-execute → `validate_data` cycle that already exists and is
  already the empirical verification
  gate for config fixes.
- Blast radius stays exactly as-built: fixes only ever land on
  `auto/upgrade-test/*` branches, never `main`; `pr.auto_merge` stays
  `false`; retries stay capped by the manifest.

## Phase 15.0 — Eval harness + LLM call tracing (build first, not last)

### ✅ Tracing — built
`LLMAnalyzer.analyze()`'s `Diagnosis` now carries `model`, `prompt_tokens`,
`completion_tokens`, `estimated_cost_usd` (extracted from the real OpenAI
response's `usage` field; cost is estimated via a hardcoded per-token
pricing table since OpenAI doesn't return billed cost directly).
`analyze_node.py` records one `event="llm_call"` per invocation via
`state_store.record_event()` (as planned — no schema change needed);
`report_node.py`/`generate_report_handler.py` sum these via
`state_store.get_events()` into a new "LLM Usage" report section
(`report_generator.py`, `report.html`), omitted entirely when the LLM was
never consulted. Verified with a real OpenAI call against a hand-built
failure log (the `lz4raw`→`lz4_raw` codec rename, a real Spark 4.0 change
already in the manifest's `migration_notes` but absent from
`known_failure_patterns`) — correctly returned 422 prompt / 94 completion
tokens and ~$0.00012 estimated cost.

### ⬜ Eval harness — not built
`tests/evals/fixtures/` + `tests/evals/test_llm_analyzer_eval.py` as
originally planned don't exist yet. This is a real gap — later phases'
"this can be trusted" claims (specifically Phase 15.1's decision to
always require human approval regardless of confidence, see below) are
currently a judgment call, not something falsifiable against a scored
corpus.

**Verify**: `pytest tests/evals/` scores and prints a pass rate (not yet
possible — harness doesn't exist); a real run's report shows token/cost
figures for any LLM call it made (done — see above).

### ⬜ Real LLM observability (LangSmith) — decided, not yet wired in
DynamoDB's own event-log tracking above is retained as the run's audit
trail — this doesn't replace it, it adds call-level tracing, latency, and
cost visualization on top via a dedicated LLM observability platform.
**LangSmith** chosen over Langfuse: Langfuse is free but self-hosted
(a new Postgres + server to run and pay for — the same infra-cost
tradeoff that already ruled out App Runner/ECS Express Mode for the
dashboard, see `docs/aws-deployment-plan.md`); LangSmith is SaaS with a
free tier, zero new AWS infrastructure. `LANGSMITH_API_KEY`/
`LANGSMITH_TRACING`/`LANGSMITH_PROJECT` placeholders are in `.env`/
`.env.example`. Sequencing: wire into the existing single-shot
`LLMAnalyzer.analyze()` call first (smallest surface, proves the
integration, retroactively traces everything already built) before the
eval harness or Phase 15.2's ReAct loop are built on top of it.

## Phase 15.1 — Structured fix output ✅ built (simplified — no auto-apply)

### What shipped
`Diagnosis` (`src/analysis/llm_analyzer.py`) was extended in place rather
than replaced with a separate `ProposedFix` Pydantic model — it now
carries `fix_key`, `fix_value`, and `is_mitigation` alongside the
existing fields, still requested via the same `response_format:
json_object` prompt (just asking for three more JSON keys) and parsed
with plain `json.loads`, not OpenAI tool-calling / schema-enforced
output. `fix_key`/`fix_value` are the same `{key, value}` shape a
pattern-matcher `fix_config` already uses — the part that actually
matters (something a human-approved fix can execute), extracted into
`src/tools/config_fix.py` so both the pattern-matcher auto-fix path and
the new human-approved-fix path (Phase 15.3) apply a fix identically.

`is_mitigation` is surfaced in `analysis_result` and the report/dashboard
— the README's "mitigation vs. remediation labeling" backlog item is
implemented for the LLM path specifically.

### Deviation: no confidence-gated auto-apply, no manifest threshold
The plan's central mechanism — `confidence >= threshold` → auto-apply —
was **not** built. Instead, `analyze_node.py`'s LLM branch *always*
routes to phase `AWAIT_APPROVAL` (Phase 15.3) regardless of confidence,
by explicit decision made when actually building this: a human must
approve every LLM-diagnosed fix, full stop, no matter how confident the
model is. No `log_analysis.llm_auto_fix_confidence_threshold` manifest
field exists. Confidence is still captured and shown to the human
approving the fix — it informs their judgment, it just doesn't
autonomously gate anything (yet; this is exactly the kind of decision the
missing eval harness above should eventually inform, rather than it
being asserted).

**Verify**: extend the Phase 15.0 eval harness with confidence-threshold
cases (moot until an auto-apply threshold exists); a real manifest run
where the pattern matcher genuinely doesn't recognize the failure
confirms the LLM path produces a usable structured fix and routes to
approval rather than silently escalating to FAILED with a wasted
diagnosis — done, see Phase 15.3's verification.

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
- `search_web(query)` — **added beyond the original design** (decided
  this session, not in the initial plan): the three tools above only let
  the model dig into things this project already has - its own log, its
  own repo, its own history. For a genuinely novel failure not covered by
  the manifest's documented breaking changes, none of them can find out
  *why* Spark actually changed or how others fixed the same thing - a
  real ceiling a human engineer wouldn't have. Uses Tavily (API key
  placeholder in `.env`/`.env.example`) rather than a raw scraper.
  Doesn't change the safety story at all - whatever `propose_fix` returns,
  web-sourced or not, still goes through the same human-approval gate and
  empirical re-execution before it can land; web content is treated as
  untrusted data for the model to reason about, not instructions.
- `get_run_history(pipeline_id)` — wraps `state_store.get_events`/
  `get_all_pipelines`, already exists.
- `propose_fix(...)` — terminal action, reuses whatever `Diagnosis`'s
  shape looks like by the time this is built (see Phase 15.1's actual
  shipped shape above, not the original `ProposedFix` design).

**Verify**: eval harness gets cases the single-shot LLM call gets wrong
but the tool-using loop should get right (e.g. a failure whose real cause
is only found by reading the actual failing line via `read_file`, not
guessable from the log alone); a real run against a deliberately
novel/unpatterned failure shows the multi-turn trace in
`record_event`-logged tool calls.

## Phase 15.3 — Human-in-the-loop approval gate ✅ built, AWS-only

*Every* LLM diagnosis routes here now (see Phase 15.1's deviation — there's
no confidence threshold below which this is skipped), not just failures
below some threshold.

### What shipped (AWS)
Step Functions' native `waitForTaskToken` service integration, as
planned: `state_machine.json.tpl`'s new `AwaitApproval` state genuinely
pauses the execution (not polling) until something external resumes it.
`await_approval_handler.py` is invoked by that state - it doesn't
complete the task itself, it just parks the task token plus the full
orchestrator state in DynamoDB (the only channel available to hand a
`waitForTaskToken` token to something outside that one Lambda
invocation). `approve_run_handler.py` (`POST /runs/{run_id}/approve`,
same API Gateway `start_run` already uses) is what a human actually
calls, via a new Approve/Reject button in `dashboard/app.py`'s AWS mode
(`render_approval_gate()`) — not a CLI command as originally planned.
Approve applies the fix via `config_fix.py` (identical mechanism to a
pattern-matcher auto-fix) and calls `send_task_success` with output
shaped to flow into the *existing* `CheckRetry` loop, so the retry that
follows is indistinguishable from an ordinary pattern-matcher-triggered
retry. Reject calls `send_task_failure`, which the state machine's
`Catch` on `AwaitApproval` routes through the same `REPORT`/`FAILED` path
an unfixable pattern-matcher escalation already takes.

### Deviation: no local mode
The local LangGraph `interrupt()` + `SqliteSaver` checkpointer half was
not built — `langgraph-checkpoint` remains an unused pinned dependency.
Locally, an LLM diagnosis still just ends the run at status
`AWAITING_APPROVAL` (a more accurate label than the old `FAILED`, but
nothing acts on it - see `analyze_node.py`'s docstring). This was a
deliberate scope cut when actually building this, not an oversight: the
real motivating use case for this phase was specifically an AWS run
approved through the dashboard, not local development. No `cli.py
approve` subcommand exists either, for the same reason - approval is
dashboard-only.

**Verify**: a real AWS execution genuinely paused at `AwaitApproval` and
resumed correctly on approval (target job re-ran with the fix applied,
flowed through `CheckRetry` → validate → report → PR exactly like an
ordinary retry) — not yet exercised end-to-end with a real seeded
failure; infrastructure is deployed and unit-tested but this specific
claim needs a real run to confirm. Local mode / `--reject` / CLI-based
approval are not applicable (not built).

## Phase 15.4 — Verified code-patch generation ⬜ not started

Extends `Diagnosis`'s shipped shape (Phase 15.1) with a `fix_type:
"code_patch"` classification and a `patch: str` (unified diff) field. A
patch is never committed straight to the target
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

### ✅ Pattern library growth — built (narrower trigger than planned)
`raise_pr_handler.py` checks whether a `PASSED` run's fix was
human-*approved* through Phase 15.3's flow specifically (an
`approved_llm_fix` flag `approve_run_handler.py` sets on the run's
DynamoDB metadata) — narrower than the original "when a human resolves
an escalation" wording, which could have included a human fixing things
manually out-of-band with no structured `fix_key`/`fix_value` to work
from. If the flag is set, it opens a *second* PR — against this repo
(`upgrade-regression-test-agent`), not the pipeline repo the first PR
targets — proposing the diagnosis + fix be added to
`known_failure_patterns`. The regex itself is a verbatim `re.escape()` of
the diagnosis text rather than LLM-drafted (simpler; the PR body
explicitly flags this as needing human judgment on whether it's too
narrow or too broad). Never auto-merged, same review discipline as any
other change.

### ⬜ Validation-diff triage — not built
No change yet to how a failed `validate_data` result is reported; still
just raw check results, no LLM classification of benign vs. regression.

**Verify**: seed two similar-but-not-identical failures, confirm the
first escalates and produces a draft pattern-library PR on human
resolution, confirm the second (after that PR is merged) now auto-fixes
via the ordinary pattern-matcher path with no LLM call at all — not yet
run; needs the same real seeded-failure end-to-end test Phase 15.3 is
waiting on.

## Suggested build order and why

*(Original plan, kept for reference — actual build order deviated: 15.1's
structured fields, 15.3's AWS approval flow, and 15.5's pattern-library
growth were built together in one session, ahead of the eval harness,
since the immediate goal was demonstrating the human-in-the-loop
mechanics end-to-end rather than following the strict dependency order
below. The eval-harness gap this leaves is real — see Phase 15.0's
status.)*

15.0 (eval harness) must come first — it's the only thing that makes any
later "this can be trusted to auto-apply" claim falsifiable rather than
asserted. 15.1 (structured output + confidence gating) is the smallest
change that converts the LLM path from dead-end to load-bearing, and
should ship before the more ambitious 15.2 (multi-turn tool use) so the
single-shot baseline it's compared against in evals actually exists. 15.3
(HITL) can be built in parallel with 15.2 once 15.1's structured fix
fields exist, since escalation already needs *somewhere* to go. 15.4 (code
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
