# --- Phase 14.6: Observability and cost guardrails ---
#
# The $5/month budget guardrail (main.tf) already covers total spend; this
# phase is about *why* something failed and *knowing* when it did, not the
# billing cap itself.

# Explicit log groups for every Lambda function, rather than letting Lambda
# auto-create them on first invocation - an auto-created log group has no
# expiration (retains forever) unless something else manages it. 14-day
# retention matches the artifacts bucket's own lifecycle policy (main.tf)
# - long enough to debug a recent run, short enough not to accumulate cost
# indefinitely on a project with no ongoing production traffic.
locals {
  lambda_function_names = [
    aws_lambda_function.create_branches.function_name,
    aws_lambda_function.mock_build.function_name,
    aws_lambda_function.prepare_execution.function_name,
    aws_lambda_function.analyze_logs.function_name,
    aws_lambda_function.generate_report.function_name,
    aws_lambda_function.raise_pr.function_name,
    aws_lambda_function.read_validation_results.function_name,
    aws_lambda_function.start_run.function_name,
  ]
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each          = toset(local.lambda_function_names)
  name              = "/aws/lambda/${each.value}"
  retention_in_days = 14
}

# Step Functions execution logging - off by default. ALL log level gives
# full state-transition detail (state entered/exited, input/output at each
# step), which is exactly what made debugging Phase 14.4's real-execution
# failures possible via `aws stepfunctions get-execution-history` - this
# just makes the same detail queryable in CloudWatch Logs Insights too,
# and gives every execution a permanent record instead of one bounded by
# the Step Functions execution-history retention window.
resource "aws_cloudwatch_log_group" "state_machine" {
  name              = "/aws/vendedlogs/states/${var.project_name}-orchestrator"
  retention_in_days = 14
}

# The step_functions_execution role's IAM permissions alone aren't enough -
# CloudWatch Logs separately requires a *resource policy* on the log group
# itself granting the states.amazonaws.com service principal permission to
# deliver logs to it (confirmed by a real failed apply:
# AccessDeniedException "state machine IAM Role is not authorized to access
# the Log Destination" even with the IAM role policy already in place).
data "aws_iam_policy_document" "state_machine_log_delivery" {
  statement {
    sid    = "AWSLogDeliveryWrite"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }
    actions   = ["logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.state_machine.arn}:*"]
  }
}

resource "aws_cloudwatch_log_resource_policy" "state_machine_log_delivery" {
  policy_name     = "${var.project_name}-state-machine-log-delivery"
  policy_document = data.aws_iam_policy_document.state_machine_log_delivery.json
}

data "aws_iam_policy_document" "step_functions_logging_permissions" {
  statement {
    sid    = "StepFunctionsLogging"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
      "logs:DescribeLogGroups",
    ]
    # Log delivery for Step Functions vended logs isn't resource-scopable
    # to one log group - this is the AWS-documented permission set for
    # sfn `logging_configuration`, deliberately broad only for these
    # specific logs-delivery actions (not general CloudWatch Logs access).
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "step_functions_logging" {
  name   = "${var.project_name}-step-functions-logging-permissions"
  role   = aws_iam_role.step_functions_execution.id
  policy = data.aws_iam_policy_document.step_functions_logging_permissions.json
}

# SNS topic for run-failure notifications - separate from the Budgets
# guardrail (which emails subscribers directly, no topic needed) since
# CloudWatch Alarms require an SNS target.
resource "aws_sns_topic" "run_alerts" {
  name = "${var.project_name}-run-alerts"
}

resource "aws_sns_topic_subscription" "run_alerts_email" {
  topic_arn = aws_sns_topic.run_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

# Fires when a Step Functions execution fails - covers escalated runs
# (max retries exhausted, non-auto-fixable diagnosis) and genuine
# infrastructure failures (e.g. Phase 14.4's spark.master rejection) alike,
# since both end the execution in a FAILED state. threshold=1 / one
# 5-minute period: a single failed run is worth an email, this system has
# no ongoing high-frequency traffic where that would be noisy.
resource "aws_cloudwatch_metric_alarm" "state_machine_failures" {
  alarm_name          = "${var.project_name}-orchestrator-execution-failures"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  evaluation_periods   = 1
  metric_name         = "ExecutionsFailed"
  namespace           = "AWS/States"
  period              = 300
  statistic           = "Sum"
  threshold           = 1
  treat_missing_data  = "notBreaching"

  dimensions = {
    StateMachineArn = aws_sfn_state_machine.orchestrator.arn
  }

  alarm_description = "An upgrade-agent Step Functions execution failed - see the run's report/PR or CloudWatch Logs Insights on ${aws_cloudwatch_log_group.state_machine.name} for details."
  alarm_actions      = [aws_sns_topic.run_alerts.arn]
}
