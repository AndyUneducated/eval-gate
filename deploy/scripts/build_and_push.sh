#!/usr/bin/env bash
# Build the API image and push it to the ECR repo Terraform created.
# Tags with both the git short SHA (immutable) and :latest (what the service
# pulls). Region + registry are derived from the ECR repo URL, so the only
# prerequisite is a working `aws`/`docker` login context + applied Terraform.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
tf_dir="$repo_root/deploy/terraform"

repo_url="$(terraform -chdir="$tf_dir" output -raw ecr_repository_url)"
registry="${repo_url%%/*}"
region="$(echo "$registry" | cut -d. -f4)"
tag="${IMAGE_TAG:-$(git -C "$repo_root" rev-parse --short HEAD 2>/dev/null || echo latest)}"

echo "[push] logging in to $registry ($region)"
aws ecr get-login-password --region "$region" | docker login --username AWS --password-stdin "$registry"

echo "[push] building $repo_url:$tag"
docker build -t "$repo_url:$tag" -t "$repo_url:latest" "$repo_root"

docker push "$repo_url:$tag"
docker push "$repo_url:latest"
echo "[push] pushed $repo_url:$tag (+ :latest)"
