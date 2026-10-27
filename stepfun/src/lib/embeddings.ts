import { pipeline } from "@huggingface/transformers";

import type { BackendKind, Embedder, EmbedderInfo, Precision } from "./types";

export const MODEL_ID = "Xenova/all-MiniLM-L6-v2";
export const MODEL_REVISION = "751bff37182d3f1213fa05d7196b954e230abad9";
export const EMBEDDING_DIMENSION = 384;

export const MODEL_SIZES = {
  fp32: 90_387_606,
  fp16: 45_297_825,
  q8: 22_972_370,
} as const;

export interface LoadOptions {
  forceBackend?: BackendKind;
  forcePrecision?: Precision;
  onProgress?: (event: unknown) => void;
}

export async function detectWebGPU(): Promise<{ available: boolean; adapter?: string }> {
  const gpu = (globalThis as unknown as { navigator?: { gpu?: unknown } }).navigator?.gpu as
    | { requestAdapter(): Promise<unknown> }
    | undefined;
  if (!gpu) return { available: false };
  try {
    const adapter = (await gpu.requestAdapter()) as
      | { info?: { vendor?: string; architecture?: string; description?: string } }
      | null;
    if (!adapter) return { available: false };
    const info = adapter.info ?? {};
    const label = [info.vendor, info.architecture, info.description]
      .filter(Boolean)
      .join(" / ");
    return { available: true, adapter: label || "WebGPU" };
  } catch {
    return { available: false };
  }
}

function defaultPrecision(backend: BackendKind): Precision {
  return backend === "webgpu" ? "fp16" : "q8";
}

export async function loadEmbedder(options: LoadOptions = {}): Promise<Embedder> {
  const started = performance.now();
  const detection = await detectWebGPU();
  const attempts: Array<{ backend: BackendKind; precision: Precision }> = [];

  if (options.forceBackend) {
    attempts.push({
      backend: options.forceBackend,
      precision: options.forcePrecision ?? defaultPrecision(options.forceBackend),
    });
  } else if (detection.available) {
    attempts.push({ backend: "webgpu", precision: "fp16" });
    attempts.push({ backend: "webgpu", precision: "q8" });
    attempts.push({ backend: "wasm", precision: "q8" });
  } else {
    attempts.push({ backend: "wasm", precision: "q8" });
  }

  const errors: string[] = [];
  for (const attempt of attempts) {
    try {
      const extractor = (await pipeline("feature-extraction", MODEL_ID, {
        device: attempt.backend,
        dtype: attempt.precision,
        revision: MODEL_REVISION,
        progress_callback: options.onProgress,
      })) as unknown as Embedder["extractor"];
      const info: EmbedderInfo = {
        backend: attempt.backend,
        precision: attempt.precision,
        modelId: MODEL_ID,
        revision: MODEL_REVISION,
        loadMs: Math.round(performance.now() - started),
        adapter: detection.adapter,
        fallbackReason: errors.length ? errors.join("; ") : undefined,
      };
      return { extractor, info };
    } catch (error) {
      errors.push(
        `${attempt.backend}/${attempt.precision}: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    }
  }

  throw new Error(`模型加载失败：${errors.join("; ")}`);
}

export interface EmbedOptions {
  batchSize?: number;
  onProgress?: (done: number, total: number) => void;
}

export async function embedTexts(
  embedder: Embedder,
  texts: string[],
  options: EmbedOptions = {},
): Promise<number[][]> {
  if (!texts.length) return [];
  const batchSize = Math.max(1, options.batchSize ?? 16);
  const vectors: number[][] = [];
  for (let start = 0; start < texts.length; start += batchSize) {
    const batch = texts.slice(start, start + batchSize);
    const output = await embedder.extractor(batch, { pooling: "mean", normalize: true });
    const rows = output.tolist();
    for (const row of rows) {
      vectors.push(row.map((value) => Number(value)));
    }
    options.onProgress?.(Math.min(texts.length, start + batch.length), texts.length);
  }
  return vectors;
}

export async function disposeEmbedder(embedder: Embedder | null): Promise<void> {
  if (!embedder) return;
  await embedder.extractor.dispose?.().catch(() => {});
}

export function backendLabel(info: EmbedderInfo | null): string {
  if (!info) return "加载中";
  return info.backend === "webgpu" ? "WebGPU" : "WASM";
}

export function precisionLabel(info: EmbedderInfo | null): string {
  if (!info) return "";
  if (info.precision === "fp16") return "FP16";
  if (info.precision === "q8") return "INT8";
  return "FP32";
}
