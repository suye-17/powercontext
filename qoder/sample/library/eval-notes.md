---
paper_id: eval-notes
title: "Evaluating Retrieval and Grounded Answers"
authors: PaperDesk Reading Group
year: 2023
tags: evaluation, retrieval, citation
status: done
---

## Abstract
A retrieval augmented system needs two evaluation loops: one for retrieval and one for the generated answer. Retrieval metrics such as recall at k and mean reciprocal rank (MRR) answer whether the right evidence is reachable. Grounding metrics answer whether the answer actually uses that evidence. Conflating the two hides whether a failure came from search or from generation.

## Method
We build a query set with expected paper identifiers and section titles. Recall at k measures whether any expected section appears in the top k results; MRR records the rank of the first relevant hit. For answers, we count citation coverage as the share of conclusion sentences that carry a valid source marker, and we check each marker against the retrieved chunk list. A citation that points to a non-existent chunk is treated as unknown evidence, not as support. Evaluation runs are stored as JSON so that metrics can be recomputed.

## Results
Recall at five is high even when recall at one is modest, which confirms that reranking has room to help. Citation coverage separates fluent but unsupported answers from grounded ones. In our notes, extractive fallback summaries always carry citations but read less smoothly than generated summaries. Evaluation on a fixed query set makes chunking and reranking changes comparable across runs.

## Limitations
The query set is small and written by the same team that built the system, so it may share blind spots with the implementation. Section-level matching is coarser than sentence-level evidence matching. Recall and MRR do not measure whether the generator interprets evidence correctly. Citation coverage measures form, not factual entailment.

## Takeaways
Report retrieval and grounding separately. Keep the expected results next to each query and publish the raw run output. Use a deterministic fallback so that the evaluation can run without an external model.
