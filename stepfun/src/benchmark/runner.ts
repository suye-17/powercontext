import { api } from "../lib/backend";
import {
  MODEL_ID,
  MODEL_REVISION,
  MODEL_SIZES,
  detectWebGPU,
  disposeEmbedder,
  embedTexts,
  loadEmbedder,
  type LoadOptions,
} from "../lib/embeddings";
import { evaluateQueries, median } from "../lib/metrics";
import type {
  BackendKind,
  ImportItem,
  Precision,
  SampleCorpus,
} from "../lib/types";

export interface BenchConfig {
  id: string;
  backend: BackendKind;
  precision: Precision;
  label: string;
}

export const BENCH_CONFIGS: BenchConfig[] = [
  { id: "fp32-wasm", backend: "wasm", precision: "fp32", label: "FP32 基线 · WASM" },
  { id: "int8-wasm", backend: "wasm", precision: "q8", label: "INT8 · WASM" },
  { id: "fp16-webgpu", backend: "webgpu", precision: "fp16", label: "FP16 · WebGPU" },
  { id: "int8-webgpu", backend: "webgpu", precision: "q8", label: "INT8 · WebGPU" },
  { id: "fp32-webgpu", backend: "webgpu", precision: "fp32", label: "FP32 · WebGPU（后端验证）" },
];

const QUERY_LIMIT = 5;
const QUERY_REPEATS = 5;
const IMPORT_BATCH = 512;

export interface BenchResult {
  config: BenchConfig;
  environment: {
    userAgent: string;
    platform: string;
    hardwareConcurrency: number;
    deviceMemory: number | null;
    webgpuAvailable: boolean;
    webgpuAdapter: string | null;
  };
  model: {
    id: string;
    revision: string;
    precision: Precision;
    sizeBytes: number;
    compressionRatio: number;
    loadMs: number;
    downloadMs: number;
    downloads: Array<{ name: string; durationMs: number; transferSize: number }>;
  };
  corpus: {
    documents: number;
    chunks: number;
    queries: number;
    batchSize: number;
  };
  timings: {
    documentEmbeddingMs: number;
    documentEmbeddingThroughput: number;
    importRefreshMs: number;
    importServerMs: number;
    queryEmbeddingMsMedian: number;
    searchMsMedian: number;
    searchServerMsMedian: number;
    endToEndMsMedian: number;
    searchModes: Record<string, number>;
  };
  quality: {
    k: number;
    recall: number;
    ndcg: number;
    perQuery: Array<{
      id: string;
      question: string;
      relevant: string[];
      retrieved: string[];
      recall: number;
      ndcg: number;
      searchMs: number;
      endToEndMs: number;
      searchMode: string;
      fullTextQuery: string;
      raw: {
        queryEmbeddingMs: number[];
        searchMs: number[];
        searchServerMs: number[];
        endToEndMs: number[];
      };
    }>;
  };
}

