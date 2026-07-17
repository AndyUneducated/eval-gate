#!/usr/bin/env bash
# Container entrypoint for the EvalGate API image.
#
# Commands:
#   serve    (default) start the FastAPI server on :8000. If RUN_MIGRATIONS is
#            truthy, run `alembic upgrade head` first (fine for a single-task
#            demo service; use the one-off `migrate` task for multi-task fleets).
#   migrate  run `alembic upgrade head` and exit — the one-off ECS task / job
#            used to apply schema changes without racing the running service.
#   *        anything else is exec'd verbatim (e.g. `docker run ... bash`).
set -euo pipefail

run_migrations() {
    echo "[entrypoint] applying database migrations (alembic upgrade head)"
    alembic upgrade head
}

cmd="${1:-serve}"

case "$cmd" in
serve)
    case "${RUN_MIGRATIONS:-false}" in
    1 | true | yes | on) run_migrations ;;
    esac
    echo "[entrypoint] starting uvicorn on 0.0.0.0:${PORT:-8000}"
    exec uvicorn evalgate.api.main:app --host 0.0.0.0 --port "${PORT:-8000}"
    ;;
migrate)
    run_migrations
    echo "[entrypoint] migrations complete"
    ;;
*)
    exec "$@"
    ;;
esac
