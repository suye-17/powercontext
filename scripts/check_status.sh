set -euo pipefail

if [ -f "$HOME/.local/bin/env" ]; then
    # shellcheck disable=SC1091
    . "$HOME/.local/bin/env"
fi

root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)

echo "==> powercontext ready"
if ! (cd "$root" && uv run powercontext ready); then
    echo "ready check FAILED" >&2
    exit 1
fi

echo "==> powercontext capabilities"
if ! (cd "$root" && uv run powercontext capabilities); then
    echo "capabilities check FAILED" >&2
    exit 1
fi

echo "all probes passed"
exit 0
