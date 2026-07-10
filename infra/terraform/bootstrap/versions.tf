terraform {
  required_version = ">= 1.13"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }

  # Intentionally local state for the bootstrap module itself - it creates
  # the S3 bucket + DynamoDB table that every other module's remote state
  # backend depends on, so it can't depend on that backend existing yet.
  # This is the standard chicken-and-egg resolution for Terraform
  # bootstrapping. Keep infra/terraform/bootstrap/terraform.tfstate out of
  # git (see .gitignore) and treat it carefully - it's the one piece of
  # state not protected by remote locking.
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project   = var.project_name
      ManagedBy = "terraform"
      Module    = "bootstrap"
    }
  }
}
