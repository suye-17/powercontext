---
paper_id: rerank-notes
title: "Reranking for Retrieval Pipelines"
authors: PaperDesk Reading Group
year: 2024
tags: rerank, retrieval, rag
status: reading
---

## Abstract
First-stage retrieval must be cheap, so it usually trades precision for recall. A reranker revisits a small candidate list and scores each query-passage pair with a stronger model. The core problem it solves is context pollution: irrelevant passages consume the generator's limited context window and invite unsupported claims. Reranking is therefore a precision filter placed between retrieval and generation.

## Method
A cross-encoder reads the query and passage together, allowing token-level interactions that a bi-encoder cannot capture. The input candidate list is produced by BM25, a dense retriever, or a hybrid of both. We compare two-stage and three-stage pipelines: retrieve top 50, rerank top 20, then pass the best 5 to the generator. Training uses query-passage pairs with hard negatives mined from the first-stage results. Reranking scores are not comparable across queries, so pipelines use them only to order candidates within one query.

## Results
Reranking improves nDCG at ten and reduces the number of irrelevant passages in the generator context. In our notes, the largest gains appear on questions with rare entities and on queries whose lexical and dense candidate lists disagree. The reranker also helps when the first stage returns a relevant passage at rank 12 but not at rank 1. Latency grows linearly with the number of reranked pairs, so candidate depth is the main cost control.

## Limitations
A cross-encoder reranker cannot fix a candidate list that misses the relevant passage entirely. Long passages increase cost and may exceed the reranker's input limit. Training hard negatives from the same retriever can overfit to its failure modes. The notes do not cover learned fusion of multiple rerankers, and nDCG rewards graded relevance that our small corpus does not provide.

## Takeaways
Treat reranking as a precision stage with an explicit budget. Keep the candidate depth configurable and log both first-stage and reranked ranks for debugging. If the generator still sees irrelevant context, inspect the reranker rather than increasing the context window.
