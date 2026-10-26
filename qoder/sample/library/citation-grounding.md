---
paper_id: citation-grounding
title: "Grounding Generated Summaries with Evidence Spans"
authors: PaperDesk Reading Group
year: 2025
tags: citation, grounding, rag, evaluation
status: unread
---

## Abstract
A generated summary is useful only if a reader can verify it. Grounding ties each conclusion sentence to an evidence span in the source corpus. We study a simple contract: the generator may cite only the numbered chunks it was given, and a validator checks that every cited number exists. When the contract fails, the system must not present the unsupported sentence as established fact.

## Method
Retrieved chunks are numbered C1 through Cn and passed to the generator with a strict instruction to cite them. The validator parses citation markers, checks them against the chunk set, and computes citation coverage as cited conclusion sentences divided by total conclusion sentences. Unknown markers are removed from the visible summary and recorded in a report. If coverage falls below a threshold, or the model call fails, the system returns an extractive summary built from the chunks, with the same citation numbers. Prompt hashes and latency are logged without storing credentials.

## Results
In our notes, generated summaries have higher readability but occasionally attach a citation to a sentence that overstates the source. The validator catches unknown markers immediately, but it cannot detect a valid marker used for a claim the chunk does not support. Coverage is therefore a necessary but not sufficient check. Extractive fallback has full coverage and lower readability, and it keeps the product usable when the model endpoint is unavailable.

## Limitations
Citation validity is not entailment. The validator does not verify that the cited sentence actually supports the conclusion, which would require a separate entailment model. Coverage can be gamed by short, trivial sentences. The notes also assume chunk-level evidence rather than character-level spans, so a citation points to a range that may contain more than the cited claim.

## Takeaways
Make citations a checked contract, not a formatting style. Keep the citation map in the evidence output, fail closed to extractive mode, and state clearly that citation coverage measures traceability rather than truth.
