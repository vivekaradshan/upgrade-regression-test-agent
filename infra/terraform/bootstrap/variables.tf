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

variable "github_repo" {
  description = "owner/repo for the GitHub Actions OIDC trust condition"
  type        = string
  default     = "vivekaradshan/upgrade-regression-test-agent"
}
