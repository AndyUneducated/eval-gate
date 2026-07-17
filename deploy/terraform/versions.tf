terraform {
  required_version = ">= 1.6"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.60"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }

  # Remote state is recommended for real use. Configure with `-backend-config`
  # at `terraform init` time, e.g. an S3 bucket + DynamoDB lock table:
  #   backend "s3" {}
}
