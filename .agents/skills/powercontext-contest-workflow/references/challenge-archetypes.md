# Challenge Archetypes

Read the selected challenge itself for binding details. These groups choose a useful investigation and verification lens; they do not replace the challenge contract.

| Archetype | Current examples | Main risks to inspect | Verification lens |
| --- | --- | --- | --- |
| Repository setup | 01 fork | Repository identity, public visibility, source relationship | Verify the exact repository metadata and requested URL. Use GitHub tools only for explicitly requested remote operations. |
| Source parsing and evidence | 02 CSV, 03 Pi history, 04 PDF | Lossy parsing, identity collisions, incomplete raw evidence, unreadable references | Check malformed input, full-record preservation, exact readback, incremental changes, and recovery where required. |
| Remote scoring and retrieval | 05 HTTP reranking, 10 evidence selection | Request limits, response validation, unstable ties, query relevance, citation or byte budgets | Verify boundaries and identities; measure relevance, request count, and encoded payload size when specified. |
| Context serialization and processing | 06 incremental pack, 07 count trigger, 08 embedding cache | Incorrect reconstruction, duplicated work, lost triggers, concurrency, restart behavior | Rebuild from serialized state; test thresholds, idempotency, concurrency, persistence, and failure recovery. |
| State and provenance propagation | 09 source lifecycle | Incorrect dependency propagation, stale search/context, loss of historical evidence | Exercise state transitions across scopes and generations; verify both current eligibility and exact historical reads. |
| Database query and performance | 11 hybrid search | Query count, filter drift, stale identity, hidden full scans, quality regressions | Inspect actual query shape and backend behavior; measure the stated cost and retrieval quality on representative inputs. |

## Cross-cutting checks

- Identify whether the score is all-or-nothing and map each required group to an observable assertion.
- Separate parser/algorithm behavior from persistence, runtime, retrieval, and host integration.
- Honor explicit constraints on dependencies, network access, model calls, code execution, privacy, and test fixtures.
- Keep evidence authoritative and derived projections distinguishable. A passing parser test does not prove persisted readback; a mocked backend does not prove a real backend claim.
- Treat public tests as additional evidence, not as a substitute for clauses they do not cover.
