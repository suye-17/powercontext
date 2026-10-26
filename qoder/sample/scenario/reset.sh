#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
QODER_DIR="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
DATA_DIR="$(realpath -m -- "${PAPERDESK_DATA_DIR:-$QODER_DIR/.runtime}")"

case "$DATA_DIR" in
  ""|"/")
    echo "refusing to reset unsafe data dir: '$DATA_DIR'" >&2
    exit 1
    ;;
esac

if [[ "$DATA_DIR" == "$QODER_DIR" ]]; then
  echo "refusing to reset the deliverable root: '$DATA_DIR'" >&2
  exit 1
fi

case "$DATA_DIR" in
  "$QODER_DIR"/.runtime|"$QODER_DIR"/.runtime/*|"$QODER_DIR"/app/.runtime|"$QODER_DIR"/app/.runtime/*) ;;
  "$QODER_DIR"/*)
    echo "refusing to reset a source path inside $QODER_DIR: '$DATA_DIR'" >&2
    echo "only .runtime directories may be reset without an explicit override" >&2
    exit 1
    ;;
  *)
    if [[ "${PAPERDESK_ALLOW_EXTERNAL_RESET:-0}" != "1" ]]; then
      echo "refusing to reset data dir outside $QODER_DIR: '$DATA_DIR'" >&2
      echo "set PAPERDESK_ALLOW_EXTERNAL_RESET=1 to override" >&2
      exit 1
    fi
    ;;
esac

kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do
    kill_tree "$child"
  done
  kill "$pid" 2>/dev/null || true
}

if [[ -f "$DATA_DIR/web.pid" ]]; then
  web_pid="$(cat "$DATA_DIR/web.pid")"
  if [[ -n "$web_pid" ]] && kill -0 "$web_pid" 2>/dev/null; then
    kill_tree "$web_pid"
    sleep 0.5
  fi
fi

"$QODER_DIR/scripts/stop_powercontext.sh" || true

rm -rf "$DATA_DIR"
rm -rf "$QODER_DIR/app/.runtime/tests"
echo "reset runtime data: $DATA_DIR"
