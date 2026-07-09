# AWS Deployment Plan (Step 14)

## Status

Not started. Steps 1-13 are complete and entirely local (see the main
[README](../README.md)); this document plans the eventual swap from local
mocks to real AWS infrastructure. Nothing described here has been built yet.

## Scope of this document

Architecture and resource mapping only — no Terraform/CDK/CloudFormation code
yet. Target: a personal AWS sandbox account, single region (`us-east-1`
assumed; nothing here is region-specific). This is the design to implement
*when* Step 14 starts, not a description of current state.

## Guiding principle

Every local component in `src/mock_infra/` was written specifically so that
swapping it for a real AWS client is a substitution, not a rewrite. The
orchestrator nodes (`src/orchestrator/nodes/*.py`) call an interface
(`LocalSparkRunner.run_spark_job`, `MockStepFunctions.start_execution`,
`AWSClientFactory.get_*_client`) — as long as the real AWS adapters honor the
same method signatures, `src/orchestrator/graph.py` and every node built in
Steps 8-11 should need zero changes.

## Component mapping

| Local (Steps 1-13) | AWS resource | Purpose |
|---|---|---|
| `src/mock_infra/mock_emr.py` (`LocalSparkRunner`, subprocess) | **EMR Serverless** application | Runs the actual PySpark job (baseline, target, and validation - see below) |
| `src/mock_infra/mock_step_functions.py` (threading) | **Step Functions** state machine | Orchestrates job dispatch and completion signaling |
| `src/mock_infra/mock_event_bridge.py` (in-process callback) | **EventBridge** rule + target | Fires when a Step Functions execution completes, triggers the next orchestrator step |
| `src/tools/state_store.py` (moto-mocked) | **DynamoDB** table `upgrade-test-runs` | Same schema as today (`run_id` partition key, `record_type` sort key) - no schema change needed, only the client swaps |
| `workspace/state/<run_id>.json` (local file, Step 12) | **DynamoDB** directly, or a small **S3**-backed snapshot | The dashboard reads live state directly from DynamoDB instead of a local file once both dashboard and orchestrator can reach the same real table |
| `workspace/runs/<run_id>/` (local git checkouts) | **S3** bucket, prefix per run | EMR Serverless jobs read code/data from S3, not a local filesystem - the local-clone step in `execute_node.py` is replaced by "clone locally, sync to S3" or a build step that packages the branch and uploads it |
| `reports/<run_id>/` (local HTML/JSON) | **S3** bucket (public-read prefix or presigned URLs) | Report hosting; the PR body links to the S3 URL instead of a local path |
| Python process running `run_upgrade_test()` (Steps 8-11) | **Fargate** ECS task | The orchestrator itself - triggered instead of run via CLI, dispatches to Step Functions and exits, resumes via EventBridge (see "Execution model change" below) |
| `python -m src.cli run ...` (Step 13, planned) | **API Gateway** → Lambda or direct Fargate task launch | External trigger replacing manual CLI invocation |
| GitHub (branches, PRs) | **GitHub** (unchanged) | No AWS equivalent - stays exactly as built in Step 4 |
| `.env` (`GITHUB_TOKEN`, `OPENAI_API_KEY`) | **Secrets Manager** | Orchestrator reads secrets from Secrets Manager instead of a local `.env` file; `src/config/settings.py` gets an AWS-backed variant |
| Not yet used locally | **SNS** topic | Run-completion notifications (success/failure alert) - mentioned in the original build plan's mock infra scope but never wired to a node; would be added in `report_node.py` or `pr_node.py` at Step 14 time |

## Execution model change (the one real architectural shift)

Locally, `run_upgrade_test()` is a single long-lived Python process that
blocks on `graph.stream(...)`, waiting synchronously for each Spark job to
finish before moving to the next node (see `execute_node.py`'s
`_wait_for_completion` polling loop).

On AWS, this changes to a **dispatch → exit → resume** pattern:

1. Fargate task starts, runs `create_branches` and `mock_build` (fast, no
   waiting), then calls `step_functions.start_execution` for the baseline
   and target EMR jobs and **exits** rather than polling.
