"""Observable contract for query-driven original evidence selection."""

from __future__ import annotations

import json
from itertools import pairwise

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import (
    MemoryEntryAddress,
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedMemoryCandidates,
)
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY


def test_evidence_request_is_strict_opt_in() -> None:
    assert PrepareContextRequest(query="query").evidence_selection is False
    assert PrepareContextRequest(query="query", evidence_selection=False).evidence_selection is False
    assert PrepareContextRequest(query="query", evidence_selection=True).evidence_selection is True


@pytest.mark.parametrize("value", [None, 0, 1, "true", "false"])
def test_evidence_request_rejects_non_boolean_flags(value) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "query", "evidence_selection": value})


@pytest.mark.parametrize("options", [{"assembly": {}}, {"include_code": True}])
def test_evidence_request_rejects_conflicting_formats(options) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "query", "evidence_selection": True, **options})
    assert PrepareContextRequest.model_validate({"query": "query", "evidence_selection": False, **options})


def test_context_build_has_empty_fragments_by_default() -> None:
    build = PreparedContextBuild(context=PreparedContextBuilder().empty(), origins=())
    assert build.fragments == ()


REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


def hit(text: str, entry_id: str = "entry", ref: ArtifactRef = REF) -> MemoryHit:
    return MemoryHit(
        memory_ref=ref, entry_id=entry_id, entry_version_id=f"{entry_id}-v1", text=text, score=1.0, matched_by=("fts",)
    )


def select(text: str, query: str, budget: int = 1400):
    return PreparedContextBuilder().build_result(
        scope_id="scope",
        memory_ref=REF,
        hits=(hit(text),),
        request=PrepareContextRequest(query=query, max_bytes=budget, evidence_selection=True),
    )


def assert_exact(build, originals):
    context = build.context
    assert context.content_bytes == len((context.content or "").encode("utf-8"))
    assert len(build.fragments) == len(build.origins)
    if context.content is None:
        assert build.fragments == build.origins == ()
        return
    assert context.content.startswith(TRUST_POLICY)
    assert context.content.endswith("END_POWERCONTEXT_PREPARED_CONTEXT_V1")
    envelope = json.loads(
        context.content.split("BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1\n", 1)[1].rsplit(
            "\nEND_POWERCONTEXT_PREPARED_CONTEXT_V1", 1
        )[0]
    )
    assert envelope["trust"] == "untrusted_history"
    assert len(envelope["items"]) == len(build.fragments)
    for fragment, origin, item in zip(build.fragments, build.origins, envelope["items"], strict=True):
        assert isinstance(origin, MemoryEntryAddress)
        assert fragment.address == origin
        assert type(fragment.start) is int and type(fragment.end) is int
        original = originals[(origin.memory.scope_id, origin.memory.artifact.revision, origin.entry_id)]
        assert 0 <= fragment.start < fragment.end <= len(original)
        assert fragment.text == original[fragment.start : fragment.end] == item["content"]
        assert (fragment.start, fragment.end) == (item["start"], item["end"])
        assert item["citation"] == {
            "scope_id": origin.memory.scope_id,
            "memory_ref": origin.memory.artifact.model_dump(mode="json"),
            "entry_id": origin.entry_id,
            "entry_version_id": origin.entry_version_id,
        }


def test_query_selects_tail_and_disjoint_evidence() -> None:
    text = (
        "Solar panels harvest sunlight. "
        + "Unrelated measurements were recorded. " * 30
        + "Cardiac recovery improved after treatment."
    )
    first = select(text, "solar sunlight", 800)
    last = select(text, "cardiac recovery", 800)
    assert "Solar panels" in " ".join(f.text for f in first.fragments)
    assert "Cardiac recovery" in " ".join(f.text for f in last.fragments)
    assert first.context.content != last.context.content
    for build in (first, last):
        assert build.context.content_bytes <= 800
        assert_exact(build, {("scope", 3, "entry"): text})
    both = select(text, "solar cardiac recovery", 1400)
    assert len(both.fragments) >= 2
    assert any("Solar panels" in f.text for f in both.fragments)
    assert any("Cardiac recovery" in f.text for f in both.fragments)
    assert_exact(both, {("scope", 3, "entry"): text})


def test_fragments_preserve_exact_scope_revision_and_offsets() -> None:
    text = "Evidence establishes reproducible recovery."
    other = REF.model_copy(update={"revision": 4})
    groups = (
        PreparedMemoryCandidates("local", REF, (hit(text), hit(text))),
        PreparedMemoryCandidates("shared", REF, (hit(text),)),
        PreparedMemoryCandidates("local", other, (hit(text, ref=other),)),
    )
    result = PreparedContextBuilder().build_scopes_result(
        current_scope_id="local",
        memory_candidates=groups,
        request=PrepareContextRequest(query="reproducible recovery", evidence_selection=True, max_bytes=4000),
    )
    assert len(result.fragments) == 3
    assert_exact(result, {("local", 3, "entry"): text, ("shared", 3, "entry"): text, ("local", 4, "entry"): text})


