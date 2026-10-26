from __future__ import annotations

from pathlib import Path
from typing import Any

import pymysql
import pylibseekdb as seekdb

from .models import Chunk, Document

_DDL = (
    """CREATE TABLE IF NOT EXISTS pc_corpus_documents (
        id CHAR(64) PRIMARY KEY,
        path TEXT NOT NULL,
        kind VARCHAR(16) NOT NULL,
        title TEXT NOT NULL,
        content_hash CHAR(64) NOT NULL,
        content LONGTEXT NOT NULL,
        updated_at VARCHAR(64) NOT NULL,
        UNIQUE KEY ux_path (path(190))
    ) DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS pc_corpus_chunks (
        id CHAR(64) PRIMARY KEY,
        document_id CHAR(64) NOT NULL,
        kind VARCHAR(16) NOT NULL,
        spec_id VARCHAR(64) NULL,
        symbol VARCHAR(255) NULL,
        test_name VARCHAR(255) NULL,
        start_line INT NOT NULL,
        end_line INT NOT NULL,
        text LONGTEXT NOT NULL,
        searchable_text LONGTEXT NOT NULL,
        FULLTEXT KEY ft_searchable (searchable_text),
        KEY ix_kind_spec (kind, spec_id),
        KEY ix_kind_symbol (kind, symbol(190)),
        KEY ix_kind_test (kind, test_name(190))
    ) DEFAULT CHARSET=utf8mb4""",
)

_CHUNK_COLUMNS = (
    "c.id, c.document_id, c.kind, c.spec_id, c.symbol, c.test_name, "
    "c.start_line, c.end_line, c.text, d.path"
)

_SEARCH_SQL = f"""
SELECT {_CHUNK_COLUMNS},
       MATCH(c.searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE) AS score
FROM pc_corpus_chunks c
JOIN pc_corpus_documents d ON d.id = c.document_id
WHERE c.kind = %s
  AND MATCH(c.searchable_text) AGAINST (%s IN NATURAL LANGUAGE MODE)
ORDER BY score DESC, d.path, c.start_line
LIMIT %s
"""

_EXACT_SQL_TEMPLATE = f"""
SELECT {_CHUNK_COLUMNS}, 0.0 AS score
FROM pc_corpus_chunks c
JOIN pc_corpus_documents d ON d.id = c.document_id
WHERE c.kind = %s AND c.{{field}} = %s
ORDER BY d.path, c.start_line
LIMIT %s
"""

_EXACT_FIELDS = {"spec_id", "symbol", "test_name"}


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path.mkdir(parents=True, exist_ok=True)
        self.instance = seekdb.open(db_dir=str(self.db_path))
        options = dict(self.instance.connection_options())
        options["init_command"] = "SET autocommit = 0"
        options.setdefault("charset", "utf8mb4")
        self.connection = pymysql.connect(database="test", autocommit=False, **options)
        self.initialize()

    def initialize(self) -> None:
        cursor = self.connection.cursor()
        try:
            for statement in _DDL:
                cursor.execute(statement)
            self.connection.commit()
        finally:
            cursor.close()

    def close(self) -> None:
        self.connection.close()
        self.instance.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _rollback(self) -> None:
        rollback = getattr(self.connection, "rollback", None)
        if callable(rollback):
            rollback()

    def replace_all(self, documents: list[Document], chunks: list[Chunk]) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute("DELETE FROM pc_corpus_chunks")
            cursor.execute("DELETE FROM pc_corpus_documents")
            for document in documents:
                cursor.execute(
                    "INSERT INTO pc_corpus_documents "
                    "(id, path, kind, title, content_hash, content, updated_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        document.id,
                        document.path,
                        document.kind,
                        document.title,
                        document.content_hash,
                        document.content,
                        document.updated_at,
                    ),
                )
            for chunk in chunks:
                cursor.execute(
                    "INSERT INTO pc_corpus_chunks "
                    "(id, document_id, kind, spec_id, symbol, test_name, start_line, end_line, text, searchable_text) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        chunk.id,
                        chunk.document_id,
                        chunk.kind,
                        chunk.spec_id,
                        chunk.symbol,
                        chunk.test_name,
                        chunk.start_line,
                        chunk.end_line,
                        chunk.text,
                        chunk.searchable_text,
                    ),
                )
            self.connection.commit()
        except Exception:
            self._rollback()
            raise
        finally:
            cursor.close()

    def reset(self) -> None:
        cursor = self.connection.cursor()
        try:
            cursor.execute("DELETE FROM pc_corpus_chunks")
            cursor.execute("DELETE FROM pc_corpus_documents")
            self.connection.commit()
        except Exception:
            self._rollback()
            raise
        finally:
            cursor.close()

    def counts(self) -> dict[str, int]:
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT kind, COUNT(*) FROM pc_corpus_chunks GROUP BY kind ORDER BY kind")
            return {str(kind): int(count) for kind, count in cursor.fetchall()}
        finally:
            cursor.close()

    @staticmethod
    def _row_to_chunk(row: tuple[Any, ...]) -> Chunk:
        return Chunk(
            id=str(row[0]),
            document_id=str(row[1]),
            kind=str(row[2]),
            spec_id=None if row[3] is None else str(row[3]),
            symbol=None if row[4] is None else str(row[4]),
            test_name=None if row[5] is None else str(row[5]),
            start_line=int(row[6]),
            end_line=int(row[7]),
            text=str(row[8]),
            path=str(row[9]),
            title="",
            searchable_text="",
        )

    def search(self, kind: str, query: str, limit: int) -> list[tuple[Chunk, float]]:
        if not query.strip():
            return []
        cursor = self.connection.cursor()
        try:
            cursor.execute(_SEARCH_SQL, (query, kind, query, limit))
            return [(self._row_to_chunk(row[:10]), float(row[10])) for row in cursor.fetchall()]
        finally:
            cursor.close()

    def find_exact(self, kind: str, field: str, value: str, limit: int) -> list[Chunk]:
        if field not in _EXACT_FIELDS:
            raise ValueError(f"unsupported exact field: {field}")
        cursor = self.connection.cursor()
        try:
            cursor.execute(_EXACT_SQL_TEMPLATE.format(field=field), (kind, value, limit))
            return [self._row_to_chunk(row[:10]) for row in cursor.fetchall()]
        finally:
            cursor.close()
