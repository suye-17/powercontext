from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .memory import (
    GOAL_KIND,
    PROFILE_KIND,
    PROGRESS_KIND,
    SESSION_KIND,
    MemoryError,
    MemoryState,
    save_goal,
    save_profile,
    save_progress,
    save_session,
)
from .models import Goal, Profile
from .runtime import PaperDeskRuntime
from .service import make_progress, new_session_id

_PACKAGE = Path(__file__).resolve().parent
_TEMPLATES = _PACKAGE / "templates"
_STATIC = _PACKAGE / "static"

STATUS_LABELS = {"unread": "未读", "reading": "在读", "done": "已完成"}
KIND_LABELS = {
    PROFILE_KIND: "阅读偏好",
    GOAL_KIND: "阅读目标",
    PROGRESS_KIND: "阅读进度",
    SESSION_KIND: "会话摘要",
}

DEFAULT_GOAL = Goal(
    goal_id="weekly",
    text="本周读完 RAG 相关两篇，并整理 rerank 要点",
    tags=("rag", "rerank"),
    deadline=None,
)


def _answer_text(value: str) -> str:
    lines = []
    for line in value.splitlines():
        text = line.replace("**", "")
        if text.startswith("- "):
            text = "• " + text[2:]
        lines.append(text)
    return "\n".join(lines)


