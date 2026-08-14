#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/heart/backend"
exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8000}"
