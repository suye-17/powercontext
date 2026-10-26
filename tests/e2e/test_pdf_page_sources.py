import asyncio
import hashlib

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    GetMemoryEntryRequest,
    SubmitSourceObservation,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.sources import (
    TEXT_EVIDENCE_PROJECTION_KEY,
    SourceDefinitionRegistry,
    SourceObservation,
    TextEvidence,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.pdf_pages import (
    PDF_PAGE_SOURCE_DEFINITION,
    PDFDocumentInput,
    parse_pdf_document,
    search_pdf_pages,
)
from tests.pdf_support import make_pdf


class _EvidencePipeline:
    # Matches the public selftest_support.source_acceptance.EvidencePipeline
    # at dab240063bfcfa7917898dc44da4baadb3747bd0.
    async def extract(self, request, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="source-evidence",
                text=source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"],
                sources=(source,),
            )
            for source in request.sources
            if source.projection(TEXT_EVIDENCE_PROJECTION_KEY)["content"].strip()
        )


def test_pdf_observations_survive_retry_revision_and_runtime_restart(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pages.db'}"))
        registry = SourceDefinitionRegistry((PDF_PAGE_SOURCE_DEFINITION,))
        original = make_pdf("Revenue growth 123", "", "Operating margin 456")
        revised = make_pdf("Revenue growth 123", "", "Operating margin 456", title="Revision")
        pages = tuple(
            page
            for content in (original, revised)
            for page in parse_pdf_document(PDFDocumentInput(document_id="年度财报", content=content))
        )
        observations = tuple([project_source_for_transport(registry, await registry.resolve(page)) for page in pages])
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="PDF pages", summary="Evidence", idempotency_key="pdf")
            )
            await runtime.ingestion.register(manifest_for_definition(PDF_PAGE_SOURCE_DEFINITION))
            for observation in (*observations, observations[0], observations[3]):
                await runtime.ingestion.submit(
                    SubmitSourceObservation(scope_id=scope.scope_id, observation=observation)
                )

        async with open_builtin_runtime(config, candidate_pipeline=_EvidencePipeline()) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            for _ in range(len(observations) + 1):
                if not (await memory.flush()).processed:
                    break
            else:
                raise AssertionError("Source processing did not drain")  # noqa: TRY003
            assert not (await memory.flush()).processed

        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get(scope.scope_id)
            journal = await context.sources.journal.entries()
            stored: dict[str, SourceObservation] = {}
            for row in journal:
                assert isinstance(row.source, SourceObservation)
                stored[row.source_ref.source_id] = row.source
            assert len(journal) == 6
            assert stored == {observation.name: observation for observation in observations}
            for observation, page in zip(observations, pages, strict=True):
                restored = PDF_PAGE_SOURCE_DEFINITION.source_class.model_validate(stored[observation.name].payload)
                assert await registry.read(restored) == page
                projection = TextEvidence.model_validate(
                    stored[observation.name].projection(TEXT_EVIDENCE_PROJECTION_KEY)
                )
                assert projection.content == f"年度财报\n{page.text}"
                assert projection.metadata == {
                    "document_id": "年度财报",
                    "document_sha256": page.document_sha256,
                    "page_number": page.page_number,
                }

        async with open_builtin_runtime(config) as runtime:
            memory = runtime.memory.for_scope(scope.scope_id)
            citations = await search_pdf_pages(memory, "Revenue growth", limit=20)
            assert len(citations) == 2
            assert len(await search_pdf_pages(memory, "Revenue growth", limit=1)) == 1
            returned = []
            for citation in citations:
                record = await memory.get(GetMemoryEntryRequest(citation=citation))
                assert len(record.entry.sources) == 1
                source = stored[record.entry.sources[0].source_id]
                projection = TextEvidence.model_validate(source.projection(TEXT_EVIDENCE_PROJECTION_KEY))
                assert record.entry.text == projection.content.strip()
                restored = PDF_PAGE_SOURCE_DEFINITION.source_class.model_validate(source.payload)
                returned.append(await PDF_PAGE_SOURCE_DEFINITION.read(restored))
            assert {page.page_number for page in returned} == {1}
            assert {page.document_sha256 for page in returned} == {
                hashlib.sha256(original).hexdigest(),
                hashlib.sha256(revised).hexdigest(),
            }
            assert runtime.scopes is not None
            other = await runtime.scopes.create(ScopeDraft(title="Other", summary="Empty", idempotency_key="other"))
            assert await search_pdf_pages(runtime.memory.for_scope(other.scope_id), "Revenue growth", limit=20) == ()

    asyncio.run(scenario())