def create_app(runtime: PaperDeskRuntime | None = None) -> FastAPI:
    app = FastAPI(title="PaperDesk 研读", docs_url="/api/docs", redoc_url=None)
    rt = runtime or PaperDeskRuntime()
    app.state.runtime = rt
    templates = Jinja2Templates(directory=str(_TEMPLATES))
    templates.env.filters["answer_text"] = _answer_text
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")

    def render(request: Request, name: str, active: str, **extra: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            name,
            _context(request, rt, active=active, **extra),
        )

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        state, memory_error = _memory_state(rt)
        profile = state.profile if state else Profile()
        progress = state.progress if state else {}
        goal = _active_goal(state)
        service = rt.service()
        continue_card = service.continue_card(
            goal=goal,
            profile=profile,
            progress=progress,
            session=state.sessions[0] if state and state.sessions else None,
        )
        plan = service.plan(goal, profile=profile, progress=progress)
        return render(
            request,
            "index.html",
            "home",
            goal=goal,
            profile=profile,
            continue_card=continue_card,
            plan=plan,
            sessions=state.sessions[:3] if state else (),
            memory_error=memory_error,
        )

    @app.get("/library", response_class=HTMLResponse)
    def library(
        request: Request,
        q: str = "",
        tag: str = "",
        year_min: int | None = None,
        status: str = "",
    ) -> HTMLResponse:
        store = rt.store()
        papers = store.list_papers()
        hits = []
        note = None
        filters = {"tag": tag or None, "year_min": year_min, "status": status or None}
        if q.strip():
            hits, note = rt.search(q, limit=12, **filters)
        elif any(filters.values()):
            papers = [
                paper
                for paper in papers
                if (not tag or tag in paper.tags)
                and (year_min is None or paper.year >= year_min)
                and (not status or paper.status == status)
            ]
        tags = sorted({tag_name for paper in store.list_papers() for tag_name in paper.tags})
        return render(
            request,
            "library.html",
            "library",
            q=q,
            tag=tag,
            year_min=year_min,
            status=status,
            tags=tags,
            papers=papers,
            hits=hits,
            note=note,
        )

    @app.get("/paper/{paper_id}", response_class=HTMLResponse)
    def paper_page(request: Request, paper_id: str) -> HTMLResponse:
        store = rt.store()
        paper = store.get_paper(paper_id)
        if paper is None:
            raise HTTPException(status_code=404, detail=f"未找到论文 {paper_id}")
        state, memory_error = _memory_state(rt)
        profile = state.profile if state else Profile()
        digest = rt.service().digest(paper_id, profile=profile)
        chunks = store.get_chunks(paper_id)
        progress = state.progress.get(paper_id) if state else None
        return render(
            request,
            "paper.html",
            "library",
            paper=paper,
            digest=digest,
            chunks=chunks,
            progress=progress,
            memory_error=memory_error,
        )

    @app.get("/ask", response_class=HTMLResponse)
    def ask_get(request: Request, q: str = "") -> HTMLResponse:
        return _render_ask(request, q)

    @app.post("/ask", response_class=HTMLResponse)
    def ask_post(request: Request, q: str = Form("")) -> HTMLResponse:
        return _render_ask(request, q)

    def _render_ask(request: Request, q: str) -> HTMLResponse:
        state, memory_error = _memory_state(rt)
        profile = state.profile if state else Profile()
        answer = rt.service().answer(q, profile=profile) if q.strip() else None
        return render(
            request,
            "ask.html",
            "ask",
            q=q,
            answer=answer,
            memory_error=memory_error,
        )

    @app.get("/session", response_class=HTMLResponse)
    def session_page(request: Request, saved: str = "") -> HTMLResponse:
        state, memory_error = _memory_state(rt)
        profile = state.profile if state else Profile()
        progress = state.progress if state else {}
        service = rt.service()
        continue_card = service.continue_card(
            goal=_active_goal(state),
            profile=profile,
            progress=progress,
            session=state.sessions[0] if state and state.sessions else None,
        )
        return render(
            request,
            "session.html",
            "session",
            continue_card=continue_card,
            progress=progress,
            progress_json={paper_id: entry.as_json() for paper_id, entry in progress.items()},
            sessions=state.sessions[:5] if state else (),
            papers=rt.store().list_papers(),
            memory_error=memory_error,
            saved=saved,
        )

    @app.post("/session/progress")
    def session_progress(
        paper_id: str = Form(...),
        status: str = Form("reading"),
        last_section: str = Form(""),
        next_action: str = Form(""),
    ) -> RedirectResponse:
        _require_paper(rt, paper_id)
        try:
            progress = make_progress(
                paper_id,
                status=status,
                last_section=last_section,
                next_action=next_action,
            )
            save_progress(rt.memory(), rt.scope_id(), progress)
        except (MemoryError, ValueError) as exc:
            return RedirectResponse(f"/session?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/session?saved=progress", status_code=303)

    @app.post("/session/finish")
    def session_finish(
        paper_id: str = Form(...),
        status: str = Form("reading"),
        last_section: str = Form(""),
        next_action: str = Form(""),
        questions: str = Form(""),
        next_steps: str = Form(""),
    ) -> RedirectResponse:
        _require_paper(rt, paper_id)
        try:
            progress = make_progress(
                paper_id,
                status=status,
                last_section=last_section,
                next_action=next_action,
            )
            scope_id = rt.scope_id()
            save_progress(rt.memory(), scope_id, progress)
            summary = rt.service().finish_session(
                new_session_id(),
                updates=(progress,),
                questions=_lines(questions),
                next_steps=_lines(next_steps),
            )
            save_session(rt.memory(), scope_id, summary)
        except (MemoryError, ValueError) as exc:
            return RedirectResponse(f"/session?error={quote(str(exc))}", status_code=303)
        return RedirectResponse(f"/session?saved={summary.session_id}", status_code=303)

    @app.get("/debug/retrieval", response_class=HTMLResponse)
    def retrieval_debug(
        request: Request,
        q: str = "",
        tag: str = "",
        year_min: int | None = None,
        status: str = "",
    ) -> HTMLResponse:
        hits = []
        note = None
        if q.strip():
            hits, note = rt.search(
                q,
                limit=10,
                tag=tag or None,
                year_min=year_min,
                status=status or None,
            )
        store = rt.store()
        return render(
            request,
            "retrieval.html",
            "retrieval",
            q=q,
            tag=tag,
            year_min=year_min,
            status=status,
            hits=hits,
            note=note,
            counts=store.counts(),
            embedding_dim=store.embedding_dim,
            embedding_count=store.embedding_count(),
        )

    @app.get("/memory", response_class=HTMLResponse)
    def memory_page(request: Request, q: str = "", mode: str = "hybrid") -> HTMLResponse:
        state, memory_error = _memory_state(rt)
        search = None
        search_error = None
        scope_id = None
        if state is not None:
            scope_id = rt.scope_id()
            if q.strip():
                try:
                    search = rt.memory().search(scope_id, q, mode=mode)
                except MemoryError as exc:
                    search_error = str(exc)
        grouped: dict[str, list[Any]] = {kind: [] for kind in KIND_LABELS}
        if state is not None:
            for entry in state.entries:
                grouped.setdefault(entry.kind, []).append(entry)
        return render(
            request,
            "memory.html",
            "memory",
            state=state,
            grouped=grouped,
            scope_id=scope_id,
            q=q,
            mode=mode,
            search=search,
            search_error=search_error,
            memory_error=memory_error,
        )

    @app.post("/memory/goal")
    def memory_goal(
        text: str = Form(...),
        goal_id: str = Form("weekly"),
        tags: str = Form(""),
        deadline: str = Form(""),
    ) -> RedirectResponse:
        goal = Goal(
            goal_id=goal_id.strip() or "weekly",
            text=text.strip(),
            tags=tuple(tag.strip().lower() for tag in tags.split(",") if tag.strip()),
            deadline=deadline.strip() or None,
        )
        try:
            save_goal(rt.memory(), rt.scope_id(), goal)
        except (MemoryError, ValueError) as exc:
            return RedirectResponse(f"/memory?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/memory?saved=goal", status_code=303)

    @app.post("/memory/profile")
    def memory_profile(
        digest_sentences: int = Form(3),
        citation_style: str = Form("inline"),
        language: str = Form("zh"),
        weekly_minutes: int = Form(120),
    ) -> RedirectResponse:
        profile = Profile(
            language=language.strip() or "zh",
            digest_sentences=max(1, min(digest_sentences, 8)),
            citation_style=citation_style.strip() or "inline",
            weekly_minutes=max(10, weekly_minutes),
        )
        try:
            save_profile(rt.memory(), rt.scope_id(), profile)
        except (MemoryError, ValueError) as exc:
            return RedirectResponse(f"/memory?error={quote(str(exc))}", status_code=303)
        return RedirectResponse("/memory?saved=profile", status_code=303)

    @app.get("/health")
    def health() -> JSONResponse:
        counts = rt.store().counts()
        try:
            powercontext = rt.memory().health()
        except MemoryError:
            powercontext = False
        return JSONResponse(
            {
                "status": "ok",
                "papers": counts["documents"],
                "chunks": counts["chunks"],
                "embeddings": rt.store().embedding_count(),
                "embedding_enabled": rt.embedding_config.available,
                "llm_mode": rt.llm_config.mode,
                "llm_available": rt.llm_config.available,
                "powercontext": powercontext,
                "powercontext_url": rt.powercontext_url,
            }
        )

    return app


def _context(request: Request, runtime: PaperDeskRuntime, *, active: str, **extra: Any) -> dict[str, Any]:
    counts = runtime.store().counts()
    return {
        "request": request,
        "active": active,
        "counts": counts,
        "status_labels": STATUS_LABELS,
        "kind_labels": KIND_LABELS,
        "embedding_enabled": runtime.embedding_config.available,
        "llm_available": runtime.llm_config.available,
        "llm_mode": runtime.llm_config.mode,
        **extra,
    }


def _memory_state(runtime: PaperDeskRuntime) -> tuple[MemoryState | None, str | None]:
    try:
        return runtime.memory_state(), None
    except MemoryError as exc:
        return None, str(exc)


def _active_goal(state: MemoryState | None) -> Goal:
    if state is not None:
        for goal in state.goals:
            if goal.status == "active":
                return goal
        if state.goals:
            return state.goals[0]
    return DEFAULT_GOAL


def _require_paper(runtime: PaperDeskRuntime, paper_id: str) -> None:
    if runtime.store().get_paper(paper_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到论文 {paper_id}")


def _lines(value: str) -> tuple[str, ...]:
    return tuple(line.strip() for line in value.splitlines() if line.strip())
