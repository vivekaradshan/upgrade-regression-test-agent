# AWS Deployment Plan (Step 14)

## Status

Phases 14.0–14.7 complete and verified against real AWS. Phases
14.8–14.9 not started. See the main [README](../README.md) for Steps
1–13 (the local build, complete).

Target: a personal AWS account (`068378433969`, `us-east-1`), single
environment — production-quality practices (least-privilege IAM,
Secrets Manager, Terraform-managed infra, monitoring, cost guardrails)
but not a multi-account/multi-env enterprise setup.

## Guiding principle

Every local component in `src/mock_infra/` and `src/orchestrator/nodes/*.py`
was written so that swapping to real AWS is mostly a substitution, not a
rewrite. Concretely: `src/aws_lambda/*.py` handlers are thin wrappers that
build the same dependency objects the local nodes expect (`GitHubClient`,
`StateStore`, `LLMAnalyzer`) and call the *existing, unmodified* node
function — the only new code is the Lambda/Step-Functions wiring.

## Architecture decisions

These were resolved through explicit tradeoff discussions, not assumed:

- **EMR Serverless** (not EC2) runs the actual PySpark jobs — matches the
  short/bursty per-run profile, no cluster lifecycle to manage. Two
  applications (`baseline`, `target`) since release label is fixed per
  application and Spark 3.5 vs 4.0 need different release labels
  (`emr-7.13.0` vs `emr-spark-8.0.0`).
- **Terraform** for all infrastructure-as-code.
- **Step Functions + Lambda as the orchestrator — not Fargate.** Each
  lightweight LangGraph node becomes a thin Lambda handler wrapping the
  existing node function; `execute_jobs`/`validate_data` use Step
  Functions' native `emr-serverless:startJobRun.sync` integration (blocks
  natively, no custom polling/resume code). The retry/validate/escalate
  branching is a Step Functions `Choice` state mirroring
  `_route_after_analysis` in `src/orchestrator/graph.py`. This eliminates
  the earlier Fargate + custom-resume-Lambda design entirely — Step
  Functions persists execution state between steps natively, so there's no
  "dispatch → exit → resume from DynamoDB" hand-off to design or debug.
- **Dashboard stays local-only, not hosted** (revised from the original
  App Runner plan — see Phase 14.7) — App Runner is closed to new
  customers as of 2026-04-30, and its recommended replacement (ECS
  Express Mode) always provisions a mandatory, non-scale-to-zero ALB
  (~$16-20/month) that alone exceeds the $5/month budget with only one
  service to run. `dashboard/app.py` gets an AWS-mode data path instead,
  run locally against real AWS data.
- **API Gateway HTTP API with IAM auth (SigV4)**, not a REST API or a
  separate API key — reuses the operator's existing AWS credentials
  instead of minting another secret to store/rotate.
- **Fleet mode and human-in-the-loop chat approval remain deferred future
  enhancements** — not built, but this architecture is forward-compatible
  with both (Step Functions Distributed Map for fleet fan-out, the native
  `waitForTaskToken` callback pattern for chat-based approval) without a
  redesign later.

## Component mapping (as built)

| Local (Steps 1–13) | AWS resource | Notes |
|---|---|---|
| `src/mock_infra/mock_emr.py` (`LocalSparkRunner`, subprocess) | **EMR Serverless** (2 applications: baseline, target) | `infra/terraform/environments/prod/main.tf` |
| `src/orchestrator/graph.py` (LangGraph) | **Step Functions** state machine | `infra/terraform/environments/prod/state_machine.json.tpl` / `.tf` |
| `src/orchestrator/nodes/*.py` (called directly) | **Lambda** (7 functions, one shared layer) | `src/aws_lambda/*_handler.py`, `infra/terraform/environments/prod/lambda.tf` |
| `src/tools/state_store.py` (moto-mocked) | **DynamoDB** table `upgrade-test-runs` | Same schema, zero code changes — only `AWSClientFactory(use_mocks=False)` |
| `workspace/state/<run_id>.json` (local snapshot file) | **DynamoDB** directly, via `cli.py --target aws` | `StateStore.get_run_metadata`/`get_all_pipelines` |
| `workspace/runs/<run_id>/` (local git checkouts) | **S3** artifacts bucket, prefix per run | `prepare_execution_handler.py` fetches just the entry script via GitHub Contents API, uploads to S3 — no local clone at all |
| `reports/<run_id>/` (local HTML/JSON) | **S3** reports bucket | `generate_report_handler.py` |
| `python -m src.cli run ...` (local, blocking) | **API Gateway** → `start_run` Lambda → `states:StartExecution` | `src/aws_lambda/start_run_handler.py`, `infra/terraform/environments/prod/api_gateway.tf` |
| GitHub (branches, PRs) | **GitHub** (unchanged) | No AWS equivalent |
| `.env` (`GITHUB_TOKEN`, `OPENAI_API_KEY`) | **Secrets Manager** | `common.py`'s `get_github_token()`/`get_openai_api_key()`, cached per warm container |
| Not yet used locally | **CloudWatch + SNS** | Planned Phase 14.6, not built |
| `dashboard/app.py` (local-mode, snapshot files) | `dashboard/app.py` AWS-mode data path (same file, `UPGRADE_AGENT_MODE=aws`) | Run locally against real AWS - not hosted, see Phase 14.7 |

