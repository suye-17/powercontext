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
EVIDENCE="${PAPERDESK_EVIDENCE_DIR:-$QODER_DIR/evidence}"
PC_URL="${PAPERDESK_POWERCONTEXT_URL:-http://127.0.0.1:8011}"
WEB_PORT="${PAPERDESK_WEB_PORT:-8080}"
PY="$APP_DIR/.venv/bin/python"
PAPERDESK="$APP_DIR/.venv/bin/paperdesk"
LIBRARY="$QODER_DIR/sample/library"
QUERIES="$QODER_DIR/sample/eval/queries.json"

export PAPERDESK_DATA_DIR="$DATA_DIR"
export PAPERDESK_POWERCONTEXT_URL="$PC_URL"
export PAPERDESK_LIBRARY="$LIBRARY"
export PYTHONPATH="$APP_DIR/src${PYTHONPATH:+:$PYTHONPATH}"

STARTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
rm -rf "$EVIDENCE"/{seekdb,powercontext,llm,eval,web}
rm -f "$EVIDENCE/run-status.txt"
mkdir -p "$EVIDENCE"/{seekdb,powercontext,llm,eval,qoder,web} "$DATA_DIR"

write_status() {
  local status="$1"
  {
    printf 'status=%s\n' "$status"
    printf 'started_at=%s\n' "$STARTED_AT"
    printf 'updated_at=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf 'commit=%s\n' "$(git -C "$QODER_DIR/.." rev-parse --short HEAD 2>/dev/null || echo unknown)"
  } >"$EVIDENCE/run-status.txt"
}
trap 'if [[ "${SCENARIO_OK:-0}" != "1" ]]; then write_status failed; fi' EXIT
write_status running

# The MiniMax free tier enforces a low requests-per-minute quota; PowerContext
# surfaces provider throttling as 503, so retry transient failures with backoff.
retry() {
  local label="$1" max="${2:-4}" delay=5 status attempt=1
  shift 2
  until "$@"; do
    status=$?
    if (( attempt >= max )); then
      echo "error: $label failed after $attempt attempts" >&2
      return "$status"
    fi
    echo "retry: $label (attempt $((attempt + 1))/$max) in ${delay}s" >&2
    sleep "$delay"
    attempt=$((attempt + 1))
    delay=$((delay * 2))
  done
}

wait_for() {
  local label="$1" max="$2" delay="$3" attempt
  shift 3
  for attempt in $(seq 1 "$max"); do
    if "$@"; then
      return 0
    fi
    if (( attempt == max )); then
      echo "error: $label failed after $max attempts" >&2
      return 1
    fi
    echo "retry: $label (attempt $((attempt + 1))/$max) in ${delay}s" >&2
    sleep "$delay"
  done
}

if [[ ! -x "$PAPERDESK" ]]; then
  echo "error: missing $PAPERDESK; run: cd $APP_DIR && uv sync --extra dev --extra server" >&2
  exit 1
fi

echo "== reset =="
"$QODER_DIR/sample/scenario/reset.sh"

echo "== start PowerContext =="
"$QODER_DIR/scripts/start_powercontext.sh"

echo "== ingest =="
"$PAPERDESK" ingest --json | tee "$EVIDENCE/seekdb/ingest.json" | tee "$EVIDENCE/seekdb/ingest.log"
"$PY" - "$EVIDENCE/seekdb/ingest.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["documents"] == 6, report
assert report["chunks"] == 30, report
assert report["embedded"] == 30, report
assert report["embedding_error"] is None, report
PY
"$PAPERDESK" schema --out "$EVIDENCE/seekdb/ddl.sql"
cp "$QODER_DIR/sample/scenario/seekdb-queries.sql" "$EVIDENCE/seekdb/queries.sql"

search_evidence() {
  local name="$1"
  shift
  "$PAPERDESK" search "$@" --json >"$EVIDENCE/seekdb/$name.json"
  "$PY" - "$EVIDENCE/seekdb/$name.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["note"] is None, report["note"]
assert report["hits"], report
assert all(hit["vector_score"] is not None for hit in report["hits"]), report["hits"][0]
PY
}
retry "search-unfiltered" 4 search_evidence query-unfiltered "chunk boundaries"
retry "search-year-2023" 4 search_evidence query-year-2023 "chunk boundaries" --year-min 2023
retry "search-status-reading" 4 search_evidence query-status-reading "rerank" --status reading
retry "search-tag-citation" 4 search_evidence query-tag-citation "citation coverage" --tag citation

