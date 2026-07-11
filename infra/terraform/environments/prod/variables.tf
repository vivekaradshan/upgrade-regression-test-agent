variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Short name prefix for all resources in this project"
  type        = string
  default     = "upgrade-agent"
}

variable "alert_email" {
  description = "Email address for budget/failure notifications"
  type        = string
  default     = "vivekaradshan@gmail.com"
}

variable "monthly_budget_limit_usd" {
  description = "Monthly AWS cost budget limit in USD"
  type        = string
  default     = "5"
}
