output "tfstate_bucket" {
  description = "S3 bucket holding Terraform remote state for all other modules"
  value       = aws_s3_bucket.tfstate.bucket
}

output "tfstate_lock_table" {
  description = "DynamoDB table used for Terraform state locking"
  value       = aws_dynamodb_table.tfstate_lock.name
}

output "github_actions_role_arn" {
  description = "IAM role ARN GitHub Actions assumes via OIDC to deploy infra"
  value       = aws_iam_role.github_actions_deploy.arn
}

output "aws_account_id" {
  value = data.aws_caller_identity.current.account_id
}
