from __future__ import annotations

from paperdesk.llm import (
    ChatCompletion,
    LLMConfig,
    LLMError,
    SynthesisResult,
    synthesize,
    validate_citations,
)
from paperdesk.models import Profile, SearchHit


def _hits() -> list[SearchHit]:
    return [
        SearchHit(
            chunk_id="chunk-a",
            paper_id="rag-survey",
            title="Retrieval Augmented Generation: A Survey",
            section="Abstract",
            path="rag-survey.md",
            start_line=9,
            end_line=10,
            text="Retrieval augmented generation combines a retriever with a generator.",
            score=0.4,
            tags=("rag", "retrieval"),
        ),
        SearchHit(
            chunk_id="chunk-b",
            paper_id="rerank-notes",
            title="Reranking for Retrieval Pipelines",
            section="Method",
            path="rerank-notes.md",
            start_line=12,
            end_line=13,
            text="A cross encoder scores query passage pairs and can be fused with lexical signals.",
            score=0.3,
            tags=("rerank",),
        ),
    ]


class FakeClient:
    def __init__(self, content: str | None = None, error: Exception | None = None) -> None:
        self.content = content
        self.error = error
        self.calls = 0

    def complete(self, messages) -> ChatCompletion:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return ChatCompletion(
            content=self.content or "",
            model="fake-model",
            latency_ms=12,
            request_payload={"messages": list(messages)},
            response_payload={"choices": [{"message": {"content": self.content}}]},
        )


def _config(mode: str = "auto", *, credentials: bool = True) -> LLMConfig:
    return LLMConfig(
        mode=mode,
        base_url="https://example.invalid/v1" if credentials else "",
        api_key="test-key" if credentials else "",
        model="test-model" if credentials else "",
    )


def test_extractive_mode_never_calls_model() -> None:
    client = FakeClient("不应被调用。")

    result = synthesize("rerank 有什么作用？", _hits(), profile=Profile(), config=_config("extractive"), client=client)

    assert client.calls == 0
    assert result.mode == "extractive"
    assert result.citations
    assert result.text.startswith("问题：")
    assert "[C1]" in result.text
    assert result.fallback_reason == "已配置为抽取式模式"


def test_missing_credentials_falls_back() -> None:
    result = synthesize("rerank 有什么作用？", _hits(), profile=Profile(), config=_config(credentials=False))

    assert result.mode == "extractive"
    assert result.fallback_reason == "缺少可用的 LLM 配置"


def test_valid_generated_answer_keeps_citations() -> None:
    answer = (
        "重排序在生成前细化候选列表，从而减少无关上下文的影响。[C2]"
        "检索增强生成把检索器与生成器结合起来。[C1]"
    )
    result = synthesize(
        "rerank 与 RAG 的关系？",
        _hits(),
        profile=Profile(),
        config=_config(),
        client=FakeClient(answer),
    )

    assert result.mode == "llm"
    assert result.text == answer
    assert [citation.index for citation in result.citations] == [1, 2]
    assert result.coverage == 1.0
    assert result.model == "fake-model"
    report = validate_citations(answer, _hits())
    assert report.unknown == ()


def test_unknown_citation_falls_back() -> None:
    result = synthesize(
        "rerank 有什么作用？",
        _hits(),
        profile=Profile(),
        config=_config(),
        client=FakeClient("这个结论引用了不存在的片段。[C9]"),
    )

    assert result.mode == "extractive"
    assert "[C9]" in (result.fallback_reason or "")
    assert "[C9]" not in result.text


def test_low_coverage_falls_back() -> None:
    answer = (
        "重排序可以减少无关上下文的影响，并提升最终答案的质量。[C2]"
        "这段解释没有任何原文出处，因此不能作为可信结论。"
        "还有一句话同样没有标注任何来源。"
    )
    result = synthesize(
        "rerank 有什么作用？",
        _hits(),
        profile=Profile(),
        config=_config(),
        client=FakeClient(answer),
    )

    assert result.mode == "extractive"
    assert "引用覆盖率" in (result.fallback_reason or "")


def test_model_error_falls_back() -> None:
    result = synthesize(
        "rerank 有什么作用？",
        _hits(),
        profile=Profile(),
        config=_config(),
        client=FakeClient(error=LLMError("timeout")),
    )

    assert result.mode == "extractive"
    assert "模型调用失败" in (result.fallback_reason or "")


def test_empty_hits_report_missing_evidence() -> None:
    result: SynthesisResult = synthesize(
        "不存在的问题",
        [],
        profile=Profile(),
        config=_config("extractive"),
    )

    assert result.mode == "extractive"
    assert result.citations == ()
    assert "没有检索到" in result.text
