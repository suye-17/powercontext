export type BackendKind = "webgpu" | "wasm";
export type Precision = "fp16" | "q8" | "fp32";

export interface EmbedderInfo {
  backend: BackendKind;
  precision: Precision;
  modelId: string;
  revision: string;
  loadMs: number;
  adapter?: string;
  fallbackReason?: string;
}

export interface Embedder {
  extractor: {
    (input: string[], options: Record<string, unknown>): Promise<{ tolist(): number[][] }>;
    dispose?: () => Promise<void>;
  };
  info: EmbedderInfo;
}

export interface Chunk {
  id: string;
  text: string;
  metadata: Record<string, string | number | boolean>;
}

export interface CorpusChunk {
  id: string;
  text: string;
}

export interface CorpusDocument {
  id: string;
  title: string;
  source: string;
  chunks: CorpusChunk[];
}

export interface CorpusQuery {
  id: string;
  question: string;
  relevant: string[];
}

export interface SampleCorpus {
  name: string;
  description: string;
  license: string;
  documents: CorpusDocument[];
  queries: CorpusQuery[];
}

export interface ImportItem extends Chunk {
  embedding: number[];
}

export interface SearchResult {
  id: string;
  text: string;
  metadata: Record<string, unknown>;
  distance: number | null;
  rank: number;
}

export interface ImportResponse {
  collection: string;
  imported: number;
  count: number;
  refreshed: boolean;
  durationMs: number;
}

export interface SearchResponse {
  collection: string;
  mode: "hybrid" | "vector-fallback";
  fullTextQuery?: string;
  results: SearchResult[];
  durationMs: number;
}

export interface StatsResponse {
  collection: string;
  exists: boolean;
  count: number;
}

export interface CollectionRow {
  collection: string;
  count: number;
}

export interface HealthResponse {
  ok: boolean;
  db: { host: string; port: number; database: string; dimension: number };
  collections: CollectionRow[];
}
