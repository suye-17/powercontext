---
title: Save and recall Memory
description: Save project decisions, retrieve relevant history, and correct outdated Memory.
---

# Save and recall Memory

Memory keeps durable decisions, constraints, and facts. PreparedContext selects relevant history for one request;
it is temporary and does not create another Memory entry.

## Save, recall, and correct

1. Complete the [Quick Start](../get-started/quickstart.md) and keep the resolved Scope consistent across sessions.
2. Explicitly ask the Agent to save the information. A direct `remember_memory` write does not require a model.
3. Search with `search_memory`, or inspect entries with `list_memory_entries` and `get_memory_entry` where the host
   exposes them. Keep the returned citation when referring to a result.
4. Use the current citation to revise incorrect information or retire information that should no longer be recalled.
   Retirement removes it from active recall while preserving history.

Host tool names differ; see [Connect Agents](../integrations/index.md). The
[HTTP API](../develop/http-api.md) exposes the complete request schemas and concurrency requirements.

FTS uses the same query terms for candidate retrieval and admission. Query normalization excludes common English
function words in longer queries and recognizes standalone execution instructions accompanying a question, such as
"Use only supplied context" and "Do not call tools, read files, inspect old sessions, or delegate." Those instructions
cannot supply evidence for an unrelated fact. Explicitly quoted terms remain searchable, including function words
used as identifiers such as `"AND" "OR" precedence`. Domain-specific directives and unrecognized wording remain searchable;
this is a conservative lexical rule, not a general intent classifier. The default threshold requires 25% of all
remaining distinct terms, with a minimum of two matches; one- or two-term queries require one match. Direct search and
prepared context share this rule, including when the optional recall gate relaxes the threshold. Stored text and
vector queries are unchanged, and matching words alone do not establish semantic relevance.

## Automatic context and extraction

Recall hooks ask the Server for bounded PreparedContext. An `empty` result is valid when no relevant information is
available. Returned history does not override current instructions or live project state.

Use [Prepare standard context text](prepare-context-text.md) to choose Memory, Experience, Profile, and Topic Memory
sections, set their order and limits, and configure the total entry limit.

Capturing a prompt creates Source evidence. It does not guarantee Memory extraction. Enable a generation model and
Source processing through [model configuration](../get-started/configure-models.md); configure embeddings only
when [vector or hybrid search](configure-vector-search.md) is needed. Review the host's prompt-capture switch before
recording project input.

Topic Memory has separate processing and retrieval surfaces (`search_topic_memory` and `get_topic_memory` on MCP).
It is not enabled by a basic explicit Memory write; inspect the Topic Memory settings and enabled runtime capabilities
in [Configuration](../operate/configuration.md).

Use [Handoff](memory-and-handoff.md) to transfer the current task boundary, and [Sources](sources.md) to preserve raw evidence.

## Deactivate and restore content by source dependencies

The built-in Python Runtime exposes `runtime.lifecycle.for_scope(scope_id)` for explicit Source, Memory, and Topic Memory
dependencies. Each Scope has one writer. Register parents first; a node's reference, parents, and policy cannot change after
registration. `all` requires every parent to support the complete body; `any` means each parent can independently support
that complete body.

```python
from powercontext.builtin.runtime.source_lifecycle import SourceLifecycleEvent

lifecycle = runtime.lifecycle.for_scope(scope_id)
await lifecycle.register_source("source-a", captured.source_ref, revision=3)
await lifecycle.register_memory(
    "memory-a", saved.entry.citation, parents=("source-a",), policy="all"
)
await lifecycle.register_topic(
    "topic-a", topic_ref, parents=("memory-a",), policy="any"
)

active_ids = await lifecycle.apply(
    SourceLifecycleEvent(
        node_id="source-a", revision=3, sequence=1,
        available=False, reason="Source withdrawn",
    )
)
audit_reference = await lifecycle.resolve("memory-a", purpose="audit")
```

Here, `runtime` comes from `open_builtin_runtime()`, `scope_id` is registered, `captured` is a Source capture result in that
Scope, `saved` is an explicit Memory write result with an `entry`, and `topic_ref` is an already published, exactly readable
Topic Memory ArtifactRef. The caller chooses the lifecycle `revision`; it is independent of Memory and Topic body revisions.
Every registered reference must be readable in the current Scope. This built-in Python entry point adds no HTTP or MCP
operation.

A Source starts available. Derived nodes immediately inherit their parents' current availability, including nodes registered
after withdrawal. Each Source has an independent, monotonically increasing event sequence. An incorrect revision, a lower
sequence, or different content at the same sequence raises `SourceLifecycleError`. Replaying an identical event creates no
additional Memory revision. To restore a Source, send a higher sequence with `available=True`.

One registration or event updates the graph, native Memory state, and Topic retrieval eligibility in the same database
transaction. A failed write or cancellation rolls back the operation; retry the same event. Registrations, events, and
eligibility survive restarts and remain isolated by Scope. Recovery does not require rewriting bodies or regenerating
summaries.

Unavailable Memory leaves current lists, search, and PreparedContext; `include_inactive=True` still exposes its state.
Unavailable Topic Memory leaves current browsing, native search, and PreparedContext, while its original ArtifactRef still
reads the exact complete body. Resolution with `purpose="generation"` rejects unavailable nodes; `purpose="audit"` returns
the stored original reference. After restoration, generation resolution returns that same reference. Memory state revisions
may advance, but the bound body version and historical citation remain unchanged. Do not externally revise the bound Memory
body version while it is registered.
