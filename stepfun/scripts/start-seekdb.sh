#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${SEEKDB_PORT:-2881}"
BASE_DIR="${SEEKDB_BASE_DIR:-$ROOT/.runtime/seekdb}"
DATA_DIR="${SEEKDB_DATA_DIR:-$BASE_DIR/store}"

mkdir -p "$BASE_DIR" "$DATA_DIR"

# Ubuntu 24.04 ships libaio.so.1t64; seekdb expects libaio.so.1.
if ! ldconfig -p 2>/dev/null | grep -q 'libaio\.so\.1 '; then
  already_available=0
  if [ -n "${LD_LIBRARY_PATH:-}" ]; then
    IFS=':' read -r -a search_dirs <<< "$LD_LIBRARY_PATH"
    for dir in "${search_dirs[@]}"; do
      if [ -e "$dir/libaio.so.1" ]; then
        already_available=1
        break
      fi
    done
  fi
  if [ "$already_available" -eq 0 ]; then
    for candidate in /lib/x86_64-linux-gnu/libaio.so.1t64 /usr/lib/x86_64-linux-gnu/libaio.so.1t64; do
      if [ -e "$candidate" ]; then
        mkdir -p "$ROOT/.runtime/lib"
        ln -sf "$candidate" "$ROOT/.runtime/lib/libaio.so.1"
        export LD_LIBRARY_PATH="$ROOT/.runtime/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
        break
      fi
    done
  fi
fi

BIN="${SEEKDB_BIN:-}"
if [ -z "$BIN" ] && command -v seekdb >/dev/null 2>&1; then
  BIN="$(command -v seekdb)"
fi

if [ -n "$BIN" ] && [ -x "$BIN" ]; then
  echo "starting seekdb from $BIN on port $PORT"
  exec "$BIN" \
    --nodaemon \
    --port "$PORT" \
    --base-dir "$BASE_DIR" \
    --data-dir "$DATA_DIR" \
    --parameter "datafile_size=${SEEKDB_DATAFILE_SIZE:-2G}" \
    --parameter "log_disk_size=${SEEKDB_LOG_DISK_SIZE:-2G}"
fi

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  docker rm -f stepfun-seekdb >/dev/null 2>&1 || true
  echo "starting seekdb container on port $PORT"
  docker run -d \
    --name stepfun-seekdb \
    -p "$PORT:2881" \
    -v "$BASE_DIR/docker:/var/lib/oceanbase" \
    oceanbase/seekdb:latest >/dev/null
  for _ in $(seq 1 30); do
    if (echo >"/dev/tcp/127.0.0.1/$PORT") >/dev/null 2>&1; then
      echo "seekdb is listening on 127.0.0.1:$PORT"
      exit 0
    fi
    sleep 2
  done
  echo "seekdb container did not become ready; check: docker logs stepfun-seekdb" >&2
  exit 1
fi

cat >&2 <<'MESSAGE'
No local seekdb binary was found and Docker is not accessible.

Choose one of:
  1. Point SEEKDB_BIN at a seekdb 1.4+ executable:
       SEEKDB_BIN=/path/to/seekdb npm run db:start
  2. Start the official container yourself:
       docker run -d --name stepfun-seekdb -p 2881:2881 \
         -v "$PWD/.runtime/seekdb/docker:/var/lib/oceanbase" oceanbase/seekdb:latest
  3. Use an already running seekdb or OceanBase instance and set
       SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASSWORD / SEEKDB_DATABASE
MESSAGE
exit 1
