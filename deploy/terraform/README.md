# EvalGate — AWS deploy (ECS Fargate + RDS)

Terraform stack that provisions a managed home for the EvalGate API:

```
Internet → ALB(:80, /healthz) → ECS Fargate service (api :8000) → RDS Postgres 16
                                        ↑ image from ECR   ↑ DATABASE_URL from Secrets Manager
```

See [../../docs/PHASE_18_PLAN.md](../../docs/PHASE_18_PLAN.md) and ADR-017 in
[../../DECISIONS.md](../../DECISIONS.md) for the "why".

## Layout

| File | What it creates |
| --- | --- |
| `versions.tf` | Terraform + provider version pins (aws, random) |
| `variables.tf` | All inputs (region, sizing, keys, OIDC toggle, …) |
| `main.tf` | AWS provider + `local.name` / AZ / image locals |
| `network.tf` | VPC, IGW, 2× public subnet, route table, 3 security groups |
| `ecr.tf` | ECR repo + "keep last 10 images" lifecycle policy |
| `rds.tf` | RDS Postgres 16 (gp3, encrypted) + subnet group + random password |
| `secrets.tf` | Secrets Manager: assembled `DATABASE_URL` (+ optional provider keys) |
| `iam.tf` | ECS execution role (pull/logs/secrets) + task role |
| `alb.tf` | ALB, target group (`/healthz`), `:80` listener |
| `ecs.tf` | Cluster, log group, task definition, Fargate service |
| `oidc.tf` | (optional) GitHub OIDC provider + narrow deploy role |
| `outputs.tf` | ALB URL, ECR URL, cluster/service names, subnets, role ARN |

## Prerequisites

- Terraform >= 1.6, AWS CLI, Docker.
- AWS credentials with permission to create the above (an admin/bootstrap role).
- **Cost note:** applying this creates billable resources (RDS, ALB, Fargate,
  NAT is intentionally avoided). Run `terraform destroy` when done.

## Quick start

```bash
cp terraform.tfvars.example terraform.tfvars   # edit region/sizing/keys as needed

terraform init
terraform apply

# Build + push the image and roll the service (from the repo root):
cd ../.. && make deploy

# Smoke test:
curl "$(terraform -chdir=deploy/terraform output -raw alb_url)/healthz"
```

Migrations run automatically on task start (`RUN_MIGRATIONS=true`, safe at
`desired_count = 1`). For `desired_count > 1`, disable that and run the one-off
task instead:

```bash
terraform apply -var run_migrations_on_start=false
make deploy && make deploy-migrate
```

## GitHub Actions (OIDC) deploy

1. `terraform apply -var create_github_oidc=true -var github_repository=owner/repo`
2. Set these repo-level Actions **variables** (all are Terraform outputs):
   `AWS_REGION`, `AWS_DEPLOY_ROLE_ARN` (`github_deploy_role_arn`),
   `ECR_REPOSITORY` (repo name), `ECS_CLUSTER`, `ECS_SERVICE`.
3. Run the **deploy** workflow (`workflow_dispatch`).

## Production hardening (out of scope for this demo)

- **Private subnets + NAT gateway / VPC endpoints** for the tasks and RDS
  (this stack uses SG-locked public subnets to skip NAT cost).
- **HTTPS**: add an ACM cert + `:443` listener + redirect `:80→:443`.
- **Remote state**: configure the `backend "s3"` block (S3 + DynamoDB lock).
- **RDS**: `multi_az = true`, longer `backup_retention_period`,
  `deletion_protection = true`, a real `final_snapshot`.
- **Autoscaling**: `desired_count > 1` + an Application Auto Scaling target.
