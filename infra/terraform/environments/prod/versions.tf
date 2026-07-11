terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }

  # Bucket/table names are the actual literal values created by
  # infra/terraform/bootstrap (Terraform's backend block can't reference
  # variables or other resources - only literal values).
  backend "s3" {
    bucket         = "upgrade-agent-tfstate-068378433969"
    key            = "environments/prod/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "upgrade-agent-tfstate-lock"
    encrypt        = true
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = var.project_name
      ManagedBy   = "terraform"
      Environment = "prod"
    }
  }
}