## Execution model (as built — no dispatch/resume hand-off needed)

Unlike the original Fargate-based draft of this plan, Step Functions
persists state between steps natively, so there is no custom "resume from
DynamoDB" logic:

1. `start_run` Lambda validates the manifest, calls `state_store.init_run()`
   (so the run has a queryable DynamoDB record from the start), and calls
   `states:StartExecution` with the initial state as input.
2. `CreateBranches` → `MockBuild` (Lambda Tasks).
3. `ExecuteBoth` (`Parallel` state): baseline and target EMR jobs run
   concurrently via `emr-serverless:startJobRun.sync`, which blocks the
   state machine natively until each job reaches a terminal state.
4. `AnalyzeLogs` (Lambda) diagnoses a target failure via pattern matching
   first, LLM fallback second (see `src/orchestrator/nodes/analyze_node.py`
   — unchanged). An auto-fixable diagnosis commits a config change to the
   target branch and loops back to `CheckRetry` → re-runs *only* the
   target job (`RunTargetEMROnly`), up to `manifest.log_analysis.retry.max_retries`.
5. On target success: `RunValidateEMR` (a third EMR job, running
   `infra/spark_drivers/validate_job.py`, which imports
   `src.tools.data_validator.DataValidator` unchanged) → `ReadValidationResults`
   Lambda (the native `.sync` task only reports job success/failure, not
   `results.json` content, so a small Lambda reads it from S3).
6. `GenerateReport` → `RaisePR` (Lambda Tasks) → execution ends.

## IAM roles (as built)

Every Lambda function gets its own individually-scoped role rather than one
shared broad role (e.g. `prepare_execution` has zero DynamoDB access,
confirmed by reading its code before granting anything) — see
`infra/terraform/environments/prod/lambda.tf`:

- **Per-Lambda roles**: CloudWatch Logs (all), Secrets Manager read (only
  the functions that need GitHub/OpenAI credentials), DynamoDB read/write
  on `upgrade-test-runs` (only the functions that call `StateStore`).
- **`start_run` role**: DynamoDB `PutItem`, `states:StartExecution` scoped
  to the one state machine ARN.
- **`emr_serverless_execution` role**: S3 read/write on the artifacts and
  reports buckets, CloudWatch Logs only.
- **`step_functions_execution` role**: Lambda `InvokeFunction` on the 7
  function ARNs, `emr-serverless:StartJobRun`/`GetJobRun`/`CancelJobRun`/
  `TagResource` scoped to the two EMR applications, `iam:PassRole` scoped
  to the EMR execution role, EventBridge managed-rule permissions
  (required by the native `.sync` integration).
- **GitHub Actions OIDC role** (Phase 14.0, not yet consumed by CI/CD):
  trusted by `repo:vivekaradshan/upgrade-regression-test-agent:*`, no
  long-lived AWS keys stored in GitHub.

## Networking

No VPC required. EMR Serverless, DynamoDB, S3, and Secrets Manager are all
reachable via AWS's public endpoints; Lambda functions run outside a VPC
(no need for one, since nothing here requires private network access) and
reach the GitHub/OpenAI APIs over the public internet.

## Cost estimate (per run)

