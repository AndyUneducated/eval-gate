#!/usr/bin/env bash
# Push a fresh image and roll the ECS service. With RUN_MIGRATIONS=true (the
# default in Terraform) the new task applies `alembic upgrade head` before it
# starts serving, so a plain deploy also migrates the schema.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tf_dir="$repo_root/deploy/terraform"

"$repo_root/deploy/scripts/build_and_push.sh"

cluster="$(terraform -chdir="$tf_dir" output -raw ecs_cluster)"
service="$(terraform -chdir="$tf_dir" output -raw ecs_service)"
repo_url="$(terraform -chdir="$tf_dir" output -raw ecr_repository_url)"
region="$(echo "${repo_url%%/*}" | cut -d. -f4)"

echo "[deploy] forcing new deployment of $service"
aws ecs update-service --cluster "$cluster" --service "$service" \
  --force-new-deployment --region "$region" >/dev/null

echo "[deploy] waiting for the service to stabilize"
aws ecs wait services-stable --cluster "$cluster" --services "$service" --region "$region"

echo "[deploy] live at $(terraform -chdir="$tf_dir" output -raw alb_url)"
