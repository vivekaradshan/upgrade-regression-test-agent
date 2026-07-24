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

variable "github_token" {
  description = "GitHub PAT the orchestrator uses for branch/PR operations - passed via TF_VAR_github_token, never written to a file"
  type        = string
  sensitive   = true
}

variable "openai_api_key" {
  description = "OpenAI API key for the LLM log-diagnosis fallback - passed via TF_VAR_openai_api_key, never written to a file"
  type        = string
  sensitive   = true
}

variable "tavily_api_key" {
  description = "Tavily API key for the ReAct loop's search_web tool (Phase 15.2) - passed via TF_VAR_tavily_api_key, never written to a file"
  type        = string
  sensitive   = true
}

variable "langsmith_api_key" {
  description = "LangSmith API key for LLM call tracing (Phase 15.0) - passed via TF_VAR_langsmith_api_key, never written to a file"
  type        = string
  sensitive   = true
}
