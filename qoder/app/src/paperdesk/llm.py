from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .models import Profile, SearchHit

_CITATION = re.compile(r"\[C(\d+)\]")
_SENTENCE = re.compile(r"(?<=[。！？!?\n])|(?<=\.)\s")
_LEADING_CITATIONS = re.compile(r"^((?:\[C\d+\])+)\s*(.*)$", re.DOTALL)
_SYSTEM_PROMPT = (
    "你是 PaperDesk 的文献阅读助手。只能依据用户提供的原文片段回答，不得编造片段之外的事实。"
    "每个结论句末尾必须用 [C1]、[C2] 这样的编号标注出处，编号必须对应给定片段。"
    "证据不足时直接说明缺少哪些证据。用简体中文回答，保持简洁，不要输出思考过程。"
)


class LLMError(RuntimeError):
    pass


@dataclass(frozen=True)
class LLMConfig:
    mode: str = "auto"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout: float = 45.0
    temperature: float = 0.2

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> LLMConfig:
        values = env if env is not None else _environ()
        mode = (values.get("PAPERDESK_LLM_MODE") or "auto").strip().lower()
        return cls(
            mode=mode,
            base_url=(values.get("PAPERDESK_LLM_BASE_URL") or values.get("MINIMAX_BASE_URL") or "").strip(),
            api_key=(values.get("PAPERDESK_LLM_API_KEY") or values.get("MINIMAX_API_KEY") or "").strip(),
            model=(values.get("PAPERDESK_LLM_MODEL") or values.get("MINIMAX_MODEL") or "").strip(),
            timeout=float(values.get("PAPERDESK_LLM_TIMEOUT") or 45),
            temperature=float(values.get("PAPERDESK_LLM_TEMPERATURE") or 0.2),
        )

    @property
    def available(self) -> bool:
        return self.mode != "extractive" and bool(self.base_url and self.api_key and self.model)


@dataclass(frozen=True)
class Citation:
    index: int
    chunk_id: str
    paper_id: str
    title: str
    section: str
    path: str
    start_line: int
    end_line: int

    def as_json(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "marker": f"[C{self.index}]",
            "chunk_id": self.chunk_id,
            "paper_id": self.paper_id,
            "title": self.title,
            "section": self.section,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True)
class CitationReport:
    used: tuple[int, ...]
    unknown: tuple[int, ...]
    coverage: float
    sentence_count: int
    cited_sentence_count: int


@dataclass(frozen=True)
class ChatCompletion:
    content: str
    model: str
    latency_ms: int
    request_payload: dict[str, Any]
    response_payload: dict[str, Any]


@dataclass(frozen=True)
class SynthesisResult:
    text: str
    mode: str
    citations: tuple[Citation, ...]
    coverage: float
    model: str | None = None
    prompt_hash: str = ""
    latency_ms: int = 0
    fallback_reason: str | None = None
    cached: bool = False
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "mode": self.mode,
            "citations": [citation.as_json() for citation in self.citations],
            "coverage": round(self.coverage, 4),
            "model": self.model,
            "prompt_hash": self.prompt_hash,
            "latency_ms": self.latency_ms,
            "fallback_reason": self.fallback_reason,
            "cached": self.cached,
            "evidence": self.evidence,
        }


class LLMClient:
    def __init__(self, config: LLMConfig) -> None:
        if not (config.base_url and config.api_key and config.model):
            raise LLMError("LLM base URL, API key, and model are required")
        self.config = config

    def complete(self, messages: Sequence[Mapping[str, str]]) -> ChatCompletion:
        url = self.config.base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url = f"{url}/chat/completions"
        payload = {
            "model": self.config.model,
            "messages": list(messages),
            "temperature": self.config.temperature,
            "stream": False,
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.config.api_key}",
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                raw = response.read().decode("utf-8")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise LLMError(f"LLM request failed: {exc}") from exc
        latency_ms = int((time.perf_counter() - started) * 1000)
        try:
            parsed = json.loads(raw)
            content = parsed["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response: {raw[:200]}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLM returned an empty answer")
        return ChatCompletion(
            content=content.strip(),
            model=str(parsed.get("model") or self.config.model),
            latency_ms=latency_ms,
            request_payload=_sanitize_payload(payload),
            response_payload=parsed,
        )


def build_messages(
    question: str,
    hits: Sequence[SearchHit],
    *,
    max_chars: int = 1200,
) -> list[dict[str, str]]:
    blocks: list[str] = []
    for index, hit in enumerate(hits, start=1):
        text = hit.text[:max_chars]
        blocks.append(
            f"[C{index}] 标题：{hit.title}\n"
            f"章节：{hit.section}\n"
            f"位置：{hit.path}:{hit.start_line}-{hit.end_line}\n"
            f"原文：{text}"
        )
    user = "问题：" + question.strip()
    if blocks:
        user += "\n\n可用原文片段：\n\n" + "\n\n".join(blocks)
    else:
        user += "\n\n没有检索到任何原文片段。"
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def validate_citations(answer: str, hits: Sequence[SearchHit]) -> CitationReport:
    markers = [int(value) for value in _CITATION.findall(answer)]
    unknown = tuple(sorted({value for value in markers if value < 1 or value > len(hits)}))
    used = tuple(sorted({value for value in markers if 1 <= value <= len(hits)}))
    sentences = [part for part in _split_sentences(answer) if len(part) >= 12]
    cited = [sentence for sentence in sentences if _CITATION.search(sentence)]
    coverage = len(cited) / len(sentences) if sentences else 0.0
    return CitationReport(
        used=used,
        unknown=unknown,
        coverage=coverage,
        sentence_count=len(sentences),
        cited_sentence_count=len(cited),
    )