@pytest.mark.parametrize(
    "scope,ref,bad_hit,reason",
    [
        ("", REF, hit("Evidence"), "memory-scope-missing"),
        ("scope", None, hit("Evidence"), "memory-ref-missing"),
        ("scope", REF, hit("Unrelated", ref=REF.model_copy(update={"revision": 4})), "memory-ref-mismatch"),
    ],
)
def test_evidence_checks_all_candidate_anchors(scope, ref, bad_hit, reason) -> None:
    with pytest.raises(PreparedContextInvariantError, match=reason):
        PreparedContextBuilder().build_scopes_result(
            current_scope_id="scope",
            memory_candidates=(PreparedMemoryCandidates(scope, ref, (bad_hit,)),),
            request=PrepareContextRequest(query="evidence", evidence_selection=True),
        )


def test_budget_includes_escaped_citations_and_envelope() -> None:
    text = 'Evidence "🙂" \\ \x00'
    full = select(text, "evidence", 2000)
    assert full.context.status == "ready"
    exact = select(text, "evidence", full.context.content_bytes)
    less = select(text, "evidence", full.context.content_bytes - 1)
    assert exact.context == full.context
    assert less.context.status == "empty"
    assert_exact(full, {("scope", 3, "entry"): text})
    assert_exact(less, {})


def test_overlapping_evidence_does_not_duplicate_coverage() -> None:
    text = "Alpha inhibits migration. Beta promotes recovery. Gamma activates survival."
    build = select(text, "alpha beta gamma recovery", 1800)
    assert_exact(build, {("scope", 3, "entry"): text})
    spans = sorted((f.start, f.end) for f in build.fragments)
    assert all(left[1] < right[0] for left, right in pairwise(spans))
    assert len(build.fragments) == 1


def test_long_sentence_tail_preserves_original_unicode_coordinates() -> None:
    text = "e\u0301 Straße " + "unrelated " * 180 + "TAILMARKER recovery"
    build = select(text, "tailmarker recovery", 800)
    assert any("TAILMARKER recovery" in f.text for f in build.fragments)
    assert_exact(build, {("scope", 3, "entry"): text})


@pytest.mark.parametrize("text,query", [("   ", "evidence"), ("Evidence is absent.", "unmatched"), ("Evidence", "!!!")])
def test_no_available_evidence_is_a_legal_empty_context(text, query) -> None:
    result = select(text, query)
    assert result.context.status == "empty"
    assert_exact(result, {})


def test_evidence_budget_probe_distinguishes_no_match_from_fitting_pressure() -> None:
    builder = PreparedContextBuilder()
    candidates = (PreparedMemoryCandidates("scope", REF, (hit("Recovery improves survival."),)),)
    for query, pressure in (("unmatched", False), ("recovery", True)):
        request = PrepareContextRequest(query=query, max_bytes=512, evidence_selection=True)
        result = builder.build_scopes_result(current_scope_id="scope", memory_candidates=candidates, request=request)
        probe = builder.probe_budget(current_scope_id="scope", memory_candidates=candidates, request=request)
        assert result.context.status == "empty"
        assert probe.delivered_items == 0 and probe.unused_bytes == 512
        assert probe.budget_bounded is pressure
        assert probe.dropped_items == int(pressure)


def test_disabled_evidence_preserves_legacy_and_scopeless_builder() -> None:
    builder = PreparedContextBuilder()
    hits = (hit("Evidence for recovery."),)
    default = builder.build_result(request=PrepareContextRequest(query="recovery"), memory_ref=REF, hits=hits)
    disabled = builder.build_result(
        request=PrepareContextRequest(query="recovery", evidence_selection=False), memory_ref=REF, hits=hits
    )
    assert default == disabled
    assert default.context.status == "ready" and default.fragments == ()


def test_feasible_complete_window_survives_dense_repeated_matches() -> None:
    text = "Alpha beta. " * 45 + "Alpha beta " + "unrelated " * 35 + "gamma recovery."
    result = select(text, "alpha beta gamma recovery", 1800)
    assert any("gamma recovery" in fragment.text for fragment in result.fragments)


def test_unique_query_evidence_is_not_displaced_by_repeated_citations() -> None:
    hits = (hit("Alpha " * 8, "repeat-a"), hit("Alpha " * 8, "repeat-b"), hit("Beta gamma support recovery.", "novel"))
    result = PreparedContextBuilder().build_result(
        scope_id="scope",
        memory_ref=REF,
        hits=hits,
        request=PrepareContextRequest(query="alpha beta gamma", max_bytes=950, evidence_selection=True),
    )
    assert any("Beta gamma" in fragment.text for fragment in result.fragments)
    assert any("Alpha" in fragment.text for fragment in result.fragments)
    assert result.context.content_bytes <= 950
