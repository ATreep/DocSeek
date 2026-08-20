#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
PID_FILE="$ROOT_DIR/.docseek-pids"
DATA_DIR="${DOCSEEK_DATA_DIR:-$ROOT_DIR/data}"
API_PORT="${DOCSEEK_API_PORT:-8000}"
FRONTEND_PORT="${DOCSEEK_FRONTEND_PORT:-5173}"
FRONTEND_HOST="${DOCSEEK_FRONTEND_HOST:-0.0.0.0}"
API_HOST="${DOCSEEK_HOST:-127.0.0.1}"
FRONTEND_URL="http://localhost:$FRONTEND_PORT"

http_host_for() {
  case "$1" in
    "0.0.0.0") printf '127.0.0.1' ;;
    "::") printf '[::1]' ;;
    *:*) printf '[%s]' "$1" ;;
    *) printf '%s' "$1" ;;
  esac
}

API_HEALTH_HOST="$(http_host_for "$API_HOST")"
FRONTEND_HEALTH_HOST="$(http_host_for "$FRONTEND_HOST")"
API_URL="http://$API_HEALTH_HOST:$API_PORT"
FRONTEND_HEALTH_URL="http://$FRONTEND_HEALTH_HOST:$FRONTEND_PORT"
if [[ "$FRONTEND_HOST" != "0.0.0.0" && "$FRONTEND_HOST" != "::" ]]; then
  FRONTEND_URL="$FRONTEND_HEALTH_URL"
fi

if [[ -t 1 && "${TERM:-}" != "dumb" && -z "${NO_COLOR:-}" ]]; then
  RESET=$'\033[0m'
  BOLD=$'\033[1m'
  DIM=$'\033[2m'
  GREEN=$'\033[32m'
  CYAN=$'\033[36m'
  YELLOW=$'\033[33m'
  RED=$'\033[31m'
else
  RESET=""
  BOLD=""
  DIM=""
  GREEN=""
  CYAN=""
  YELLOW=""
  RED=""
fi

status_line() {
  local label="$1"
  local detail="$2"
  printf '  %s●%s  %-10s %s\n' "$GREEN" "$RESET" "$label" "$detail"
}

install_dependencies() {
  printf '\n%sPreparing DocSeek%s\n' "$BOLD" "$RESET"
  printf '  %s1/2%s  Installing locked Python dependencies with uv...\n' "$CYAN" "$RESET"
  uv sync --locked
  printf '  %s2/2%s  Installing frontend dependencies with npm...\n' "$CYAN" "$RESET"
  (cd "$ROOT_DIR/frontend" && npm install --no-audit --no-fund)
  printf '  %s✓ Dependencies are ready.%s\n' "$GREEN" "$RESET"
}

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

install_dependencies

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
    if curl -fsS "$API_URL/api/health" >/dev/null 2>&1; then
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

wait_for_frontend() {
  local attempt
  for attempt in {1..100}; do
    if curl -fsS "$FRONTEND_HEALTH_URL" >/dev/null 2>&1; then
      return 0
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      echo "DocSeek frontend failed during startup. Recent log output:" >&2
      tail -40 "$DATA_DIR/conf/frontend.log" >&2 || true
      return 1
    fi
    sleep 0.1
  done
  echo "DocSeek frontend did not become ready in time. Recent log output:" >&2
  tail -40 "$DATA_DIR/conf/frontend.log" >&2 || true
  return 1
}

show_dashboard() {
  local browser_command="Copy the URL into your browser's address bar"
  if command -v open >/dev/null 2>&1; then
    browser_command="open $FRONTEND_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    browser_command="xdg-open $FRONTEND_URL"
  fi

  printf '\n%s╭──────────────────────────────────────────────────────────────╮%s\n' "$CYAN" "$RESET"
  printf '%s│%s  %sDOCSEEK%s                                                     %s│%s\n' "$CYAN" "$RESET" "$BOLD" "$RESET" "$CYAN" "$RESET"
  printf '%s│%s  Your local knowledge workspace is ready.                  %s│%s\n' "$CYAN" "$RESET" "$CYAN" "$RESET"
  printf '%s╰──────────────────────────────────────────────────────────────╯%s\n' "$CYAN" "$RESET"

  printf '\n%sServices%s\n' "$BOLD" "$RESET"
  status_line "Frontend" "$FRONTEND_URL"
  status_line "API" "$API_URL"

  printf '\n%sOpen DocSeek in your browser%s\n' "$BOLD" "$RESET"
  printf '  1. Open your preferred web browser.\n'
  printf '  2. Visit %s%s%s\n' "$CYAN" "$FRONTEND_URL" "$RESET"
  printf '  3. You can also run: %s%s%s\n' "$CYAN" "$browser_command" "$RESET"
  printf '  %sTip: Many terminals let you open the URL directly from their output.%s\n' "$DIM" "$RESET"
  if [[ "$FRONTEND_HOST" == "0.0.0.0" || "$FRONTEND_HOST" == "::" ]]; then
    printf '  %sRemote device: replace localhost with this machine\047s IP address.%s\n' "$DIM" "$RESET"
  fi

  printf '\n%sRuntime%s\n' "$BOLD" "$RESET"
  printf '  Logs      %s%s/conf/api.log%s\n' "$DIM" "$DATA_DIR" "$RESET"
  printf '            %s%s/conf/frontend.log%s\n' "$DIM" "$DATA_DIR" "$RESET"
  printf '  Stop      Press %sCtrl+C%s to stop both services.\n\n' "$YELLOW" "$RESET"
}

monitor_services() {
  while true; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
      printf '%sDocSeek API stopped unexpectedly. Recent log output:%s\n' "$RED" "$RESET" >&2
      tail -40 "$DATA_DIR/conf/api.log" >&2 || true
      return 1
    fi
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
      printf '%sDocSeek frontend stopped unexpectedly. Recent log output:%s\n' "$RED" "$RESET" >&2
      tail -40 "$DATA_DIR/conf/frontend.log" >&2 || true
      return 1
    fi
    sleep 1
  done
}

printf '\n%sStarting services%s\n' "$BOLD" "$RESET"
uv run uvicorn backend.app.main:app --host "$API_HOST" --port "$API_PORT" > "$DATA_DIR/conf/api.log" 2>&1 &
API_PID=$!
wait_for_api
(cd "$ROOT_DIR/frontend" && npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT") > "$DATA_DIR/conf/frontend.log" 2>&1 &
FRONTEND_PID=$!
wait_for_frontend
printf '%s\n%s\n' "$API_PID" "$FRONTEND_PID" > "$PID_FILE"

show_dashboard
monitor_services
