from __future__ import annotations

import functools
import hashlib
import re
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path

import pymysql
import pylibseekdb as seekdb

from .models import VALID_STATUSES, Chunk, Paper, ParsedPaper, SearchHit

_DOCUMENT_COLUMNS = """
    c.id, c.paper_id, d.title, c.section, d.path, c.start_line, c.end_line,
    c.text, d.tags, c.order_index
"""
_DOCUMENT_JOIN = "FROM pd_chunks c JOIN pd_documents d ON d.id = c.document_id"
_TOKEN = re.compile(r"[a-z0-9]+")
_EMBEDDING_DIM_KEY = "embedding_dimension"
# Measured on the sample query set: equal-weight RRF let generic embedding
# neighbours outrank exact term matches, while 8:1 kept the lexical baseline
# and still admitted vector-only candidates behind it.
_KEYWORD_WEIGHT = 8.0


class StoreError(RuntimeError):
    pass


def _serialized(method):
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


class PaperStore:
    def __init__(self, db_dir: Path | str, *, embedding_dim: int | None = None) -> None:
        self.db_dir = Path(db_dir)
        self.db_dir.mkdir(parents=True, exist_ok=True)
        # Sync FastAPI endpoints share one store across threadpool workers; a
        # single pymysql cursor cannot interleave execute/fetch calls.
        self._lock = threading.RLock()
        self._expected_dim = embedding_dim
        self._instance = seekdb.open(str(self.db_dir.resolve()))
        self._conn: pymysql.connections.Connection | None = None
        self._has_embedding_column = False
        try:
            options = self._instance.connection_options()
            self._conn = pymysql.connect(
                **options,
                database="test",
                charset="utf8mb4",
                autocommit=False,
                connect_timeout=10,
                read_timeout=30,
                write_timeout=30,
            )
            self._cursor = self._conn.cursor()
            self._prepare_schema()
            if embedding_dim is not None:
                self._ensure_embedding_column(embedding_dim)
            stored_dim = self.embedding_dim
            if embedding_dim is not None and stored_dim is not None and stored_dim != embedding_dim:
                raise StoreError(
                    f"embedding dimension mismatch: store has {stored_dim}, requested {embedding_dim}"
                )
        except Exception:
            self.close()
            raise

    def __enter__(self) -> PaperStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @_serialized
    def close(self) -> None:
        connection = getattr(self, "_conn", None)
        if connection is not None:
            try:
                connection.close()
            finally:
                self._conn = None
        instance = getattr(self, "_instance", None)
        if instance is not None:
            try:
                if not instance.closed:
                    instance.close()
            finally:
                self._instance = None

    @property
    @_serialized
    def embedding_dim(self) -> int | None:
        value = self._meta(_EMBEDDING_DIM_KEY)
        return int(value) if value is not None else None

    @_serialized
    def embedding_count(self) -> int:
        if not self._has_embedding_column:
            return 0
        row = self._fetchone("SELECT COUNT(*) FROM pd_chunks WHERE embedding IS NOT NULL")
        return int(row[0]) if row else 0

    @_serialized
    def replace_all(
        self,
        papers: Sequence[ParsedPaper],
        *,
        embeddings: Mapping[str, Sequence[float]] | None = None,
    ) -> dict[str, int]:
        dimension = self._validate_embeddings(papers, embeddings)
        if dimension is not None:
            self._ensure_embedding_column(dimension)

        try:
            self._cursor.execute("DELETE FROM pd_chunks")
            self._cursor.execute("DELETE FROM pd_documents")
            for parsed in papers:
                paper = parsed.paper
                document_id = _document_id(paper.paper_id)
                self._cursor.execute(
                    """
                    INSERT INTO pd_documents
                        (id, paper_id, title, authors, year, tags, status, path,
                         content_hash, content, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        document_id,
                        paper.paper_id,
                        paper.title,
                        paper.authors,
                        paper.year,
                        ",".join(paper.tags),
                        paper.status,
                        paper.path,
                        paper.content_hash,
                        paper.content,
                        paper.updated_at,
                    ),
                )
                for chunk in parsed.chunks:
                    embedding = embeddings.get(chunk.id) if embeddings else None
                    if self._has_embedding_column:
                        self._cursor.execute(
                            """
                            INSERT INTO pd_chunks
                                (id, document_id, paper_id, section, order_index,
                                 start_line, end_line, text, searchable_text, embedding)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                chunk.id,
                                chunk.document_id,
                                chunk.paper_id,
                                chunk.section,
                                chunk.order_index,
                                chunk.start_line,
                                chunk.end_line,
                                chunk.text,
                                chunk.searchable_text,
                                _vector_literal(embedding),
                            ),
                        )
                    else:
                        self._cursor.execute(
                            """
                            INSERT INTO pd_chunks
                                (id, document_id, paper_id, section, order_index,
                                 start_line, end_line, text, searchable_text)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                chunk.id,
                                chunk.document_id,
                                chunk.paper_id,
                                chunk.section,
                                chunk.order_index,
                                chunk.start_line,
                                chunk.end_line,
                                chunk.text,
                                chunk.searchable_text,
                            ),
                        )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return self.counts()

    @_serialized
    def reset(self) -> None:
        self._cursor.execute("DELETE FROM pd_chunks")
        self._cursor.execute("DELETE FROM pd_documents")
        self._conn.commit()

    @_serialized
    def counts(self) -> dict[str, int]:
        documents = self._fetchone("SELECT COUNT(*) FROM pd_documents")
        chunks = self._fetchone("SELECT COUNT(*) FROM pd_chunks")
        return {
            "documents": int(documents[0]) if documents else 0,
            "chunks": int(chunks[0]) if chunks else 0,
        }

    @_serialized
    def schema(self) -> str:
        statements: list[str] = []
        for table in ("pd_documents", "pd_chunks", "pd_meta"):
            row = self._fetchone(f"SHOW CREATE TABLE {table}")
            if row and len(row) > 1:
                statements.append(str(row[1]).rstrip(";") + ";")
        return "\n\n".join(statements)

    @_serialized
    def list_papers(self) -> list[Paper]:
        rows = self._fetchall(
            """
            SELECT paper_id, title, authors, year, tags, status, path,
                   content, content_hash, updated_at
            FROM pd_documents
            ORDER BY paper_id
            """
        )
        return [_paper_from_row(row) for row in rows]

    @_serialized
    def get_paper(self, paper_id: str) -> Paper | None:
        row = self._fetchone(
            """
            SELECT paper_id, title, authors, year, tags, status, path,
                   content, content_hash, updated_at
            FROM pd_documents
            WHERE paper_id = %s
            """,
            (paper_id,),
        )
        return _paper_from_row(row) if row else None

    @_serialized
    def get_chunks(self, paper_id: str) -> tuple[Chunk, ...]:
        rows = self._fetchall(
            """
            SELECT id, document_id, paper_id, section, order_index,
                   start_line, end_line, text, searchable_text
            FROM pd_chunks
            WHERE paper_id = %s
            ORDER BY order_index
            """,
            (paper_id,),
        )
        return tuple(
            Chunk(
                id=row[0],
                document_id=row[1],
                paper_id=row[2],
                section=row[3],
                order_index=int(row[4]),
                start_line=int(row[5]),
                end_line=int(row[6]),
                text=row[7],
                searchable_text=row[8],
            )
            for row in rows
        )

    @_serialized
    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        tag: str | None = None,
        year_min: int | None = None,
        status: str | None = None,
        query_embedding: Sequence[float] | None = None,
        rrf_k: int = 60,
    ) -> list[SearchHit]:
        normalized = " ".join(query.strip().lower().split())
        if not normalized:
            return []
        limit = max(1, min(int(limit), 100))
        filters, filter_params = self._filters(tag=tag, year_min=year_min, status=status)
        rows, keyword_scores = self._keyword_hits(normalized, filters, filter_params, limit)

        if query_embedding is None:
            ranked = sorted(
                keyword_scores.items(),
                key=lambda item: (-item[1], _row_key(rows[item[0]])),
            )[:limit]
            return [
                _hit(rows[chunk_id], score=score, fts_score=score)
                for chunk_id, score in ranked
            ]

        vector_scores = self._vector_hits(
            query_embedding, filters, filter_params, limit, rows
        )
        fused = _rrf_fuse(keyword_scores, vector_scores, rrf_k)
        ranked = sorted(fused.items(), key=lambda item: (-item[1], _row_key(rows[item[0]])))
        return [
            _hit(
                rows[chunk_id],
                score=score,
                vector_score=vector_scores.get(chunk_id),
                fts_score=keyword_scores.get(chunk_id, 0.0),
            )
            for chunk_id, score in ranked[:limit]
        ]

    def _prepare_schema(self) -> None:
        # No-op DDL on every CLI start raced with the embedded server's startup
        # housekeeping and could wedge the connection; only create when missing.
        tables = {row[0] for row in self._fetchall("SHOW TABLES")}
        if not {"pd_documents", "pd_chunks", "pd_meta"} <= tables:
            self._ensure_schema()
        self._has_embedding_column = bool(self._fetchall("SHOW COLUMNS FROM pd_chunks LIKE 'embedding'"))

    def _ensure_schema(self) -> None:
        self._cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pd_documents (
                id CHAR(32) PRIMARY KEY,
                paper_id VARCHAR(128) NOT NULL,
                title VARCHAR(512) NOT NULL,
                authors TEXT NOT NULL,
                year INT NOT NULL,
                tags VARCHAR(255) NOT NULL,
                status VARCHAR(16) NOT NULL,
                path TEXT NOT NULL,
                content_hash CHAR(64) NOT NULL,
                content LONGTEXT NOT NULL,
                updated_at VARCHAR(64) NOT NULL,
                UNIQUE KEY ux_paper (paper_id),
                FULLTEXT KEY ft_title (title)
            ) DEFAULT CHARSET=utf8mb4
            """
        )
        self._cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pd_chunks (
                id CHAR(32) PRIMARY KEY,
                document_id CHAR(32) NOT NULL,
                paper_id VARCHAR(128) NOT NULL,
                section VARCHAR(255) NOT NULL,
                order_index INT NOT NULL,
                start_line INT NOT NULL,
                end_line INT NOT NULL,
                text LONGTEXT NOT NULL,
                searchable_text LONGTEXT NOT NULL,
                FULLTEXT KEY ft_searchable (searchable_text),
                KEY ix_paper (paper_id),
                KEY ix_document (document_id)
            ) DEFAULT CHARSET=utf8mb4
            """
        )
        self._cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS pd_meta (
                meta_key VARCHAR(64) PRIMARY KEY,
                meta_value VARCHAR(255) NOT NULL
            ) DEFAULT CHARSET=utf8mb4
            """
        )
        self._conn.commit()

    def _ensure_embedding_column(self, dimension: int) -> None:
        stored = self.embedding_dim
        if stored is not None and stored != dimension:
            raise StoreError(f"embedding dimension mismatch: store has {stored}, requested {dimension}")
        if self._has_embedding_column:
            if stored is None:
                self._set_meta(_EMBEDDING_DIM_KEY, str(dimension))
            if not self._has_vector_index():
                self._create_vector_index()
            return
        self._cursor.execute(f"ALTER TABLE pd_chunks ADD COLUMN embedding VECTOR({int(dimension)}) NULL")
        self._create_vector_index()
        self._has_embedding_column = True
        self._set_meta(_EMBEDDING_DIM_KEY, str(dimension))

    def _has_vector_index(self) -> bool:
        rows = self._fetchall("SHOW INDEX FROM pd_chunks")
        return any(row[2] == "ix_chunk_embedding" for row in rows)

    def _create_vector_index(self) -> None:
        self._cursor.execute(
            "CREATE VECTOR INDEX ix_chunk_embedding ON pd_chunks (embedding) "
            "WITH (distance=cosine, type=hnsw, lib=vsag)"
        )
        self._conn.commit()

    def _validate_embeddings(
        self,
        papers: Sequence[ParsedPaper],
        embeddings: Mapping[str, Sequence[float]] | None,
    ) -> int | None:
        if embeddings is None:
            return None
        known = {chunk.id for parsed in papers for chunk in parsed.chunks}
        unknown = set(embeddings) - known
        if unknown:
            raise StoreError(f"embeddings provided for unknown chunks: {sorted(unknown)[:3]}")
        dimensions = {len(values) for values in embeddings.values()}
        if not dimensions:
            return None
        if len(dimensions) > 1:
            raise StoreError(f"inconsistent embedding dimensions: {sorted(dimensions)}")
        dimension = dimensions.pop()
        if dimension <= 0:
            raise StoreError("embedding dimension must be positive")
        if self._expected_dim is not None and self._expected_dim != dimension:
            raise StoreError(
                f"embedding dimension mismatch: expected {self._expected_dim}, got {dimension}"
            )
        for chunk_id, values in embeddings.items():
            for value in values:
                if not isinstance(value, int | float):
                    raise StoreError(f"embedding for {chunk_id} contains a non-numeric value")
        return dimension

    def _keyword_hits(
        self,
        query: str,
        filters: list[str],
        filter_params: list[object],
        limit: int,
    ) -> tuple[dict[str, tuple], dict[str, float]]:
        where = " AND ".join(["MATCH(c.searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE)"] + filters)
        params: list[object] = [query, query, *filter_params, max(limit * 5, 50)]
        rows = self._fetchall(
            f"""
            SELECT {_DOCUMENT_COLUMNS},
                   MATCH(c.searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS fts
            {_DOCUMENT_JOIN}
            WHERE {where}
            ORDER BY fts DESC
            LIMIT %s
            """,
            tuple(params),
        )
        scores: dict[str, float] = {}
        by_id: dict[str, tuple] = {}
        for row in rows:
            chunk_id = row[0]
            by_id[chunk_id] = row[:10]
            scores[chunk_id] = float(row[10] or 0.0)

        for row, boost in self._identifier_matches(query, filters, filter_params):
            chunk_id = row[0]
            by_id.setdefault(chunk_id, row)
            scores[chunk_id] = scores.get(chunk_id, 0.0) + boost
        return by_id, scores

    def _identifier_matches(
        self,
        query: str,
        filters: list[str],
        filter_params: list[object],
    ) -> list[tuple[tuple, float]]:
        tokens = [token for token in _TOKEN.findall(query) if len(token) >= 2]
        if not tokens:
            return []
        clauses = [
            "%s LIKE CONCAT('%%', LOWER(c.paper_id), '%%')",
            "%s LIKE CONCAT('%%', LOWER(c.section), '%%')",
        ]
        params: list[object] = [query, query]
        placeholders = ", ".join(["%s"] * len(tokens))
        clauses.append(f"LOWER(c.section) IN ({placeholders})")
        params.extend(tokens)
        for token in tokens:
            clauses.append("FIND_IN_SET(%s, d.tags)")
            params.append(token)
        where = " AND ".join([f"({' OR '.join(clauses)})", *filters])
        rows = self._fetchall(
            f"SELECT {_DOCUMENT_COLUMNS} {_DOCUMENT_JOIN} WHERE {where} LIMIT 200",
            tuple([*params, *filter_params]),
        )
        token_set = set(tokens)
        matches: list[tuple[tuple, float]] = []
        for row in rows:
            paper_id = str(row[1]).lower()
            section = str(row[3]).lower()
            tags = {tag for tag in str(row[8]).split(",") if tag}
            boost = 0.0
            if paper_id and paper_id in query:
                boost += 1.0
            if section and section in query:
                boost += 0.5
            tag_hits = len(tags & token_set)
            if tag_hits:
                boost += min(0.3 * tag_hits, 0.6)
            if boost:
                matches.append((row, boost))
        return matches

    def _vector_hits(
        self,
        query_embedding: Sequence[float],
        filters: list[str],
        filter_params: list[object],
        limit: int,
        rows: dict[str, tuple],
    ) -> dict[str, float]:
        dimension = self.embedding_dim
        if dimension is None:
            raise StoreError("store has no embeddings; ingest with embeddings before vector search")
        if len(query_embedding) != dimension:
            raise StoreError(
                f"query embedding has dimension {len(query_embedding)}, store expects {dimension}"
            )
        where = " AND ".join(["c.embedding IS NOT NULL", *filters])
        fetched = self._fetchall(
            f"""
            SELECT {_DOCUMENT_COLUMNS}, cosine_distance(c.embedding, %s) AS distance
            {_DOCUMENT_JOIN}
            WHERE {where}
            ORDER BY distance ASC
            LIMIT %s
            """,
            tuple([_vector_literal(query_embedding), *filter_params, max(limit * 5, 50)]),
        )
        scores: dict[str, float] = {}
        for row in fetched:
            chunk_id = row[0]
            rows.setdefault(chunk_id, row[:10])
            distance = float(row[10])
            scores[chunk_id] = max(0.0, min(1.0, 1.0 - distance))
        return scores

    def _filters(
        self,
        *,
        tag: str | None,
        year_min: int | None,
        status: str | None,
    ) -> tuple[list[str], list[object]]:
        clauses: list[str] = []
        params: list[object] = []
        if tag:
            normalized = " ".join(tag.strip().lower().split())
            if normalized:
                clauses.append("FIND_IN_SET(%s, d.tags)")
                params.append(normalized)
        if year_min is not None:
            clauses.append("d.year >= %s")
            params.append(int(year_min))
        if status:
            if status not in VALID_STATUSES:
                raise ValueError(f"invalid status filter {status!r}")
            clauses.append("d.status = %s")
            params.append(status)
        return clauses, params

    def _set_meta(self, key: str, value: str) -> None:
        self._cursor.execute("DELETE FROM pd_meta WHERE meta_key = %s", (key,))
        self._cursor.execute("INSERT INTO pd_meta (meta_key, meta_value) VALUES (%s, %s)", (key, value))
        self._conn.commit()

    def _meta(self, key: str) -> str | None:
        row = self._fetchone("SELECT meta_value FROM pd_meta WHERE meta_key = %s", (key,))
        return str(row[0]) if row else None

    def _fetchall(self, sql: str, params: tuple = ()) -> list[tuple]:
        if params:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return list(self._cursor.fetchall())

    def _fetchone(self, sql: str, params: tuple = ()) -> tuple | None:
        if params:
            self._cursor.execute(sql, params)
        else:
            self._cursor.execute(sql)
        return self._cursor.fetchone()


