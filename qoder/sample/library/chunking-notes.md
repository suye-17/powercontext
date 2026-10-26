---
paper_id: chunking-notes
title: "Chunking Strategies for Long Technical Documents"
authors: PaperDesk Reading Group
year: 2022
tags: chunking, rag, retrieval
status: unread
---

## Abstract
Chunking decides what a retriever can see. A chunk that is too small loses the context needed to interpret a claim, while a chunk that is too large dilutes its embedding and wastes the generator context window. This note compares fixed windows, sentence packing, and semantic boundaries on technical documents with headings, formulas, and references.

## Method
Fixed windows split text every N tokens with an overlap. Sentence packing accumulates whole sentences until a token budget is reached, which avoids cutting mid-sentence but can still cross a topic boundary. Semantic chunking uses paragraph or heading boundaries, optionally confirmed by embedding similarity between adjacent sentences. We evaluate each strategy with the same retriever and generator so that chunking is the only variable. Each chunk keeps its document path, section title, and line range for citation.

## Results
Sentence packing improves readability of retrieved evidence and slightly raises recall for short factual queries. Semantic boundaries help queries that depend on a section's argument, but they produce uneven chunk sizes and can reduce recall when a relevant fact sits in a long paragraph. Overlap reduces boundary misses at the cost of duplicate evidence and a larger index. Heading-aware chunks make citations easier for readers to verify.

## Limitations
The study uses a small English corpus and cannot settle the best chunk size for code or tables. Semantic boundaries depend on an embedding model that may not match the retriever. Overlap can cause the same passage to appear twice in the generator context, inflating perceived support. The notes do not measure end-to-end answer quality with human raters.

## Takeaways
Prefer heading-aware sentence packing for technical notes, keep line numbers, and make chunk size configurable. Treat the recall-precision tradeoff as a corpus-specific tuning problem, not a universal constant.
