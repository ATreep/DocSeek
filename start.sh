#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PID_FILE="$ROOT_DIR/.docseek-pids"
DATA_DIR="${DOCSEEK_DATA_DIR:-$ROOT_DIR/data}"
API_PORT="${DOCSEEK_API_PORT:-8000}"
FRONTEND_PORT="${DOCSEEK_FRONTEND_PORT:-5173}"
FRONTEND_HOST="${DOCSEEK_FRONTEND_HOST:-0.0.0.0}"

stop_previous() {
  previous_pids=""
  if [[ -f "$PID_FILE" ]]; then
    previous_pids="$(<"$PID_FILE")" || true
    rm -f "$PID_FILE"
    while read -r pid; do
      [[ "$pid" =~ ^[0-9]+$ ]] || continue
      kill "$pid" 2>/dev/null || true
    done <<< "$previous_pids"
    sleep 0.3
    while read -r pid; do
      [[ "$pid" =~ ^[0-9]+$ ]] || continue
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
    done <<< "$previous_pids"
  fi
  # These patterns are scoped to DocSeek's own module/working directory.
  pkill -f "uvicorn backend.app.main:app" 2>/dev/null || true
  pkill -f "$ROOT_DIR/frontend/node_modules/.bin/vite" 2>/dev/null || true
}

stop_previous
mkdir -p "$DATA_DIR/conf" "$DATA_DIR/projects"

if ! command -v uv >/dev/null 2>&1; then
  echo "DocSeek requires uv (https://docs.astral.sh/uv/)." >&2
  exit 1
fi
if ! command -v npm >/dev/null 2>&1; then
  echo "DocSeek requires npm." >&2
  exit 1
fi
if ! command -v curl >/dev/null 2>&1; then
  echo "DocSeek requires curl for the startup health check." >&2
  exit 1
fi

if [[ ! -x "$ROOT_DIR/frontend/node_modules/.bin/vite" ]]; then
  (cd "$ROOT_DIR/frontend" && npm install --no-audit --no-fund)
fi

export DOCSEEK_DATA_DIR="$DATA_DIR"
API_PID=""
FRONTEND_PID=""

cleanup() {
  for pid in "${API_PID:-}" "${FRONTEND_PID:-}"; do
    [[ -n "$pid" ]] && kill "$pid" 2>/dev/null || true
  done
  rm -f "$PID_FILE"
}
trap cleanup INT TERM EXIT

wait_for_api() {
  local attempt
  for attempt in {1..100}; do
    if curl -fsS "http://${DOCSEEK_HOST:-127.0.0.1}:$API_PORT/api/health" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$API_PID" 2>/dev/null; then
      echo "DocSeek API failed during startup. Recent log output:" >&2
      tail -40 "$DATA_DIR/conf/api.log" >&2 || true
      return 1
    fi
    sleep 0.1
  done
  echo "DocSeek API did not become ready in time. Recent log output:" >&2
  tail -40 "$DATA_DIR/conf/api.log" >&2 || true
  return 1
}

uv run uvicorn backend.app.main:app --host "${DOCSEEK_HOST:-127.0.0.1}" --port "$API_PORT" > "$DATA_DIR/conf/api.log" 2>&1 &
API_PID=$!
wait_for_api
(cd "$ROOT_DIR/frontend" && npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT") > "$DATA_DIR/conf/frontend.log" 2>&1 &
FRONTEND_PID=$!
printf '%s\n%s\n' "$API_PID" "$FRONTEND_PID" > "$PID_FILE"

echo "DocSeek API: http://${DOCSEEK_HOST:-127.0.0.1}:$API_PORT"
echo "DocSeek frontend: http://localhost:$FRONTEND_PORT"
echo "Logs: $DATA_DIR/conf/api.log and $DATA_DIR/conf/frontend.log"
wait "$API_PID" "$FRONTEND_PID"
