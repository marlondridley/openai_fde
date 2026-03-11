#!/usr/bin/env bash
set -euo pipefail

# Remove Python caches and temp artifacts
find . -name '__pycache__' -type d -prune -exec rm -rf {} +
rm -rf .pytest_cache .mypy_cache .ruff_cache
rm -f run.log run-live.log

# Stop any compose services left behind
if command -v docker >/dev/null 2>&1; then
  docker compose down --remove-orphans >/dev/null 2>&1 || true
fi

echo "Demo workspace reset. Reinstall deps or rebuild Docker image if requirements changed."
