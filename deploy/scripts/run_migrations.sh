#!/usr/bin/env bash
# Run schema migrations as a one-off Fargate task (the `migrate` entrypoint
# command). This is the multi-task-safe alternative to RUN_MIGRATIONS on the
# service: use it when desired_count > 1 so migrations don't race N tasks.
# Task logs stream to the same CloudWatch group as the service.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tf_dir="$repo_root/deploy/terraform"

cluster="$(terraform -chdir="$tf_dir" output -raw ecs_cluster)"
family="$(terraform -chdir="$tf_dir" output -raw task_definition_family)"
sg="$(terraform -chdir="$tf_dir" output -raw app_security_group_id)"
repo_url="$(terraform -chdir="$tf_dir" output -raw ecr_repository_url)"
region="$(echo "${repo_url%%/*}" | cut -d. -f4)"
subnets="$(terraform -chdir="$tf_dir" output -json public_subnet_ids | tr -d '[]" \n')"

network="awsvpcConfiguration={subnets=[$subnets],securityGroups=[$sg],assignPublicIp=ENABLED}"
overrides='{"containerOverrides":[{"name":"api","command":["migrate"]}]}'

echo "[migrate] launching one-off migrate task on $cluster"
aws ecs run-task \
  --cluster "$cluster" \
  --launch-type FARGATE \
  --task-definition "$family" \
  --network-configuration "$network" \
  --overrides "$overrides" \
  --region "$region"
echo "[migrate] task launched — follow logs in CloudWatch group /ecs/$family"