{
  echo "# chunk boundaries, no filters"
  cat "$EVIDENCE/seekdb/query-unfiltered.json"
  echo "# chunk boundaries, year >= 2023 (drops the 2022 chunking note)"
  cat "$EVIDENCE/seekdb/query-year-2023.json"
  echo "# rerank, status=reading only"
  cat "$EVIDENCE/seekdb/query-status-reading.json"
  echo "# citation coverage, tag=citation"
  cat "$EVIDENCE/seekdb/query-tag-citation.json"
} >"$EVIDENCE/seekdb/query-results.txt"
"$PY" - "$EVIDENCE/seekdb" <<'PY'
import json, sys
from pathlib import Path

root = Path(sys.argv[1])

def papers(name):
    report = json.load(open(root / name, encoding="utf-8"))
    return [hit["paper_id"] for hit in report["hits"]]

unfiltered = papers("query-unfiltered.json")
assert unfiltered and unfiltered[0] == "chunking-notes", unfiltered
assert "chunking-notes" not in papers("query-year-2023.json")
reading = set(papers("query-status-reading.json"))
assert reading == {"rerank-notes"}, reading
citation = set(papers("query-tag-citation.json"))
assert citation == {"citation-grounding", "eval-notes"}, citation
print("seekdb filters: top", unfiltered[0], "| year/status/tag scopes verified")
PY

echo "== session 1: goal, profile, progress, answer =="
{
  retry "set-goal" 4 "$PAPERDESK" memory set-goal --goal-id weekly \
    --text "本周读完 RAG 相关两篇，并整理 rerank 要点" --tags "rag,rerank"
  sleep 2
  retry "set-profile" 4 "$PAPERDESK" memory set-profile --digest-sentences 3 --weekly-minutes 120
  sleep 2
  retry "set-progress" 4 "$PAPERDESK" memory set-progress --paper-id rag-survey --status reading \
    --last-section Results --next-action "读 Evaluation 章"
} | tee "$EVIDENCE/powercontext/write.log"

"$PAPERDESK" plan --json >"$EVIDENCE/powercontext/plan-session1.json"

retry "answer" 3 "$PAPERDESK" answer "rerank 在 RAG 里解决什么问题？" \
  --evidence "$EVIDENCE/llm/answer-session1.json" \
  | tee "$EVIDENCE/llm/answer-session1.txt"
"$PY" - "$EVIDENCE/llm/answer-session1.json" <<'PY'
import json, sys
synthesis = json.load(open(sys.argv[1], encoding="utf-8"))["synthesis"]
assert synthesis["mode"] == "llm", synthesis
assert synthesis["coverage"] == 1.0, synthesis
assert not synthesis["fallback_reason"], synthesis
PY

retry "finish-session" 4 "$PAPERDESK" memory finish-session \
  --paper-id rag-survey \
  --status reading \
  --last-section Evaluation \
  --next-action "整理 rerank 与 RAG 的关系" \
  --questions "rerank 在 RAG 里解决什么问题？" \
  --next-steps "读完 Evaluation 章,整理 rerank 要点" \
  | tee "$EVIDENCE/powercontext/session1.txt"

"$PAPERDESK" memory list --json >"$EVIDENCE/powercontext/list-before.json"
"$PAPERDESK" memory list >"$EVIDENCE/powercontext/list-before.txt"

check_memory_hybrid() {
  "$PAPERDESK" memory check --json >"$EVIDENCE/powercontext/scope.json"
  "$PY" - "$EVIDENCE/powercontext/scope.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
search = report["search"]
assert report["entry_count"] == 4, report
assert report["kinds"] == {
    "paperdesk.goal": 1,
    "paperdesk.profile": 1,
    "paperdesk.progress": 1,
    "paperdesk.session": 1,
}, report["kinds"]
assert any(
    entry["kind"] == "paperdesk.progress" and entry["revision"] == 2
    for entry in report["entries"]
), report["entries"]
assert search["mode"] == "hybrid", search
assert search["hits"], search
assert search["hits"][0]["memory"]["kind"] == "paperdesk.progress", search["hits"][0]["memory"]
PY
}
sleep 10
wait_for "memory-check" 4 20 check_memory_hybrid

