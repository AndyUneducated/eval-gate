output "alb_url" {
  description = "Base URL of the API behind the ALB (try GET /healthz)."
  value       = "http://${aws_lb.main.dns_name}"
}

output "ecr_repository_url" {
  description = "ECR repo to push the API image to."
  value       = aws_ecr_repository.api.repository_url
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}

output "ecs_service" {
  value = aws_ecs_service.api.name
}

output "task_definition_family" {
  value = aws_ecs_task_definition.api.family
}

output "app_security_group_id" {
  description = "Security group for one-off tasks (e.g. the migrate task)."
  value       = aws_security_group.app.id
}

output "public_subnet_ids" {
  value = aws_subnet.public[*].id
}

output "rds_endpoint" {
  value = aws_db_instance.main.address
}

output "github_deploy_role_arn" {
  description = "Role ARN for the GitHub Actions deploy workflow (OIDC)."
  value       = var.create_github_oidc ? aws_iam_role.github_deploy[0].arn : null
}
