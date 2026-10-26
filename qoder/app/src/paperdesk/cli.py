from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .eval import evaluate, load_queries
from .memory import (
    GOAL_KIND,
    PROFILE_KIND,
    PROGRESS_KIND,
    SESSION_KIND,
    MemoryError,
    save_goal,
    save_profile,
    save_progress,
    save_session,
)
from .models import Goal, Profile
from .runtime import PACKAGE_ROOT, PaperDeskRuntime
from .service import make_progress, new_session_id


def main(argv: list[str] | None = None) -> int:
    _load_local_env()
    parser = _parser()
    args = parser.parse_args(argv)
    runtime = PaperDeskRuntime()
    try:
        return args.handler(args, runtime)
    except (MemoryError, ValueError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        runtime.close()


def _load_local_env() -> None:
    path = PACKAGE_ROOT / ".env"
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip("\"'")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="paperdesk", description="PaperDesk 科研文献阅读工作台")
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="解析样例资料库并写入 seekdb")
    ingest.add_argument("--root", type=Path, help="Markdown 资料库目录")
    ingest.add_argument("--no-embed", action="store_true", help="跳过向量化，仅写入全文索引")
    ingest.add_argument("--json", action="store_true")
    ingest.set_defaults(handler=_ingest)

    search = sub.add_parser("search", help="检索章节分块")
    search.add_argument("query")
    search.add_argument("-n", "--limit", type=int, default=8)
    search.add_argument("--tag")
    search.add_argument("--year-min", type=int)
    search.add_argument("--status", choices=("unread", "reading", "done"))
    search.add_argument("--json", action="store_true")
    search.set_defaults(handler=_search)

    plan = sub.add_parser("plan", help="结合记忆与检索生成阅读计划")
    plan.add_argument("--goal-id", default="weekly")
    plan.add_argument("--goal-text")
    plan.add_argument("--tags", default="", help="逗号分隔的目标标签")
    plan.add_argument("--deadline")
    plan.add_argument("--save-goal", action="store_true", help="把目标写入 PowerContext")
    plan.add_argument("--json", action="store_true")
    plan.set_defaults(handler=_plan)

    digest = sub.add_parser("digest", help="生成单篇速读")
    digest.add_argument("paper_id")
    digest.add_argument("--evidence", type=Path)
    digest.add_argument("--json", action="store_true")
    digest.set_defaults(handler=_digest)

    answer = sub.add_parser("answer", help="基于检索分块生成带引用的中文综述")
    answer.add_argument("question")
    answer.add_argument("-n", "--limit", type=int, default=6)
    answer.add_argument("--tag")
    answer.add_argument("--year-min", type=int)
    answer.add_argument("--status", choices=("unread", "reading", "done"))
    answer.add_argument("--evidence", type=Path)
    answer.add_argument("--json", action="store_true")
    answer.set_defaults(handler=_answer)

    evaluation = sub.add_parser("eval", help="运行 recall@k / MRR 评测")
    evaluation.add_argument("--queries", type=Path, required=True)
    evaluation.add_argument("--out", type=Path)
    evaluation.add_argument("--no-embed", action="store_true")
    evaluation.add_argument("--json", action="store_true")
    evaluation.set_defaults(handler=_eval)

    memory = sub.add_parser("memory", help="PowerContext 记忆操作")
    memory_sub = memory.add_subparsers(dest="memory_command", required=True)

    check = memory_sub.add_parser("check", help="体检 scope、条目、版本与语义召回")
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=_memory_check)

    listing = memory_sub.add_parser("list", help="列出活跃记忆条目")
    listing.add_argument("--kind", choices=(PROFILE_KIND, GOAL_KIND, PROGRESS_KIND, SESSION_KIND))
    listing.add_argument("--include-inactive", action="store_true")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=_memory_list)

    recall = memory_sub.add_parser("search", help="按问题召回记忆")
    recall.add_argument("query")
    recall.add_argument("--mode", default="hybrid", choices=("auto", "fts", "vector", "hybrid"))
    recall.add_argument("--json", action="store_true")
    recall.set_defaults(handler=_memory_search)

    goal = memory_sub.add_parser("set-goal", help="写入或修订阅读目标")
    goal.add_argument("--goal-id", default="weekly")
    goal.add_argument("--text", required=True)
    goal.add_argument("--tags", default="")
    goal.add_argument("--deadline")
    goal.set_defaults(handler=_memory_set_goal)

    profile = memory_sub.add_parser("set-profile", help="写入或修订阅读偏好")
    profile.add_argument("--language", default="zh")
    profile.add_argument("--digest-sentences", type=int, default=3)
    profile.add_argument("--citation-style", default="inline")
    profile.add_argument("--weekly-minutes", type=int, default=120)
    profile.set_defaults(handler=_memory_set_profile)

    progress = memory_sub.add_parser("set-progress", help="写入或修订单篇阅读进度")
    progress.add_argument("--paper-id", required=True)
    progress.add_argument("--status", required=True, choices=("unread", "reading", "done"))
    progress.add_argument("--last-section", default="")
    progress.add_argument("--next-action", default="")
    progress.set_defaults(handler=_memory_set_progress)

    finish = memory_sub.add_parser("finish-session", help="结束会话并保存进度与摘要")
    finish.add_argument("--session-id")
    finish.add_argument("--paper-id", required=True)
    finish.add_argument("--status", required=True, choices=("unread", "reading", "done"))
    finish.add_argument("--last-section", default="")
    finish.add_argument("--next-action", default="")
    finish.add_argument("--questions", default="")
    finish.add_argument("--next-steps", default="")
    finish.set_defaults(handler=_memory_finish_session)

    schema = sub.add_parser("schema", help="输出 seekdb 表结构")
    schema.add_argument("--out", type=Path)
    schema.set_defaults(handler=_schema)

    serve = sub.add_parser("serve", help="启动 Web 工作台")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(handler=_serve)
    return parser


