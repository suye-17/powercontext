#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QODER_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
if [[ -f "$QODER_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$QODER_DIR/.env"
  set +a
fi
APP_DIR="$QODER_DIR/app"
DATA_DIR="${PAPERDESK_DATA_DIR:-$QODER_DIR/.runtime}"
PC_HOME="${PAPERDESK_POWERCONTEXT_HOME:-$DATA_DIR/powercontext}"
PC_HOST="${PAPERDESK_POWERCONTEXT_HOST:-127.0.0.1}"
PC_PORT="${PAPERDESK_POWERCONTEXT_PORT:-8011}"
LOG_FILE="$DATA_DIR/powercontext.log"
PID_FILE="$DATA_DIR/powercontext.pid"

mkdir -p "$DATA_DIR" "$PC_HOME"

if curl -fsS "http://$PC_HOST:$PC_PORT/health/live" >/dev/null 2>&1; then
  echo "PowerContext is already listening on http://$PC_HOST:$PC_PORT"
  exit 0
fi

if [[ ! -x "$APP_DIR/.venv/bin/powercontext" ]]; then
  echo "error: app venv is missing PowerContext; run: cd $APP_DIR && uv sync --extra server" >&2
  exit 1
fi

export POWERCONTEXT_HOME="$PC_HOME"
export POWERCONTEXT_SERVER_HTTP_HOST="$PC_HOST"
export POWERCONTEXT_SERVER_HTTP_PORT="$PC_PORT"
export POWERCONTEXT_SERVER_MCP_ENABLED=false
export POWERCONTEXT_SERVER_DASHBOARD_ENABLED=false
export POWERCONTEXT_SERVER_ACCESS_MODE=disabled
export POWERCONTEXT_SERVER_LOGGING_LEVEL=WARNING
# PaperDesk writes explicit memories; background dream jobs only add provider load.
export POWERCONTEXT_SERVER_RUNTIME_DREAM_ENABLED=false

if [[ -n "${POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL:-}" ]]; then
  :
elif [[ -n "${MINIMAX_API_KEY:-}" && -n "${MINIMAX_BASE_URL:-}" ]]; then
  export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL="minimax:embo-01"
  export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID="minimax-embo-01"
  export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=1536
  export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL="$MINIMAX_BASE_URL"
  export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_HEADERS="{\"Authorization\":\"Bearer $MINIMAX_API_KEY\"}"
  echo "embedding: MiniMax embo-01 (1536 dimensions)"
else
  echo "embedding: not configured; PowerContext search will fall back to fts"
fi

cd "$APP_DIR"
nohup .venv/bin/powercontext server run >"$LOG_FILE" 2>&1 &
echo $! >"$PID_FILE"

for _ in $(seq 1 40); do
  if curl -fsS "http://$PC_HOST:$PC_PORT/health/live" >/dev/null 2>&1; then
    echo "PowerContext ready: http://$PC_HOST:$PC_PORT (pid $(cat "$PID_FILE"))"
    exit 0
  fi
  sleep 0.5
done

echo "error: PowerContext did not become healthy; see $LOG_FILE" >&2
tail -20 "$LOG_FILE" >&2 || true
exit 1
