#!/usr/bin/env bash
# 预置总账本的前任档案（幂等：已有预置档案时不重复写入）。
# 用法：./scripts/seed.sh [PowerContext 地址，默认 http://127.0.0.1:8000]

set -euo pipefail

BASE="${1:-http://127.0.0.1:8000}"
BASE="${BASE%/}"
MARK="预置档案"

post() {
  curl --fail --silent --show-error \
    --header 'Content-Type: application/json' \
    --data "$2" \
    "$BASE$1"
}

echo "== 建总账本（幂等） =="
WORLD_SCOPE="$(post /v1/scopes \
  '{"title":"记忆伪造师·总账本","summary":"所有访客的归档总账","idempotency_key":"memory-forgery:world"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["scope_id"])')"
echo "总账本 scope_id = $WORLD_SCOPE"

echo "== 检查是否已有预置档案 =="
EXISTING="$(post /v1/memory/entries/list "{\"scope_id\":\"$WORLD_SCOPE\"}")"
COUNT="$(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
mark = sys.argv[2]
print(sum(1 for e in data.get("entries", []) if mark in (e.get("text") or "")))
' "$EXISTING" "$MARK")"

if [ "$COUNT" -gt 0 ]; then
  echo "已有 $COUNT 条预置档案，跳过写入（幂等）。"
  exit 0
fi

echo "== 写入预置档案 =="
post /v1/memory/remember "{
  \"scope_id\": \"$WORLD_SCOPE\",
  \"kind\": \"ledger\",
  \"text\": \"预置档案：客人「灰雀」坚称自己从未进过这间店，账本上却留着他三年前的登记。\",
  \"reason\": \"预置前任档案\"
}" > /dev/null
post /v1/memory/remember "{
  \"scope_id\": \"$WORLD_SCOPE\",
  \"kind\": \"ledger\",
  \"text\": \"预置档案：客人「灯芯」说自己只来过一次，还欠着上一次的账。\",
  \"reason\": \"预置前任档案\"
}" > /dev/null

echo "完成：两条预置档案已写入总账本。"
echo "现在打开应用，随便报一个代号，修复师会在第一句回复里提起这些旧档案。"