def synthesize(
    question: str,
    hits: Sequence[SearchHit],
    *,
    profile: Profile,
    config: LLMConfig | None = None,
    client: LLMClient | None = None,
    min_coverage: float = 0.5,
) -> SynthesisResult:
    config = config or LLMConfig.from_env()
    messages = build_messages(question, hits)
    prompt_hash = _prompt_hash(messages)

    if not hits:
        return _extractive(question, hits, profile, prompt_hash, "没有检索到可引用的原文片段")
    if config.mode == "extractive":
        return _extractive(question, hits, profile, prompt_hash, "已配置为抽取式模式")
    if not config.available:
        return _extractive(question, hits, profile, prompt_hash, "缺少可用的 LLM 配置")

    try:
        completion = (client or LLMClient(config)).complete(messages)
    except LLMError as exc:
        return _extractive(question, hits, profile, prompt_hash, f"模型调用失败：{exc}")

    report = validate_citations(completion.content, hits)
    if report.unknown:
        unknown = ", ".join(f"[C{value}]" for value in report.unknown)
        return _extractive(question, hits, profile, prompt_hash, f"模型引用了不存在的编号：{unknown}")
    if not report.used:
        return _extractive(question, hits, profile, prompt_hash, "模型回答没有引用任何原文片段")
    if report.coverage < min_coverage:
        return _extractive(
            question,
            hits,
            profile,
            prompt_hash,
            f"引用覆盖率 {report.coverage:.0%} 低于阈值 {min_coverage:.0%}",
        )
    citations = tuple(_citation(index, hits[index - 1]) for index in report.used)
    return SynthesisResult(
        text=completion.content,
        mode="llm",
        citations=citations,
        coverage=report.coverage,
        model=completion.model,
        prompt_hash=prompt_hash,
        latency_ms=completion.latency_ms,
        evidence={
            "request": completion.request_payload,
            "response": completion.response_payload,
            "citation_report": {
                "used": list(report.used),
                "unknown": list(report.unknown),
                "coverage": round(report.coverage, 4),
                "sentences": report.sentence_count,
                "cited_sentences": report.cited_sentence_count,
            },
        },
    )


def _extractive(
    question: str,
    hits: Sequence[SearchHit],
    profile: Profile,
    prompt_hash: str,
    reason: str,
) -> SynthesisResult:
    picked = list(hits[: max(1, min(profile.digest_sentences, len(hits)))]) if hits else []
    if not picked:
        text = f"问题：{question}\n\n没有检索到可引用的原文片段，无法给出有出处的回答。"
        return SynthesisResult(
            text=text,
            mode="extractive",
            citations=(),
            coverage=0.0,
            prompt_hash=prompt_hash,
            fallback_reason=reason,
        )
    display_reason = _CITATION.sub("无效编号", reason)
    lines = [f"问题：{question}", f"抽取式摘要（{display_reason}）："]
    for index, hit in enumerate(picked, start=1):
        lines.append(f"- [C{index}] 《{hit.title}》· {hit.section}：{_first_sentence(hit.text)}")
    lines.append("以上内容摘自论文笔记原文，编号与检索片段一一对应。")
    text = "\n".join(lines)
    citations = tuple(_citation(index, hit) for index, hit in enumerate(picked, start=1))
    report = validate_citations(text, hits)
    return SynthesisResult(
        text=text,
        mode="extractive",
        citations=citations,
        coverage=report.coverage,
        prompt_hash=prompt_hash,
        fallback_reason=reason,
    )


def _citation(index: int, hit: SearchHit) -> Citation:
    return Citation(
        index=index,
        chunk_id=hit.chunk_id,
        paper_id=hit.paper_id,
        title=hit.title,
        section=hit.section,
        path=hit.path,
        start_line=hit.start_line,
        end_line=hit.end_line,
    )


def _split_sentences(text: str) -> list[str]:
    merged: list[str] = []
    for raw in _SENTENCE.split(text):
        part = raw.strip()
        if not part:
            continue
        match = _LEADING_CITATIONS.match(part)
        if merged and match:
            merged[-1] += match.group(1)
            remainder = match.group(2).strip()
            if remainder:
                merged.append(remainder)
        else:
            merged.append(part)
    return merged


def _first_sentence(text: str) -> str:
    text = re.sub(r"^\s*##\s+[^\n]*\n?", "", text).strip()
    text = " ".join(text.split())
    match = re.search(r"[^.!?]{1,240}[.!?]", text)
    return match.group(0).strip() if match else text[:240]


def _prompt_hash(messages: Sequence[Mapping[str, str]]) -> str:
    joined = "\n".join(message["content"] for message in messages)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _sanitize_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    if "api_key" in sanitized:
        sanitized["api_key"] = "***"
    return sanitized


def _environ() -> Mapping[str, str]:
    import os

    return os.environ