def _ingest(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    report = runtime.ingest(args.root, embed=not args.no_embed)
    payload = report.as_json()
    if args.json:
        _json(payload)
    else:
        print(f"documents={payload['documents']} chunks={payload['chunks']} embedded={payload['embedded']}")
        print(f"seekdb: {payload['db_dir']}")
        if payload["embedding_error"]:
            print(f"embedding fallback: {payload['embedding_error']}")
    return 0


def _search(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    hits, note = runtime.search(
        args.query,
        limit=args.limit,
        tag=args.tag,
        year_min=args.year_min,
        status=args.status,
    )
    payload = {"query": args.query, "note": note, "hits": [_hit(hit) for hit in hits]}
    if args.json:
        _json(payload)
    else:
        if note:
            print(note)
        for index, hit in enumerate(hits, start=1):
            scores = f"score={hit.score:.3f} fts={hit.fts_score:.3f}"
            if hit.vector_score is not None:
                scores += f" vector={hit.vector_score:.3f}"
            print(f"{index}. {hit.paper_id} · {hit.section} · {scores} · {hit.path}:{hit.start_line}")
            print(f"   {hit.text[:160].replace(chr(10), ' ')}")
    return 0


def _plan(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    state = runtime.memory_state()
    goal = None
    if args.goal_text:
        goal = Goal(
            goal_id=args.goal_id,
            text=args.goal_text,
            tags=_tags(args.tags),
            deadline=args.deadline,
        )
        if args.save_goal:
            save_goal(runtime.memory(), runtime.scope_id(), goal)
    else:
        goal = next((item for item in state.goals if item.goal_id == args.goal_id), None)
        if goal is None and state.goals:
            goal = state.goals[0]
    if goal is None:
        goal = Goal(goal_id=args.goal_id, text="本周读完 RAG 相关两篇，并整理 rerank 要点", tags=("rag", "rerank"), deadline=None)
    card = runtime.service().continue_card(
        goal=goal,
        profile=state.profile,
        progress=state.progress,
        session=state.sessions[0] if state.sessions else None,
    )
    items = runtime.service().plan(goal, profile=state.profile, progress=state.progress)
    payload = {
        "goal": goal.as_json(),
        "continue": card.as_json(),
        "plan": [item.as_json() for item in items],
    }
    if args.json:
        _json(payload)
    else:
        print(f"目标：{goal.text}")
        print(f"继续：{card.headline}")
        for index, item in enumerate(items, start=1):
            print(f"{index}. {item.paper_id} · {item.title} · 下一节 {item.next_section}")
            print("   " + "；".join(item.reasons))
    return 0


def _digest(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    state = runtime.memory_state()
    digest = runtime.service().digest(args.paper_id, profile=state.profile)
    if digest is None:
        raise ValueError(f"unknown paper or empty paper: {args.paper_id}")
    payload = digest.as_json()
    _write_evidence(args.evidence, {"kind": "digest", **payload})
    if args.json:
        _json(payload)
    else:
        print(f"{payload['title']} · {digest.synthesis.mode} · coverage={digest.synthesis.coverage:.2f}")
        print(digest.synthesis.text)
        for citation in digest.synthesis.citations:
            print(f"[C{citation.index}] {citation.paper_id} · {citation.section} · {citation.path}:{citation.start_line}")
    return 0


def _answer(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    state = runtime.memory_state()
    answer = runtime.service().answer(
        args.question,
        profile=state.profile,
        tag=args.tag,
        year_min=args.year_min,
        status=args.status,
        limit=args.limit,
    )
    payload = answer.as_json()
    _write_evidence(args.evidence, {"kind": "answer", **payload})
    if args.json:
        _json(payload)
    else:
        print(f"{answer.synthesis.mode} · coverage={answer.synthesis.coverage:.2f}")
        print(answer.synthesis.text)
        for citation in answer.synthesis.citations:
            print(f"[C{citation.index}] {citation.paper_id} · {citation.section} · {citation.path}:{citation.start_line}")
    return 0


def _eval(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    queries = load_queries(args.queries)
    embeddings: dict[str, tuple[float, ...]] = {}
    note = None
    embedder = None if args.no_embed else runtime.embedder()
    if embedder is not None:
        try:
            vectors = embedder.embed([item.query for item in queries], kind="query")
            embeddings = {item.query: vector for item, vector in zip(queries, vectors)}
        except Exception as exc:
            note = f"向量评测不可用，使用全文基线：{exc}"
    report = evaluate(runtime.store(), queries, query_embeddings=embeddings or None)
    if note:
        report["note"] = note
    report["embedding"] = bool(embeddings)
    if args.out:
        _write_evidence(args.out, report)
    if args.json:
        _json(report)
    else:
        print(f"queries={report['query_count']} embedding={report['embedding']}")
        print(f"recall@1={report['recall@1']} recall@3={report['recall@3']} recall@5={report['recall@5']} mrr={report['mrr']}")
        if note:
            print(note)
        if args.out:
            print(f"written: {args.out}")
    return 0


def _memory_check(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    client = runtime.memory()
    if not client.health():
        raise MemoryError(f"PowerContext 未就绪：{runtime.powercontext_url}/health/live")
    scope_id = runtime.scope_id()
    entries = client.list_entries(scope_id)
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[entry.kind] = kinds.get(entry.kind, 0) + 1
    search = client.search(scope_id, "上次读到哪篇论文，下一步做什么", mode="hybrid")
    payload = {
        "base_url": runtime.powercontext_url,
        "scope_id": scope_id,
        "entry_count": len(entries),
        "kinds": kinds,
        "entries": [entry.as_json() for entry in entries],
        "search": search.as_json(),
    }
    if args.json:
        _json(payload)
    else:
        print(f"PowerContext: {runtime.powercontext_url}")
        print(f"scope={scope_id} entries={len(entries)}")
        for kind, count in sorted(kinds.items()):
            print(f"  {kind}: {count}")
        for entry in entries:
            print(f"  - {entry.kind} rev={entry.revision}")
        print(f"search mode={search.mode} hits={len(search.hits)} fallback={search.fallback_reason}")
    return 0


def _memory_list(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    entries = runtime.memory().list_entries(
        runtime.scope_id(),
        kind=args.kind,
        include_inactive=args.include_inactive,
    )
    payload = [entry.as_json() for entry in entries]
    if args.json:
        _json(payload)
    else:
        for entry in entries:
            print(f"{entry.kind} rev={entry.revision} state={entry.state}")
            print(f"  {entry.text}")
    return 0


def _memory_search(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    result = runtime.memory().search(runtime.scope_id(), args.query, mode=args.mode)
    if args.json:
        _json(result.as_json())
    else:
        if result.fallback_reason:
            print(result.fallback_reason)
        print(f"requested={result.requested_mode} mode={result.mode} hits={len(result.hits)}")
        for hit in result.hits:
            print(f"  {hit.score:.3f} {hit.record.kind} rev={hit.record.revision} matched={','.join(hit.matched_by)}")
            print(f"    {hit.record.text}")
    return 0


def _memory_set_goal(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    goal = Goal(goal_id=args.goal_id, text=args.text, tags=_tags(args.tags), deadline=args.deadline)
    record = save_goal(runtime.memory(), runtime.scope_id(), goal)
    print(f"goal {record.artifact_id} revision={record.revision}")
    return 0


def _memory_set_profile(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    profile = Profile(
        language=args.language,
        digest_sentences=args.digest_sentences,
        citation_style=args.citation_style,
        weekly_minutes=args.weekly_minutes,
    )
    record = save_profile(runtime.memory(), runtime.scope_id(), profile)
    print(f"profile {record.artifact_id} revision={record.revision}")
    return 0


def _memory_set_progress(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    progress = make_progress(
        args.paper_id,
        status=args.status,
        last_section=args.last_section,
        next_action=args.next_action,
    )
    record = save_progress(runtime.memory(), runtime.scope_id(), progress)
    print(f"progress {args.paper_id} revision={record.revision}")
    return 0


def _memory_finish_session(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    scope_id = runtime.scope_id()
    progress = make_progress(
        args.paper_id,
        status=args.status,
        last_section=args.last_section,
        next_action=args.next_action,
    )
    progress_record = save_progress(runtime.memory(), scope_id, progress)
    summary = runtime.service().finish_session(
        args.session_id or new_session_id(),
        updates=(progress,),
        questions=_split(args.questions),
        next_steps=_split(args.next_steps),
    )
    session_record = save_session(runtime.memory(), scope_id, summary)
    print(f"progress revision={progress_record.revision} session {summary.session_id} revision={session_record.revision}")
    return 0


def _schema(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    ddl = runtime.store().schema()
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(ddl + "\n", encoding="utf-8")
        print(f"written: {args.out}")
    else:
        print(ddl)
    return 0


def _serve(args: argparse.Namespace, runtime: PaperDeskRuntime) -> int:
    import uvicorn

    from .web import create_app

    uvicorn.run(create_app(runtime), host=args.host, port=args.port, log_level="info")
    return 0


def _hit(hit: Any) -> dict[str, Any]:
    payload = {
        "chunk_id": hit.chunk_id,
        "paper_id": hit.paper_id,
        "title": hit.title,
        "section": hit.section,
        "path": hit.path,
        "start_line": hit.start_line,
        "end_line": hit.end_line,
        "score": round(hit.score, 4),
        "fts_score": round(hit.fts_score, 4),
        "vector_score": round(hit.vector_score, 4) if hit.vector_score is not None else None,
        "tags": list(hit.tags),
        "text": hit.text,
    }
    return payload


def _tags(value: str) -> tuple[str, ...]:
    return tuple(tag.strip().lower() for tag in value.split(",") if tag.strip())


def _split(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def _write_evidence(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