echo "== restart PowerContext =="
"$QODER_DIR/scripts/stop_powercontext.sh"
"$QODER_DIR/scripts/start_powercontext.sh"
"$PAPERDESK" memory list --json >"$EVIDENCE/powercontext/list-after.json"
"$PAPERDESK" memory list >"$EVIDENCE/powercontext/list-after.txt"

"$PY" - "$EVIDENCE/powercontext/list-before.json" "$EVIDENCE/powercontext/list-after.json" >"$EVIDENCE/powercontext/restart-normalized.json" <<'PY'
import json, sys
rows = []
for path in sys.argv[1:]:
    entries = json.load(open(path, encoding="utf-8"))
    rows.append(sorted(entries, key=lambda item: (item["kind"], item["artifact_id"])))
before, after = rows
assert before == after, "memory entries changed across restart"
json.dump(before, sys.stdout, ensure_ascii=False, indent=2)
sys.stdout.write("\n")
PY
if diff -u "$EVIDENCE/powercontext/list-before.txt" "$EVIDENCE/powercontext/list-after.txt" >"$EVIDENCE/powercontext/restart-compare.txt"; then
  echo "restart compare: no differences" >>"$EVIDENCE/powercontext/restart-compare.txt"
else
  echo "error: memory list changed across restart" >&2
  exit 1
fi

echo "== session 2: continue, digest, memory recall =="
"$PAPERDESK" plan --json >"$EVIDENCE/powercontext/plan-session2.json"
"$PY" - "$EVIDENCE/powercontext/plan-session1.json" "$EVIDENCE/powercontext/plan-session2.json" <<'PY'
import json, sys
session1 = json.load(open(sys.argv[1], encoding="utf-8"))
session2 = json.load(open(sys.argv[2], encoding="utf-8"))
ids1 = [item["paper_id"] for item in session1["plan"][:2]]
assert "rag-survey" in ids1 and "rerank-notes" in ids1, ids1
assert session2["plan"][0]["paper_id"] == "rag-survey", session2["plan"][0]
assert session2["continue"]["paper_id"] == "rag-survey", session2["continue"]
assert session2["continue"]["last_section"] == "Evaluation", session2["continue"]
print("plan-session1:", ids1)
print("plan-session2 first:", session2["plan"][0]["paper_id"], "-", session2["plan"][0]["next_section"])
print("continue:", session2["continue"]["headline"])
PY

retry "digest" 3 "$PAPERDESK" digest rag-survey --evidence "$EVIDENCE/llm/digest-rag-survey.json" \
  | tee "$EVIDENCE/llm/digest-rag-survey.txt"
"$PY" - "$EVIDENCE/llm/digest-rag-survey.json" <<'PY'
import json, sys
synthesis = json.load(open(sys.argv[1], encoding="utf-8"))["synthesis"]
assert synthesis["mode"] == "llm", synthesis
assert synthesis["coverage"] == 1.0, synthesis
assert not synthesis["fallback_reason"], synthesis
PY

search_memory_hybrid() {
  "$PAPERDESK" memory search "上次读到哪篇论文，下一步做什么" --mode hybrid --json \
    >"$EVIDENCE/powercontext/search-hybrid.json"
  "$PY" - "$EVIDENCE/powercontext/search-hybrid.json" <<'PY'
import json, sys
report = json.load(open(sys.argv[1], encoding="utf-8"))
assert report["mode"] == "hybrid", report
assert report["hits"], report
top = report["hits"][0]["memory"]
assert top["kind"] == "paperdesk.progress", top
assert "rag-survey" in top["text"], top
PY
}
sleep 2
wait_for "memory-search-hybrid" 4 20 search_memory_hybrid
retry "memory-search-fts" 4 "$PAPERDESK" memory search "上次读到哪篇论文，下一步做什么" --mode fts --json \
  >"$EVIDENCE/powercontext/search-fts.json"
{
  "$PY" - "$EVIDENCE/powercontext/search-hybrid.json" "$EVIDENCE/powercontext/search-fts.json" <<'PY'
import json, sys
for label, path in (("hybrid", sys.argv[1]), ("fts", sys.argv[2])):
    report = json.load(open(path, encoding="utf-8"))
    top = report["hits"][0]["memory"] if report["hits"] else None
    print(f"{label}: mode={report['mode']} hits={len(report['hits'])} "
          f"top={top['kind'] if top else None} matched_by={','.join(report['hits'][0]['matched_by']) if top else None}")
PY
} | tee "$EVIDENCE/powercontext/recall-summary.txt"

