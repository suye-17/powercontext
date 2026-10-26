-- 1. Corpus inventory by chunk kind.
SELECT kind, COUNT(*) AS chunk_count
FROM pc_corpus_chunks
GROUP BY kind
ORDER BY kind;

-- 2. Full-text candidates for specs.
SELECT c.spec_id, d.path, c.start_line, c.end_line,
       MATCH(c.searchable_text) AGAINST ('SPEC-003 min_priority priority' IN NATURAL LANGUAGE MODE) AS score
FROM pc_corpus_chunks c
JOIN pc_corpus_documents d ON d.id = c.document_id
WHERE c.kind = 'spec'
  AND MATCH(c.searchable_text) AGAINST ('SPEC-003 min_priority priority' IN NATURAL LANGUAGE MODE)
ORDER BY score DESC, d.path, c.start_line
LIMIT 5;

-- 3. Exact code symbol match.
SELECT c.symbol, d.path, c.start_line, c.end_line
FROM pc_corpus_chunks c
JOIN pc_corpus_documents d ON d.id = c.document_id
WHERE c.kind = 'code' AND c.symbol = 'list_tasks';

-- 4. Exact test match.
SELECT c.test_name, d.path, c.start_line, c.end_line
FROM pc_corpus_chunks c
JOIN pc_corpus_documents d ON d.id = c.document_id
WHERE c.kind = 'test' AND c.test_name = 'test_add_task_rejects_invalid_priority';
