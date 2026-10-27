#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8787}"

for collection in \
  stepfun_kb_fp16 \
  stepfun_kb_int8 \
  stepfun_kb_fp32 \
  stepfun_bench_fp32_wasm \
  stepfun_bench_int8_wasm \
  stepfun_bench_fp16_webgpu \
  stepfun_bench_int8_webgpu \
  stepfun_bench_fp32_webgpu; do
  curl -fsS -X POST "$BASE_URL/api/reset" \
    -H "content-type: application/json" \
    -d "{\"collection\":\"$collection\"}" >/dev/null 2>&1 || true
  echo "reset $collection"
done

echo "collections reset; seekdb data directory left intact"
