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
