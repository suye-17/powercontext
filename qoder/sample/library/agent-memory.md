---
paper_id: agent-memory
title: "Long-Term Memory for Reading Agents"
authors: PaperDesk Reading Group
year: 2024
tags: agents, memory, personalization
status: unread
---

## Abstract
A reading agent that forgets each session forces the user to repeat goals, preferences, and progress. Long-term memory turns repeated instructions into state that can be read back at startup. This note distinguishes four memory kinds: stable preferences, active goals, per-document progress, and episodic session summaries. The design goal is continuity without pretending that memory is always correct.

## Method
Each memory entry is stored as a typed record with a stable identifier and a JSON payload. Preferences and goals are singleton or keyed records; progress is keyed by paper identifier; sessions are append-only. Updates use compare-and-swap on a revision token so that two writers cannot silently overwrite each other. Reads at startup reconstruct the current state and merge it with live retrieval results. A semantic search path allows the agent to recall a past session from a natural-language question.

## Results
Restarting the service with the same persistent store reproduces the same active records, which makes continuity testable. Revision history shows how a progress record moved from one section to the next. Semantic recall retrieves a session summary from a paraphrase of the user's earlier question, while lexical search alone misses it. Continuity changes planning: an in-progress paper is promoted ahead of unread papers with similar tags.

## Limitations
Memory can become stale when the user changes goals outside the system. Preferences inferred from behavior can be wrong, so the memory page must allow explicit correction. Semantic recall depends on an embedding service and needs a lexical fallback. The notes do not address privacy controls for shared devices.

## Takeaways
Type the memory, key it explicitly, revise it with version checks, and show it to the user. A restart comparison is a simple and convincing continuity test.