interface PerQueryTiming {
  id: string;
  question: string;
  relevant: string[];
  retrieved: string[];
  queryEmbeddingMs: number;
  searchMs: number;
  searchServerMs: number;
  endToEndMs: number;
  searchMode: string;
  fullTextQuery: string;
  raw: {
    queryEmbeddingMs: number[];
    searchMs: number[];
    searchServerMs: number[];
    endToEndMs: number[];
  };
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function loadCorpus(): Promise<SampleCorpus> {
  const response = await fetch("/sample-corpus.json");
  if (!response.ok) throw new Error(`加载示例语料失败：HTTP ${response.status}`);
  return (await response.json()) as SampleCorpus;
}

function corpusChunks(corpus: SampleCorpus): Array<{
  id: string;
  text: string;
  metadata: Record<string, string | number | boolean>;
}> {
  return corpus.documents.flatMap((document) =>
    document.chunks.map((chunk, index) => ({
      id: chunk.id,
      text: chunk.text,
      metadata: {
        documentId: document.id,
        title: document.title,
        source: document.source,
        chunkIndex: index,
      },
    })),
  );
}

export async function runBenchmark(
  config: BenchConfig,
  onProgress: (message: string) => void,
): Promise<BenchResult> {
  const corpus = await loadCorpus();
  const chunks = corpusChunks(corpus);
  const webgpu = await detectWebGPU();
  onProgress(
    `配置 ${config.label}：${corpus.documents.length} 篇文档 / ${chunks.length} 个文本块 / ${corpus.queries.length} 个查询`,
  );

  const loadOptions: LoadOptions = {
    forceBackend: config.backend,
    forcePrecision: config.precision,
    onProgress: (event) => {
      if (event && typeof event === "object") {
        const value = event as { status?: string; file?: string; progress?: number };
        if (value.status === "progress" && value.file) {
          onProgress(
            `模型 ${value.file} ${typeof value.progress === "number" ? `${Math.round(value.progress)}%` : ""}`,
          );
        }
      }
    },
  };

  const resourceStart = performance.getEntriesByType("resource").length;
  const embedder = await loadEmbedder(loadOptions);
  const modelDownloads = performance
    .getEntriesByType("resource")
    .slice(resourceStart)
    .filter((entry) => /\/onnx\/|tokenizer|config\.json/.test(entry.name))
    .map((entry) => {
      const resource = entry as PerformanceResourceTiming;
      return {
        name: resource.name.split("/").pop() ?? resource.name,
        durationMs: resource.duration,
        transferSize: resource.transferSize,
      };
    });
  onProgress(
    `模型就绪：${embedder.info.backend} / ${embedder.info.precision} / ${embedder.info.loadMs} ms${
      embedder.info.adapter ? ` / ${embedder.info.adapter}` : ""
    }`,
  );

  const collection = `stepfun_bench_${config.id.replace(/-/g, "_")}`;

  try {
    await api.reset(collection);
    const embeddingStart = performance.now();
    const vectors = await embedTexts(
      embedder,
      chunks.map((chunk) => chunk.text),
      {
        batchSize: 16,
        onProgress: (done, total) =>
          onProgress(`文档向量化 ${done}/${total}`),
      },
    );
    const documentEmbeddingMs = performance.now() - embeddingStart;

    const importItems: ImportItem[] = chunks.map((chunk, index) => ({
      ...chunk,
      embedding: vectors[index],
    }));
    const importStart = performance.now();
    let importServerMs = 0;
    const batches = Math.ceil(importItems.length / IMPORT_BATCH);
    for (let index = 0; index < batches; index += 1) {
      const batch = importItems.slice(index * IMPORT_BATCH, (index + 1) * IMPORT_BATCH);
      const result = await api.importItems(collection, batch, index === batches - 1);
      importServerMs += result.durationMs;
      onProgress(`写入 seekdb 批次 ${index + 1}/${batches}`);
    }
    const importRefreshMs = performance.now() - importStart;

    const perQuery: PerQueryTiming[] = [];
    for (const query of corpus.queries) {
      const queryEmbeddingRuns: number[] = [];
      const searchRuns: number[] = [];
      const searchServerRuns: number[] = [];
      const endToEndRuns: number[] = [];
      let firstIds: string[] = [];
      let firstMode = "";
      let firstFullText = "";
      for (let repeat = 0; repeat < QUERY_REPEATS; repeat += 1) {
        const start = performance.now();
        const queryVector = await embedTexts(embedder, [query.question], { batchSize: 1 });
        const afterEmbed = performance.now();
        const result = await api.search(
          collection,
          query.question,
          queryVector[0],
          QUERY_LIMIT,
        );
        const afterSearch = performance.now();
        await new Promise((resolve) => requestAnimationFrame(resolve));
        const afterPaint = performance.now();
        queryEmbeddingRuns.push(afterEmbed - start);
        searchRuns.push(afterSearch - afterEmbed);
        searchServerRuns.push(result.durationMs);
        endToEndRuns.push(afterPaint - start);
        if (repeat === 0) {
          firstIds = result.results.map((row) => row.id);
          firstMode = result.mode;
          firstFullText = result.fullTextQuery ?? "";
        }
      }
      perQuery.push({
        id: query.id,
        question: query.question,
        relevant: query.relevant,
        retrieved: firstIds,
        queryEmbeddingMs: median(queryEmbeddingRuns),
        searchMs: median(searchRuns),
        searchServerMs: median(searchServerRuns),
        endToEndMs: median(endToEndRuns),
        searchMode: firstMode,
        fullTextQuery: firstFullText,
        raw: {
          queryEmbeddingMs: queryEmbeddingRuns,
          searchMs: searchRuns,
          searchServerMs: searchServerRuns,
          endToEndMs: endToEndRuns,
        },
      });
      onProgress(`查询 ${query.id} 完成`);
    }

    const quality = evaluateQueries(
      corpus.queries,
      Object.fromEntries(perQuery.map((query) => [query.id, query.retrieved])),
      QUERY_LIMIT,
    );
    const perQueryWithTimings = quality.perQuery.map((row) => {
      const timing = perQuery.find((query) => query.id === row.id);
      return {
        ...row,
        searchMs: timing?.searchMs ?? 0,
        endToEndMs: timing?.endToEndMs ?? 0,
        searchMode: timing?.searchMode ?? "",
        fullTextQuery: timing?.fullTextQuery ?? "",
        raw: timing?.raw ?? {
          queryEmbeddingMs: [],
          searchMs: [],
          searchServerMs: [],
          endToEndMs: [],
        },
      };
    });
    const searchModes = perQuery.reduce<Record<string, number>>((accumulator, query) => {
      accumulator[query.searchMode] = (accumulator[query.searchMode] ?? 0) + 1;
      return accumulator;
    }, {});

    const modelSize = MODEL_SIZES[config.precision];
    return {
      config,
      environment: {
        userAgent: navigator.userAgent,
        platform: navigator.platform,
        hardwareConcurrency: navigator.hardwareConcurrency,
        deviceMemory:
          typeof (navigator as unknown as { deviceMemory?: number }).deviceMemory === "number"
            ? (navigator as unknown as { deviceMemory?: number }).deviceMemory!
            : null,
        webgpuAvailable: webgpu.available,
        webgpuAdapter: webgpu.adapter ?? null,
      },
      model: {
        id: MODEL_ID,
        revision: MODEL_REVISION,
        precision: config.precision,
        sizeBytes: modelSize,
        compressionRatio: MODEL_SIZES.fp32 / modelSize,
        loadMs: embedder.info.loadMs,
        downloadMs: modelDownloads.reduce((total, entry) => total + entry.durationMs, 0),
        downloads: modelDownloads,
      },
      corpus: {
        documents: corpus.documents.length,
        chunks: chunks.length,
        queries: corpus.queries.length,
        batchSize: 16,
      },
      timings: {
        documentEmbeddingMs,
        documentEmbeddingThroughput: chunks.length / (documentEmbeddingMs / 1000),
        importRefreshMs,
        importServerMs,
        queryEmbeddingMsMedian: median(perQuery.map((query) => query.queryEmbeddingMs)),
        searchMsMedian: median(perQuery.map((query) => query.searchMs)),
        searchServerMsMedian: median(perQuery.map((query) => query.searchServerMs)),
        endToEndMsMedian: median(perQuery.map((query) => query.endToEndMs)),
        searchModes,
      },
      quality: {
        k: QUERY_LIMIT,
        recall: quality.recall,
        ndcg: quality.ndcg,
        perQuery: perQueryWithTimings,
      },
    };
  } finally {
    await disposeEmbedder(embedder);
  }
}

export function benchError(config: BenchConfig, error: unknown): string {
  return `${config.label}: ${messageOf(error)}`;
}
