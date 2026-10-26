-- PaperDesk seekdb evidence queries (run through pylibseekdb/pymysql).
-- The application issues these shapes from src/paperdesk/store.py.

-- 1. Full-text retrieval with structured filters and join back to paper metadata.
SELECT c.id, c.paper_id, d.title, c.section, d.path, c.start_line, c.end_line,
       MATCH(c.searchable_text) AGAINST ('hybrid fusion' IN NATURAL LANGUAGE MODE) AS fts
FROM pd_chunks c
JOIN pd_documents d ON d.id = c.document_id
WHERE MATCH(c.searchable_text) AGAINST ('hybrid fusion' IN NATURAL LANGUAGE MODE)
  AND d.year >= 2023
ORDER BY fts DESC
LIMIT 10;

-- 2. Vector candidates by cosine distance (requires embeddings from ingest).
SELECT c.id, c.paper_id, c.section,
       cosine_distance(c.embedding, (SELECT embedding FROM pd_chunks LIMIT 1)) AS distance
FROM pd_chunks c
WHERE c.embedding IS NOT NULL
ORDER BY distance ASC
LIMIT 10;

-- 3. Index and schema inventory.
SHOW CREATE TABLE pd_documents;
SHOW CREATE TABLE pd_chunks;
SELECT COUNT(*) AS documents FROM pd_documents;
SELECT COUNT(*) AS chunks FROM pd_chunks;
SELECT COUNT(*) AS embedded_chunks FROM pd_chunks WHERE embedding IS NOT NULL;