3 Spark jobs per run (baseline + target + validation), all short EMR
Serverless jobs:

| Resource | Estimate |
|---|---|
| EMR Serverless (3 short jobs, ~2–5 min each) | ~$0.10–0.20 |
| Lambda (7 functions, short invocations) | ~$0.001 |
| Step Functions (1 execution, ~10–15 state transitions incl. retries) | ~$0.001 |
| DynamoDB (on-demand, low volume) | free tier |
| S3 (small artifacts, short retention) | ~$0.01 |
| API Gateway (1 request per run) | free tier |
| **Total** | **~$0.12–0.22 per run** |

A flat **$5/month** AWS Budget guardrail (`aws_budgets_budget.monthly_cost_guardrail`
in `main.tf`) alerts at 80%/100%/forecasted regardless of per-run cost —
already built, ahead of Phase 14.6.

## What doesn't change

- `src/config/manifest.py` — schema unchanged.
- `src/tools/github_client.py` — GitHub remains the only non-AWS
  dependency, unchanged.
- `src/analysis/*.py` — pattern matching, LLM fallback, corrective action
  logic are pure functions, run identically inside a Lambda handler.
- `src/tools/data_validator.py` — comparison logic unchanged; only how
  it's invoked changes (EMR job instead of in-process SparkSession).
- `src/tools/state_store.py` — zero code changes, only the boto3 client
  swaps from moto-mocked to real.
- `src/reporting/report_generator.py` — same HTML/JSON generation; only
  the write target changes (S3 instead of local disk).

## Phases

### ✅ Phase 14.0 — Terraform bootstrap
S3 bucket + DynamoDB lock table for Terraform remote state, GitHub Actions
OIDC identity provider + deploy role. `infra/terraform/bootstrap/`.

### ✅ Phase 14.1 — Data and secrets layer
DynamoDB table `upgrade-test-runs`, S3 buckets (artifacts, reports),
Secrets Manager entries for `GITHUB_TOKEN`/`OPENAI_API_KEY`, the $5/month
budget guardrail.

### ✅ Phase 14.2 — EMR Serverless
Two EMR Serverless applications (baseline on `emr-7.13.0`, target on
`emr-spark-8.0.0`) + execution role. Verified via a real smoke-test job
run: baseline succeeded and produced real Parquet, target failed with a
genuine `SparkArithmeticException`/`DIVIDE_BY_ZERO` on real Spark 4.0.2.

### ✅ Phase 14.3 — Lambda handlers
`src/aws_lambda/` package: one thin handler per node, a shared Lambda
Layer (all `src/` code, built via `infra/scripts/build_lambda_layer.sh`
using `pip --platform manylinux2014_x86_64` to cross-package genuine Linux
wheels from a Mac, no Docker needed).

### ✅ Phase 14.4 — Step Functions state machine
`infra/terraform/environments/prod/state_machine.json.tpl`/`.tf`. Verified
against real AWS through 4 iterations, each fixing a bug found by an
actual failed execution (not just `terraform apply` succeeding) — see the
file's inline comments for details (EMR Serverless rejecting
`spark.master`, EMR's `SUCCESS`/`State`/`JobRunId` output shape vs. the
local mock's `SUCCEEDED` vocabulary, and the `.sync` Catch block silently
discarding `jobRunId` on a genuine Spark job failure). The 4th execution
completed the full path for real: baseline succeeds, target fails with a
real ANSI division-by-zero exception, pattern matcher diagnoses it,
auto-fix commits to the target branch, retry succeeds, validation passes
on real EMR, report lands in S3, PR opens.

### ✅ Phase 14.5 — API Gateway trigger
`POST /runs` (HTTP API, IAM/SigV4 auth) → `start_run` Lambda →
`states:StartExecution`. `cli.py` gets `--target {local,aws}` on `run`,
`status`, and `cleanup`. Verified against real AWS: a signed CLI request
started a real execution end-to-end successfully.

