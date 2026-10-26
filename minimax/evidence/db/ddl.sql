-- Embedded seekdb schema created by src/sdd_context/store.py.
CREATE TABLE IF NOT EXISTS pc_corpus_documents (
    id CHAR(64) PRIMARY KEY,
    path TEXT NOT NULL,
    kind VARCHAR(16) NOT NULL,
    title TEXT NOT NULL,
    content_hash CHAR(64) NOT NULL,
    content LONGTEXT NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    UNIQUE KEY ux_path (path(190))
) DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS pc_corpus_chunks (
    id CHAR(64) PRIMARY KEY,
    document_id CHAR(64) NOT NULL,
    kind VARCHAR(16) NOT NULL,
    spec_id VARCHAR(64) NULL,
    symbol VARCHAR(255) NULL,
    test_name VARCHAR(255) NULL,
    start_line INT NOT NULL,
    end_line INT NOT NULL,
    text LONGTEXT NOT NULL,
    searchable_text LONGTEXT NOT NULL,
    FULLTEXT KEY ft_searchable (searchable_text),
    KEY ix_kind_spec (kind, spec_id),
    KEY ix_kind_symbol (kind, symbol(190)),
    KEY ix_kind_test (kind, test_name(190))
) DEFAULT CHARSET=utf8mb4;
