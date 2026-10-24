"""Single-statement native Memory candidates for embedded seekdb."""

from __future__ import annotations

from pyobvector import VECTOR
from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncConnection
from typing_extensions import override

from powercontext.builtin.artifacts.memory import (
    CapabilityNotSupportedError,
    EmbeddingProfile,
    MemorySearchChannels,
    MemorySearchRequest,
)
from powercontext.builtin.artifacts.memory.canonical import validate_embedding
from powercontext.builtin.persistence.memory_index import CompositeMemoryIndex, memory_channel_hits
from powercontext.builtin.persistence.oceanbase.memory_index import OceanBaseMemoryFTSIndex, OceanBaseMemoryVectorIndex
from powercontext.builtin.persistence.tags import memory_tag_parameters, memory_tag_sql

_HYBRID_SQL = """
SELECT * FROM (
    SELECT m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
           v.text, 0 AS channel, -MATCH(m.searchable_text) AGAINST (:query) AS channel_score,
           NULL AS distance
    FROM pc_memory_entry_heads AS m
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id
     AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
      AND MATCH(m.searchable_text) AGAINST (:query)
      {tag_filter}
    ORDER BY channel_score, m.memory_artifact_id, m.entry_id, m.entry_version_id
    LIMIT :candidate_limit
) AS lexical
UNION ALL
SELECT * FROM (
    SELECT {vector_hint} m.memory_artifact_id, m.head_revision, m.entry_id, m.entry_version_id,
           v.text, 1 AS channel, l2_distance(m.embedding, :query_vector) AS channel_score,
           l2_distance(m.embedding, :query_vector) AS distance
    FROM pc_memory_vector_entries AS m
    {vector_join}
    JOIN pc_memory_entry_versions AS v
      ON v.scope_id = m.scope_id
     AND v.memory_artifact_id = m.memory_artifact_id
     AND v.entry_version_id = m.entry_version_id
    WHERE m.scope_id = :scope_id
      AND m.memory_artifact_id IN :memory_artifact_ids
    ORDER BY l2_distance(m.embedding, :query_vector) APPROXIMATE
    LIMIT :candidate_limit
) AS semantic
ORDER BY channel, channel_score, memory_artifact_id, entry_id, entry_version_id
"""


_TAGGED_VECTOR_JOIN = """
JOIN (
    SELECT tags.artifact_id, tags.target_id
    FROM pc_artifact_tags AS tags
    WHERE tags.scope_id = :scope_id
      AND tags.family = 'memory'
      AND tags.artifact_id IN :memory_artifact_ids
      AND tags.target_type = 'memory_entry'
      AND tags.tag_key_hash IN :tag_hashes
      AND tags.tag_key IN :tag_keys
    GROUP BY tags.artifact_id, tags.target_id
    HAVING COUNT(*) >= :tag_minimum
) AS eligible
  ON eligible.artifact_id = m.memory_artifact_id
 AND eligible.target_id = m.entry_id
"""


class SeekDBMemoryIndex(CompositeMemoryIndex):
    """Reuse projection maintenance and single channels, combine hybrid reads."""

    def __init__(self, profile: EmbeddingProfile) -> None:
        self._vector = OceanBaseMemoryVectorIndex(profile)
        super().__init__(OceanBaseMemoryFTSIndex(), self._vector)
        self._search_sql = text(_HYBRID_SQL.format(tag_filter="", vector_join="", vector_hint="")).bindparams(
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )
        # A correlated tag predicate above the ANN scan can discard its bounded
        # candidates. Drive indexed vector lookups from eligible identities so
        # tag filtering precedes the channel's global top-k.
        self._tagged_search_sql = text(
            _HYBRID_SQL.format(
                tag_filter=memory_tag_sql("m"),
                vector_join=_TAGGED_VECTOR_JOIN,
                vector_hint="/*+ LEADING(eligible m v) USE_NL(m) */",
            )
        ).bindparams(
            bindparam("memory_artifact_ids", expanding=True),
            bindparam("tag_keys", expanding=True),
            bindparam("tag_hashes", expanding=True),
            bindparam("query_vector", type_=VECTOR(profile.dimension)),
        )

    @override
    async def search(
        self,
        connection: AsyncConnection,
        scope_id: str,
        request: MemorySearchRequest,
        /,
    ) -> MemorySearchChannels:
        if request.mode != "hybrid":
            return await super().search(connection, scope_id, request)
        profile = self._vector.profile
        if request.embedding_profile != profile or request.query_vector is None:
            raise CapabilityNotSupportedError("embedding-profile")
        if not await self._vector.vector_complete(connection, scope_id, request.memories, profile):
            raise CapabilityNotSupportedError("vector")
        rows = (
            (
                await connection.execute(
                    self._search_sql if request.tag_filter is None else self._tagged_search_sql,
                    {
                        "scope_id": scope_id,
                        "memory_artifact_ids": tuple(memory.artifact_id for memory in request.memories),
                        "query": request.analyzed_query,
                        "query_vector": validate_embedding(request.query_vector, dimension=profile.dimension),
                        "candidate_limit": request.candidate_limit,
                        **memory_tag_parameters(request.tag_filter),
                    },
                )
            )
            .mappings()
            .all()
        )
        return MemorySearchChannels(
            fts=memory_channel_hits((row for row in rows if row["channel"] == 0), request.memories),
            vector=memory_channel_hits((row for row in rows if row["channel"] == 1), request.memories),
        )
