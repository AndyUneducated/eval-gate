variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "project" {
  description = "Name prefix for all resources."
  type        = string
  default     = "evalgate"
}

variable "environment" {
  description = "Deployment environment tag (prod / staging / demo)."
  type        = string
  default     = "demo"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
  default     = "10.42.0.0/16"
}

variable "az_count" {
  description = "Number of AZs / public subnets to spread across (>= 2 for the ALB)."
  type        = number
  default     = 2
}

variable "alb_ingress_cidrs" {
  description = "CIDRs allowed to reach the ALB on :80. Lock this down for anything real."
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "container_image" {
  description = "Full image ref for the API container. Empty -> the created ECR repo's :latest tag."
  type        = string
  default     = ""
}

variable "api_cpu" {
  description = "Fargate task CPU units (1024 = 1 vCPU)."
  type        = number
  default     = 512
}

variable "api_memory" {
  description = "Fargate task memory (MiB)."
  type        = number
  default     = 1024
}

variable "desired_count" {
  description = "Number of API tasks to run."
  type        = number
  default     = 1
}

variable "run_migrations_on_start" {
  description = "Run `alembic upgrade head` before serving. Safe at desired_count = 1."
  type        = bool
  default     = true
}

variable "db_instance_class" {
  description = "RDS instance class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_allocated_storage" {
  description = "RDS storage in GiB."
  type        = number
  default     = 20
}

variable "db_name" {
  description = "Initial Postgres database name."
  type        = string
  default     = "evalgate"
}

variable "db_username" {
  description = "Postgres master username."
  type        = string
  default     = "evalgate"
}

variable "openai_api_key" {
  description = "Optional judge provider key; stored in Secrets Manager and injected as env."
  type        = string
  default     = ""
  sensitive   = true
}

variable "anthropic_api_key" {
  description = "Optional judge provider key; stored in Secrets Manager and injected as env."
  type        = string
  default     = ""
  sensitive   = true
}

variable "log_retention_days" {
  description = "CloudWatch log retention for the API container."
  type        = number
  default     = 14
}

variable "create_github_oidc" {
  description = "Create the GitHub OIDC provider + deploy role. One OIDC provider per account — set false if it already exists."
  type        = bool
  default     = false
}

variable "github_repository" {
  description = "owner/repo allowed to assume the deploy role via OIDC (required when create_github_oidc = true)."
  type        = string
  default     = ""
}
