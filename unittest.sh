set -euo pipefail

if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.local/bin/env"
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
scripts_dir="$root/scripts"
pid_file="$root/.powercontext/server.pid"

# Gracefully stop any server we started, even on failure.
cleanup() {
    if [ -f "$pid_file" ]; then
        local pid
        pid=$(cat "$pid_file" 2>/dev/null || true)
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            echo "==> stopping server (pid=$pid)"
            kill "$pid" 2>/dev/null || true
            for _ in $(seq 1 30); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$pid_file"
    fi
}
trap cleanup EXIT

echo "===== [1/3] start server ====="
if ! bash "$scripts_dir/start_server.sh"; then
    echo "start_server.sh failed" >&2
    exit 1
fi

echo "===== [2/3] wait for server ready ====="
max_wait=120
interval=3
elapsed=0
while [ "$elapsed" -lt "$max_wait" ]; do
    if bash "$scripts_dir/check_status.sh" >/dev/null 2>&1; then
        echo "server ready after ${elapsed}s"
        break
    fi
    sleep "$interval"
    elapsed=$((elapsed + interval))
done

if [ "$elapsed" -ge "$max_wait" ]; then
    echo "server did not become ready within ${max_wait}s" >&2
    bash "$scripts_dir/check_status.sh" || true
    exit 1
fi

echo "===== [3/3] final status probe ====="
if ! bash "$scripts_dir/check_status.sh"; then
    echo "status probe FAILED" >&2
    exit 1
fi

echo "===== unittest PASSED ====="
exit 0
