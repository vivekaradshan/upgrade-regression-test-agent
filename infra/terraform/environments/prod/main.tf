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
