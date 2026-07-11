data "aws_caller_identity" "current" {}

# Cost guardrail, set up ahead of the rest of the phased build (Phase
# 14.6 in the plan) since it's cheap, independent of every other resource,
# and worth having in place before any billable infra exists rather than
# after. Sends an email alert (no SNS topic/subscription-confirmation
# needed - AWS Budgets emails its subscriber list directly) at 80% and
# 100% of actual monthly spend, plus a forecasted-to-exceed warning.
resource "aws_budgets_budget" "monthly_cost_guardrail" {
  name         = "${var.project_name}-monthly-budget"
  budget_type  = "COST"
  limit_amount = var.monthly_budget_limit_usd
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 80
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "ACTUAL"
    subscriber_email_addresses = [var.alert_email]
  }

  notification {
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    notification_type          = "FORECASTED"
    subscriber_email_addresses = [var.alert_email]
  }
}

# --- Phase 14.1: data and secrets layer ---

# Name and key schema match src/tools/state_store.py's TABLE_NAME constant
# and record design exactly (run_id partition key, record_type sort key -
# "_metadata" for run-level state, a pipeline_id for pipeline state,
# "event#<timestamp>#<uuid>" for the append-only audit log) - StateStore's
# code needs zero changes to work against this table, only the
# AWSClientFactory(use_mocks=False) toggle changes at call time.
resource "aws_dynamodb_table" "runs" {
  name         = "upgrade-test-runs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "run_id"
  range_key    = "record_type"

  attribute {
    name = "run_id"
    type = "S"
  }

  attribute {
    name = "record_type"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }
}

# Per-run local checkouts, generated input data, and Spark job logs
# (locally: workspace/runs/<run_id>/ - see execute_node.py). Short
# lifecycle expiration since these are transient working artifacts, not
# permanent records - the DynamoDB table + reports bucket are the durable
# record of what happened.
resource "aws_s3_bucket" "artifacts" {
  bucket = "${var.project_name}-artifacts-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-after-14-days"
    status = "Enabled"
    filter {}
    expiration {
      days = 14
    }
  }
}

# HTML/JSON reports (locally: reports/<run_id>/ - see report_generator.py).
# Kept private, not public-read - the PR body links via a presigned URL
# generated at report-creation time rather than making the bucket public.
resource "aws_s3_bucket" "reports" {
  bucket = "${var.project_name}-reports-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "reports" {
  bucket                  = aws_s3_bucket.reports.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "reports" {
  bucket = aws_s3_bucket.reports.id
  rule {
    id     = "expire-after-90-days"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
}

# Secret values come in via TF_VAR_github_token / TF_VAR_openai_api_key
# environment variables at apply time (never written to any .tf or
# .tfvars file). Note: Terraform state itself will contain the plaintext
# secret value, same as it does for any resource attribute - the state
# bucket is private, encrypted, and access-scoped via IAM (see bootstrap),
# which is the standard, accepted tradeoff for managing Secrets Manager
# values through Terraform.
resource "aws_secretsmanager_secret" "github_token" {
  name = "${var.project_name}/github-token"
}

resource "aws_secretsmanager_secret_version" "github_token" {
  secret_id     = aws_secretsmanager_secret.github_token.id
  secret_string = var.github_token
}

resource "aws_secretsmanager_secret" "openai_api_key" {
  name = "${var.project_name}/openai-api-key"
}

resource "aws_secretsmanager_secret_version" "openai_api_key" {
  secret_id     = aws_secretsmanager_secret.openai_api_key.id
  secret_string = var.openai_api_key
}