def _document_id(paper_id: str) -> str:
    return hashlib.sha256(paper_id.encode("utf-8")).hexdigest()[:32]


def _paper_from_row(row: tuple) -> Paper:
    return Paper(
        paper_id=row[0],
        title=row[1],
        authors=row[2],
        year=int(row[3]),
        tags=tuple(tag for tag in str(row[4]).split(",") if tag),
        status=row[5],
        path=row[6],
        content=row[7],
        content_hash=row[8],
        updated_at=row[9],
    )


def _hit(
    row: tuple,
    *,
    score: float,
    vector_score: float | None = None,
    fts_score: float = 0.0,
) -> SearchHit:
    return SearchHit(
        chunk_id=row[0],
        paper_id=row[1],
        title=row[2],
        section=row[3],
        path=row[4],
        start_line=int(row[5]),
        end_line=int(row[6]),
        text=row[7],
        score=round(score, 6),
        vector_score=round(vector_score, 6) if vector_score is not None else None,
        tags=tuple(tag for tag in str(row[8]).split(",") if tag),
        fts_score=round(fts_score, 6),
    )


def _row_key(row: tuple) -> tuple[str, int]:
    return str(row[1]), int(row[9])


def _vector_literal(values: Sequence[float] | None) -> str | None:
    if values is None:
        return None
    return "[" + ",".join(f"{float(value):.8g}" for value in values) + "]"


def _rrf_fuse(
    keyword_scores: Mapping[str, float],
    vector_scores: Mapping[str, float],
    k: int,
    *,
    keyword_weight: float = _KEYWORD_WEIGHT,
) -> dict[str, float]:
    fused: dict[str, float] = {}
    keyword_ranked = sorted(keyword_scores.items(), key=lambda item: -item[1])
    for rank, (chunk_id, _score) in enumerate(keyword_ranked, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + keyword_weight / (k + rank)
    vector_ranked = sorted(vector_scores.items(), key=lambda item: -item[1])
    for rank, (chunk_id, _score) in enumerate(vector_ranked, start=1):
        fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return fused
