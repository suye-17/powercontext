from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .embeddings import EmbeddingClient, EmbeddingConfig, EmbeddingError
from .llm import LLMClient, LLMConfig
from .memory import (
    MemoryState,
    PowerContextClient,
    load_state,
)
from .models import SearchHit
from .parser import ParsedPaper, parse_library
from .service import PaperDeskService
from .store import PaperStore

PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATA_DIR = PACKAGE_ROOT / ".runtime"
DEFAULT_LIBRARY = PACKAGE_ROOT / "sample" / "library"
DEFAULT_POWERCONTEXT_URL = "http://127.0.0.1:8011"


@dataclass(frozen=True)
class IngestReport:
    documents: int
    chunks: int
    embedded: int
    embedding_error: str | None
    db_dir: str

    def as_json(self) -> dict[str, Any]:
        return {
            "documents": self.documents,
            "chunks": self.chunks,
            "embedded": self.embedded,
            "embedding_error": self.embedding_error,
            "db_dir": self.db_dir,
        }


class PaperDeskRuntime:
    def __init__(
        self,
        *,
        data_dir: Path | str | None = None,
        library_dir: Path | str | None = None,
        powercontext_url: str | None = None,
        embedding_config: EmbeddingConfig | None = None,
        llm_config: LLMConfig | None = None,
        environ: dict[str, str] | None = None,
    ) -> None:
        env = environ if environ is not None else dict(os.environ)
        self.data_dir = Path(data_dir or env.get("PAPERDESK_DATA_DIR") or DEFAULT_DATA_DIR)
        self.library_dir = Path(library_dir or env.get("PAPERDESK_LIBRARY") or DEFAULT_LIBRARY)
        self.powercontext_url = (
            powercontext_url or env.get("PAPERDESK_POWERCONTEXT_URL") or DEFAULT_POWERCONTEXT_URL
        )
        self.scope_key = env.get("PAPERDESK_SCOPE_KEY") or "paperdesk-local-scope-v1"
        self.embedding_config = embedding_config or EmbeddingConfig.from_env(env)
        self.llm_config = llm_config or LLMConfig.from_env(env)
        self._store: PaperStore | None = None
        self._embedder: EmbeddingClient | None = None
        self._memory: PowerContextClient | None = None
        self._scope_id: str | None = None

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "seekdb"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    def embedder(self) -> EmbeddingClient | None:
        if not self.embedding_config.available:
            return None
        if self._embedder is None:
            self._embedder = EmbeddingClient(self.embedding_config)
        return self._embedder

    def store(self, *, embedding_dim: int | None = None) -> PaperStore:
        if self._store is not None:
            return self._store
        dimension = embedding_dim
        if dimension is None and self.embedding_config.available:
            dimension = self.embedding_config.dimension
        self._store = PaperStore(self.db_dir, embedding_dim=dimension)
        return self._store

    def memory(self) -> PowerContextClient:
        if self._memory is None:
            self._memory = PowerContextClient(self.powercontext_url)
        return self._memory

    def scope_id(self) -> str:
        if self._scope_id is None:
            scope = self.memory().ensure_scope(
                title="PaperDesk 研读工作台",
                summary="科研文献阅读目标、偏好、进度与会话摘要",
                idempotency_key=self.scope_key,
            )
            self._scope_id = scope.scope_id
        return self._scope_id

    def memory_state(self) -> MemoryState:
        return load_state(self.memory(), self.scope_id())

    def service(self) -> PaperDeskService:
        client = LLMClient(self.llm_config) if self.llm_config.available else None
        return PaperDeskService(
            self.store(),
            llm_config=self.llm_config,
            llm_client=client,
            embed_query=self.embed_query,
        )

    def embed_query(self, query: str) -> tuple[float, ...] | None:
        embedder = self.embedder()
        if embedder is None or self.store().embedding_dim is None:
            return None
        try:
            return embedder.embed([query], kind="query")[0]
        except EmbeddingError:
            return None

    def ingest(self, root: Path | str | None = None, *, embed: bool = True) -> IngestReport:
        library = Path(root) if root is not None else self.library_dir
        papers = parse_library(library)
        embeddings, error = self._embed_papers(papers) if embed else ({}, None)
        store = self.store(embedding_dim=self.embedding_config.dimension if embeddings else None)
        counts = store.replace_all(papers, embeddings=embeddings or None)
        db_dir = store.db_dir
        display = db_dir.relative_to(PACKAGE_ROOT) if db_dir.is_relative_to(PACKAGE_ROOT) else db_dir
        return IngestReport(
            documents=counts.get("documents", len(papers)),
            chunks=counts.get("chunks", 0),
            embedded=store.embedding_count(),
            embedding_error=error,
            db_dir=str(display),
        )

    def _embed_papers(
        self, papers: list[ParsedPaper]
    ) -> tuple[dict[str, tuple[float, ...]], str | None]:
        embedder = self.embedder()
        if embedder is None:
            return {}, None
        chunks = [chunk for paper in papers for chunk in paper.chunks]
        if not chunks:
            return {}, None
        try:
            vectors = embedder.embed([chunk.searchable_text for chunk in chunks], kind="db")
        except EmbeddingError as exc:
            return {}, str(exc)
        return {chunk.id: vector for chunk, vector in zip(chunks, vectors)}, None

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tag: str | None = None,
        year_min: int | None = None,
        status: str | None = None,
    ) -> tuple[list[SearchHit], str | None]:
        store = self.store()
        vector: tuple[float, ...] | None = None
        note: str | None = None
        embedder = self.embedder()
        if embedder is not None and store.embedding_dim is not None:
            try:
                vector = embedder.embed([query], kind="query")[0]
            except EmbeddingError as exc:
                note = f"向量检索不可用，已回退到全文检索：{exc}"
        hits = store.search(
            query,
            limit=limit,
            tag=tag,
            year_min=year_min,
            status=status,
            query_embedding=vector,
        )
        return hits, note

    def close(self) -> None:
        if self._store is not None:
            self._store.close()
            self._store = None