2. Step Functions runs the actual EMR Serverless job.
3. On completion, Step Functions emits a **EventBridge** event.
4. An EventBridge rule triggers a **new** Fargate task (or a Lambda that
   resumes the Fargate task), which picks up from `analyze_logs` using the
   run's state already persisted in DynamoDB - not from in-memory Python
   state, since the process that started the run may no longer exist.

This means `execute_node.py` and `analyze_node.py` need a real state-machine
definition (steps, not just Python function calls) that maps to this
dispatch/resume boundary, and the orchestrator needs a way to be re-invoked
"resume from DynamoDB state" rather than always starting fresh at
`create_branches`. This is the main design gap not yet solved locally and
worth prototyping first when Step 14 starts.

## Validation also becomes its own EMR job

Per an earlier design note from this project: validation should not run
in-process inside the orchestrator (as `validate_node.py` currently does,
spinning up a local `SparkSession` directly). On AWS, `validate_data`
dispatches a **third** EMR Serverless job (alongside baseline and target),
passing the run_id and both output S3 paths as job parameters, and waits for
its own Step Functions execution/EventBridge completion signal exactly like
the baseline/target jobs. `src/tools/data_validator.py`'s actual comparison
logic doesn't change - only how it's invoked (dispatched job vs. in-process
call).

## IAM roles needed

- **Fargate task role**: DynamoDB read/write on `upgrade-test-runs`, S3
  read/write on the run-artifacts bucket, Step Functions `StartExecution`,
  Secrets Manager read for GitHub/OpenAI credentials, SNS `Publish`.
- **EMR Serverless execution role**: S3 read (input code/data) + write
  (output Parquet, logs).
- **Step Functions execution role**: `emr-serverless:StartJobRun`,
  `emr-serverless:GetJobRun`, EventBridge `PutEvents`.
- **EventBridge rule role**: permission to invoke the Fargate task
  (via ECS `RunTask`) or a resume Lambda.

## Networking

EMR Serverless doesn't require a VPC unless the job needs private network
access (it doesn't here - S3 and DynamoDB are reachable via AWS's public
endpoints or VPC gateway endpoints). Fargate task can run in a default VPC's
public subnets with a security group allowing outbound HTTPS only (GitHub
API, OpenAI API, AWS APIs) - no inbound rules needed since nothing calls the
Fargate task directly; API Gateway invokes it via the ECS API, not a network
path into the task.

## Cost estimate (per run)

Building on the original plan's estimate, adjusted for what Steps 1-13
actually run (baseline + target + **validation** = 3 Spark jobs per run, not
2):

| Resource | Estimate |
|---|---|
| EMR Serverless (3 short jobs, ~2-5 min each) | ~$0.10-0.20 |
| Fargate (2 short-lived tasks: dispatch + resume) | ~$0.02 |
| Step Functions (3 state machine executions) | ~$0.001 |
| DynamoDB (on-demand, low volume) | free tier |
| S3 (small artifacts, short retention) | ~$0.01 |
| SNS | free tier |
| EventBridge | free tier |
| **Total** | **~$0.15-0.25 per run** |

## What doesn't change

- `src/config/manifest.py` — manifest schema stays identical.
- `src/tools/github_client.py` — GitHub remains the only real dependency
  locally and stays exactly as-is.
- `src/analysis/*.py` — pattern matching, LLM fallback, corrective action
  logic are all pure functions independent of where they run.
- `src/tools/data_validator.py` — the comparison logic itself, only its
  invocation context changes (see above).
- `src/reporting/report_generator.py` — same HTML/JSON generation; only the
  write target changes (S3 instead of local disk).

## Open questions to resolve before implementation starts

1. EMR Serverless vs. EMR on EC2 (Serverless is simpler and matches the
   short/bursty job profile; EC2 gives more control if a real customer
   pipeline needs it later).
2. How "resume from DynamoDB state" actually gets implemented — a resume
   Lambda that re-invokes a fresh Fargate task with `run_id` as input, most
   likely, but the exact hand-off contract isn't designed yet.
3. Terraform vs. CDK vs. CloudFormation for the actual IaC, deferred per
   this document's scope.