"$PY" - "$EVIDENCE/llm/answer-session1.json" "$EVIDENCE/llm/citation-report.json" <<'PY'
import json, sys
answer = json.load(open(sys.argv[1], encoding="utf-8"))
hits = {hit["chunk_id"] for hit in answer["hits"]}
citations = answer["synthesis"]["citations"]
assert citations, "answer has no citations"
assert all(item["chunk_id"] in hits for item in citations), "citation does not map to a retrieved chunk"
report = {
    "mode": answer["synthesis"]["mode"],
    "coverage": answer["synthesis"]["coverage"],
    "used": [item["marker"] for item in citations],
    "citations": citations,
    "hit_chunk_ids": sorted(hits),
    "validator": answer["synthesis"].get("evidence", {}).get("citation_report"),
    "fallback_reason": answer["synthesis"]["fallback_reason"],
}
json.dump(report, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("citation check:", report["mode"], "coverage", report["coverage"], "markers", report["used"])
PY

PAPERDESK_LLM_MODE=extractive "$PAPERDESK" answer "rerank 在 RAG 里解决什么问题？" \
  --evidence "$EVIDENCE/llm/answer-extractive.json" | tee "$EVIDENCE/llm/answer-extractive.txt"

echo "== retrieval evaluation =="
retry "eval-keyword" 3 "$PAPERDESK" eval --queries "$QUERIES" --no-embed --json \
  >"$EVIDENCE/eval/retrieval-metrics-keyword.json"
sleep 2
retry "eval-hybrid" 4 "$PAPERDESK" eval --queries "$QUERIES" --json \
  >"$EVIDENCE/eval/retrieval-metrics.json"
"$PY" - "$EVIDENCE/eval/retrieval-metrics-keyword.json" "$EVIDENCE/eval/retrieval-metrics.json" \
  >"$EVIDENCE/eval/metrics.txt" <<'PY'
import json, sys
for label, path in (("keyword", sys.argv[1]), ("hybrid", sys.argv[2])):
    report = json.load(open(path, encoding="utf-8"))
    assert report["embedding"] is (label == "hybrid"), (label, report["embedding"])
    assert report["recall@5"] >= 0.9, (label, report)
    assert report["mrr"] >= 0.6, (label, report)
    print(label, {key: report[key] for key in ("recall@1", "recall@3", "recall@5", "mrr", "embedding")})
PY
cat "$EVIDENCE/eval/metrics.txt"

echo "== web evidence =="
if curl -fsS "http://127.0.0.1:$WEB_PORT/health" >/dev/null 2>&1; then
  echo "error: something is already serving http://127.0.0.1:$WEB_PORT" >&2
  exit 1
fi
(
  cd "$APP_DIR"
  nohup .venv/bin/paperdesk serve --port "$WEB_PORT" >"$DATA_DIR/web.log" 2>&1 &
  echo $! >"$DATA_DIR/web.pid"
)
for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/health" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done
if ! curl -fsS "http://127.0.0.1:$WEB_PORT/health" >/dev/null 2>&1; then
  echo "error: web app did not become healthy; see $DATA_DIR/web.log" >&2
  exit 1
fi
: >"$EVIDENCE/web/http-status.txt"
for path in "/" "/library" "/library?q=hybrid+fusion" "/paper/rag-survey" "/session" "/debug/retrieval?q=hybrid+fusion" "/memory" "/ask?q=rerank+in+RAG"; do
  name="${path//[^a-zA-Z0-9]/_}"
  code="$(curl -sS -o "$EVIDENCE/web/${name}.html" --max-time 120 -w '%{http_code}' "http://127.0.0.1:$WEB_PORT$path")"
  echo "$code $path" | tee -a "$EVIDENCE/web/http-status.txt"
done
"$PY" - "$EVIDENCE/web/http-status.txt" <<'PY'
import sys
rows = [line.split(None, 1) for line in open(sys.argv[1], encoding="utf-8") if line.strip()]
bad = [line for line in rows if line[0] != "200"]
assert not bad, bad
print("web pages:", len(rows), "all 200")
PY
curl -fsS "http://127.0.0.1:$WEB_PORT/health" | tee "$EVIDENCE/web/health.json"

write_status complete
SCENARIO_OK=1
trap - EXIT

echo
echo "scenario complete"
echo "  PowerContext: $PC_URL"
echo "  Web:          http://127.0.0.1:$WEB_PORT"
echo "  evidence:     $EVIDENCE"