### ✅ Phase 14.6 — Observability and cost guardrails
CloudWatch Log Groups for all 8 Lambda functions and Step Functions
execution logging (`level=ALL`, `includeExecutionData`) - full
state-transition detail in CloudWatch Logs Insights instead of only the
bounded execution-history API. An SNS topic + email subscription and a
CloudWatch Alarm on the `AWS/States` `ExecutionsFailed` metric notify on
any failed execution. Found via a real failed apply: enabling Step
Functions logging needs a CloudWatch Logs *resource policy* granting
`states.amazonaws.com` write access to the log group, separate from (and
in addition to) the IAM role's own `logs:CreateLogDelivery` permissions.
Verified against real AWS: `logging_configuration` confirmed `level=ALL`
with the log group correctly attached, alarm correctly wired to the SNS
topic. (The flat $5/month budget alarm was already done ahead of
schedule - this phase is about per-run/failure-rate observability, not
the billing cap itself.)

### ✅ Phase 14.7 — Dashboard: AWS-mode data path + trigger (local-only, not hosted)
**Architecture change from the original plan**: App Runner turned out to
be closed to new customers (AWS stopped accepting new App Runner
customers as of April 30, 2026); its AWS-recommended replacement, ECS
Express Mode, was verified (via live AWS docs, not assumed) to always
provision a mandatory, non-scale-to-zero Application Load Balancer
(~$16-20/month) — with only one service to run, there's no other service
to share that ALB cost across, so it alone exceeds the $5/month budget.
Every "hosted with a public URL" option (App Runner, ECS Express Mode,
plain Fargate+ALB) was ruled out on this basis; decided to keep the
dashboard local-only rather than raise the budget for hosting.

What was built instead: `dashboard/app.py` gets a real `UPGRADE_AGENT_MODE`
env var (default `local`, unchanged behavior). In `aws` mode:
`load_aws_snapshots()` builds the same `{run_id: {metadata, pipelines}}`
shape `render()` already expects, from `StateStore.list_runs()` (new -
a `Scan` filtered to `_metadata` records, since there's no local snapshot
directory to enumerate run_ids from) and `get_all_pipelines()` against
real DynamoDB. A new trigger form POSTs the bundled manifest to API
Gateway via a SigV4-signed request - `cli.py`'s signing logic was
extracted into `src/tools/signed_http.py` so both the CLI and dashboard
reuse the identical signing path. Run locally with
`UPGRADE_AGENT_MODE=aws streamlit run dashboard/app.py` against real AWS
data - zero additional hosting cost.

Verified against real AWS: the app starts and renders without error
against real `Settings()`/`StateStore` wiring; `list_runs()` confirmed
returning real run records from DynamoDB, sorted newest-first.

### ⬜ Phase 14.8 — CI/CD (scoped down: plan-on-PR only, no auto-apply)
**Revised from the original plan.** CI/CD's usual justification is gating
promotion between environments (a bad change can't reach prod directly);
that doesn't apply here since this project is deliberately single-
environment (no dev/staging to promote through) - so a full
`terraform-apply.yml` auto-deploying on every merge to `main` would add
process friction (every infra tweak needs a PR+merge instead of a direct
`terraform apply`) without the usual risk-mitigation payoff, for a
solo-operated project.

What's still worth having: infra changes should still go through a PR to
`main` for review, not be pushed directly - so
`.github/workflows/terraform-plan.yml` (runs on PRs touching `infra/`,
posts the plan diff as a PR comment) is in scope. `terraform apply`
itself stays manual, run locally the same way every change in Phases
14.0-14.7 was - reviewed via the PR's plan comment first, then applied
by hand after merge. The OIDC deploy role from Phase 14.0 remains built
but unused for now; it's what a future `terraform-apply.yml` would use if
this project ever did grow a second environment worth gating.

### ⬜ Phase 14.9 — End-to-end validation
Trigger one real run via `--target aws` against the real manifest.
Confirm: GitHub branches created, EMR jobs ran and produced correct
Parquet output, the ANSI-mode failure was detected and auto-fixed exactly
as it is locally, DynamoDB has correct state, the report landed in S3,
the local AWS-mode dashboard shows it live, a PR was opened, and actual
AWS cost matches the ~$0.12–0.22/run estimate. Clean up test artifacts
(branches, PR) same as the local e2e test does.

## Verification approach throughout

Every phase is verified against real AWS state before being considered
done — not just `terraform plan`/`apply` succeeding. Phase 14.4 in
particular required 4 real Step Functions executions, each surfacing a
genuine bug that unit tests (mocked) couldn't have caught, since they
depend on the exact shape of real AWS service responses.
