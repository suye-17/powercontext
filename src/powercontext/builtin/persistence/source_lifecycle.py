"""Transaction-bound persistence for scope-local source lifecycle graphs."""

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import ColumnElement

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.codec import stored_bytes
from powercontext.builtin.persistence.tables import (
    SOURCE_LIFECYCLE_STATES_TABLE,
    SOURCE_LIFECYCLE_UNAVAILABLE_TOPICS_TABLE,
)


class SourceLifecycleRepository:
    """Store the versioned graph without interpreting its domain rules."""

    async def load(self, connection: AsyncConnection, scope_id: str, /) -> bytes | None:
        payload = await connection.scalar(
            select(SOURCE_LIFECYCLE_STATES_TABLE.c.payload).where(SOURCE_LIFECYCLE_STATES_TABLE.c.scope_id == scope_id)
        )
        return None if payload is None else stored_bytes(payload, column="source_lifecycle.payload")

    async def save(self, connection: AsyncConnection, scope_id: str, payload: bytes, /) -> None:
        table = SOURCE_LIFECYCLE_STATES_TABLE
        existing = await connection.scalar(select(table.c.scope_id).where(table.c.scope_id == scope_id))
        if existing is None:
            await connection.execute(insert(table).values(scope_id=scope_id, payload=payload))
        else:
            await connection.execute(update(table).where(table.c.scope_id == scope_id).values(payload=payload))

    async def set_unavailable_topics(
        self, connection: AsyncConnection, scope_id: str, references: tuple[ArtifactRef, ...], /
    ) -> None:
        table = SOURCE_LIFECYCLE_UNAVAILABLE_TOPICS_TABLE
        rows = (
            await connection.execute(
                select(table.c.family, table.c.artifact_id, table.c.revision).where(table.c.scope_id == scope_id)
            )
        ).all()
        existing = {(str(row.family), str(row.artifact_id), int(row.revision)) for row in rows}
        desired = {(ref.family, ref.artifact_id, ref.revision) for ref in references}
        for family, artifact_id, revision in existing - desired:
            await connection.execute(
                delete(table).where(
                    table.c.scope_id == scope_id,
                    table.c.family == family,
                    table.c.artifact_id == artifact_id,
                    table.c.revision == revision,
                )
            )
        additions = [
            {"scope_id": scope_id, "family": family, "artifact_id": artifact_id, "revision": revision}
            for family, artifact_id, revision in sorted(desired - existing)
        ]
        if additions:
            await connection.execute(insert(table), additions)


def topic_is_available(
    scope_id: str, artifact_id: ColumnElement[str], revision: ColumnElement[int]
) -> ColumnElement[bool]:
    """Exclude only exact Topic references withdrawn in this scope."""

    table = SOURCE_LIFECYCLE_UNAVAILABLE_TOPICS_TABLE
    return (
        ~select(table.c.artifact_id)
        .where(
            table.c.scope_id == scope_id,
            table.c.family == "topic-memory",
            table.c.artifact_id == artifact_id,
            table.c.revision == revision,
        )
        .correlate_except(table)
        .exists()
    )
