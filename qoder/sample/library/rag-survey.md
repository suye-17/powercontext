---
paper_id: rag-survey
title: "Retrieval Augmented Generation: A Reading Map"
authors: PaperDesk Reading Group
year: 2023
tags: rag, retrieval, survey
status: unread
---

## Abstract
Retrieval augmented generation (RAG) couples a document retriever with a language model so that answers can be grounded in external evidence rather than model parameters alone. This reading map organizes the design space along three axes: what is retrieved, when retrieval happens, and how retrieved evidence is fused into generation. The central claim is that most quality differences between RAG systems come from retrieval granularity and evidence selection, not from the generator. We use this map to compare sparse retrieval, dense retrieval, and hybrid fusion.

## Method
We group systems by retrieval unit. Passage-level retrieval offers precise evidence spans, while document-level retrieval preserves broader context. Dense bi-encoders learn a shared embedding space for queries and passages; sparse lexical retrieval such as BM25 remains strong for rare terms, identifiers, and exact phrases. Hybrid fusion combines the two candidate lists, commonly with reciprocal rank fusion (RRF), which is robust because it uses ranks instead of incomparable score scales. A reranker may then refine the top candidates before generation.

## Results
Across the notes we compare, hybrid retrieval improves recall on terminology-heavy questions, and reranking improves the precision of the context passed to the generator. Systems that cite evidence spans produce answers that are easier to audit, but citation coverage must be measured rather than assumed. Retrieval latency is dominated by the first-stage candidate search when the index is warm. The survey also observes that chunk boundaries can change which passage wins a query even when the underlying document is unchanged.

## Limitations
The map covers English technical documents and does not model multilingual tokenization. It treats rerankers as a black box and does not compare training objectives. Most reported gains are measured on small curated sets, so absolute numbers should not be transferred across corpora. The notes also assume that the generator faithfully uses the supplied evidence, which requires a separate grounding check.

## Takeaways
Start from retrieval quality, then improve the generator. Keep a lexical baseline, add dense retrieval when paraphrase matters, and fuse by rank rather than raw score. Always preserve a path from an answer sentence back to a source passage and its line numbers.
