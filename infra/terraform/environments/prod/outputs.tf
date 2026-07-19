output "budget_name" {
  value = aws_budgets_budget.monthly_cost_guardrail.name
}

output "dynamodb_table_name" {
  value = aws_dynamodb_table.runs.name
}

output "dynamodb_table_arn" {
  value = aws_dynamodb_table.runs.arn
}

output "artifacts_bucket" {
  value = aws_s3_bucket.artifacts.bucket
}

output "reports_bucket" {
  value = aws_s3_bucket.reports.bucket
}

output "github_token_secret_arn" {
  value = aws_secretsmanager_secret.github_token.arn
}

output "openai_api_key_secret_arn" {
  value = aws_secretsmanager_secret.openai_api_key.arn
}

output "emr_execution_role_arn" {
  value = aws_iam_role.emr_serverless_execution.arn
}

output "emr_baseline_application_id" {
  value = aws_emrserverless_application.baseline.id
}

output "emr_target_application_id" {
  value = aws_emrserverless_application.target.id
}

output "pyyaml_pyfiles_s3_uri" {
  value = "s3://${aws_s3_bucket.artifacts.id}/${aws_s3_object.pyyaml_pyfiles.key}"
}

output "lambda_create_branches_arn" {
  value = aws_lambda_function.create_branches.arn
}

output "lambda_mock_build_arn" {
  value = aws_lambda_function.mock_build.arn
}

output "lambda_prepare_execution_arn" {
  value = aws_lambda_function.prepare_execution.arn
}

output "lambda_analyze_logs_arn" {
  value = aws_lambda_function.analyze_logs.arn
}

output "lambda_generate_report_arn" {
  value = aws_lambda_function.generate_report.arn
}

output "lambda_raise_pr_arn" {
  value = aws_lambda_function.raise_pr.arn
}

output "lambda_read_validation_results_arn" {
  value = aws_lambda_function.read_validation_results.arn
}

output "lambda_await_approval_arn" {
  value = aws_lambda_function.await_approval.arn
}

output "lambda_approve_run_arn" {
  value = aws_lambda_function.approve_run.arn
}

output "state_machine_arn" {
  value = aws_sfn_state_machine.orchestrator.arn
}

# Base endpoint (no path) - the CLI's UPGRADE_AGENT_API_ENDPOINT expects
# this and appends /runs itself (see cli.py's cmd_run).
output "api_endpoint" {
  value = aws_apigatewayv2_api.runs.api_endpoint
}
