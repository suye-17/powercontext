set -euo pipefail

# Ensure uv is on PATH (covers shells that haven't sourced ~/.bashrc yet).
if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.local/bin/env"
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
run_dir="$root/.powercontext"
pid_file="$run_dir/server.pid"
log_file="$run_dir/server.log"

mkdir -p "$run_dir"

# If a previous server is still running, stop it first.
if [ -f "$pid_file" ]; then
    old_pid=$(cat "$pid_file" 2>/dev/null || true)
    if [ -n "$old_pid" ] && kill -0 "$old_pid" 2>/dev/null; then
        echo "stopping previous server (pid=$old_pid)"
        kill "$old_pid" 2>/dev/null || true
        for _ in $(seq 1 30); do
            kill -0 "$old_pid" 2>/dev/null || break
            sleep 1
        done
        kill -9 "$old_pid" 2>/dev/null || true
    fi
    rm -f "$pid_file"
fi

echo "==> uv sync --locked --extra cli --extra server"
if ! uv sync --locked --extra cli --extra server 2>&1 | tee -a "$log_file"; then
    echo "uv sync failed" >&2
    exit 1
fi

echo "==> starting powercontext server run"
cd "$root"
uv run powercontext server run >> "$log_file" 2>&1 &
server_pid=$!
echo "$server_pid" > "$pid_file"
echo "server pid=$server_pid  log=$log_file"
