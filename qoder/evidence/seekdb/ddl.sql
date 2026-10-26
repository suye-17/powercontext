CREATE TABLE `pd_documents` (
  `id` char(32) NOT NULL,
  `paper_id` varchar(128) NOT NULL,
  `title` varchar(512) NOT NULL,
  `authors` text NOT NULL,
  `year` int(11) NOT NULL,
  `tags` varchar(255) NOT NULL,
  `status` varchar(16) NOT NULL,
  `path` text NOT NULL,
  `content_hash` char(64) NOT NULL,
  `content` longtext NOT NULL,
  `updated_at` varchar(64) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `ux_paper` (`paper_id`) BLOCK_SIZE 16384 LOCAL,
  FULLTEXT KEY `ft_title` (`title`) WITH PARSER space PARSER_PROPERTIES=(min_token_size=3,max_token_size=84) BLOCK_SIZE 16384
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0;

CREATE TABLE `pd_chunks` (
  `id` char(32) NOT NULL,
  `document_id` char(32) NOT NULL,
  `paper_id` varchar(128) NOT NULL,
  `section` varchar(255) NOT NULL,
  `order_index` int(11) NOT NULL,
  `start_line` int(11) NOT NULL,
  `end_line` int(11) NOT NULL,
  `text` longtext NOT NULL,
  `searchable_text` longtext NOT NULL,
  `embedding` VECTOR(1536) DEFAULT NULL,
  PRIMARY KEY (`id`),
  FULLTEXT KEY `ft_searchable` (`searchable_text`) WITH PARSER space PARSER_PROPERTIES=(min_token_size=3,max_token_size=84) BLOCK_SIZE 16384,
  KEY `ix_paper` (`paper_id`) BLOCK_SIZE 16384 LOCAL,
  KEY `ix_document` (`document_id`) BLOCK_SIZE 16384 LOCAL,
  VECTOR KEY `ix_chunk_embedding` (`embedding`) WITH (DISTANCE=COSINE, TYPE=HNSW, LIB=VSAG, M=16, EF_CONSTRUCTION=200, EF_SEARCH=64) BLOCK_SIZE 16384
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0;

CREATE TABLE `pd_meta` (
  `meta_key` varchar(64) NOT NULL,
  `meta_value` varchar(255) NOT NULL,
  PRIMARY KEY (`meta_key`)
) ORGANIZATION INDEX DEFAULT CHARSET = utf8mb4 ROW_FORMAT = DYNAMIC COMPRESSION = 'zstd_1.3.8' BLOCK_SIZE = 16384 TABLET_SIZE = 134217728 PCTFREE = 0;
