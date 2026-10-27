import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import "./app.css";
import { api } from "./lib/backend";
import { chunkDocument } from "./lib/chunk";
import {
  backendLabel,
  disposeEmbedder,
  embedTexts,
  loadEmbedder,
  precisionLabel,
  type LoadOptions,
} from "./lib/embeddings";
import type {
  Chunk,
  ImportItem,
  SampleCorpus,
  SearchResponse,
  StatsResponse,
} from "./lib/types";

type ImportStage = "idle" | "embedding" | "writing" | "refreshing" | "done";

interface ImportState {
  running: boolean;
  stage: ImportStage;
  done: number;
  total: number;
  message: string;
}

const EMPTY_IMPORT: ImportState = {
  running: false,
  stage: "idle",
  done: 0,
  total: 0,
  message: "",
};

function collectionFor(precision: string): string {
  if (precision === "fp16") return "stepfun_kb_fp16";
  if (precision === "fp32") return "stepfun_kb_fp32";
  return "stepfun_kb_int8";
}

function progressText(event: unknown): string {
  if (!event || typeof event !== "object") return "下载模型";
  const value = event as { status?: string; file?: string; progress?: number };
  if (value.status === "progress" && value.file) {
    const percent = typeof value.progress === "number" ? ` ${Math.round(value.progress)}%` : "";
    return `${value.file}${percent}`;
  }
  if (value.status === "done" && value.file) return `${value.file} 完成`;
  return value.status ?? "加载模型";
}

function App() {
  const [embedder, setEmbedder] = useState<Awaited<ReturnType<typeof loadEmbedder>> | null>(null);
  const [modelState, setModelState] = useState<"loading" | "ready" | "error">("loading");
  const [modelError, setModelError] = useState("");
  const [modelProgress, setModelProgress] = useState("准备加载模型");
  const [collection, setCollection] = useState("");
  const [stats, setStats] = useState<StatsResponse | null>(null);
  const [corpus, setCorpus] = useState<SampleCorpus | null>(null);
  const [importState, setImportState] = useState<ImportState>(EMPTY_IMPORT);
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(10);
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const fileInput = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    let cancelled = false;
    const options: LoadOptions = {
      onProgress: (event) => {
        if (!cancelled) setModelProgress(progressText(event));
      },
    };
    loadEmbedder(options)
      .then((loaded) => {
        if (cancelled) {
          void disposeEmbedder(loaded);
          return;
        }
        setEmbedder(loaded);
        setModelState("ready");
        setCollection(collectionFor(loaded.info.precision));
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setModelState("error");
        setModelError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!collection) return;
    let cancelled = false;
    api
      .stats(collection)
      .then((value) => {
        if (!cancelled) setStats(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [collection, notice]);

  useEffect(() => () => void disposeEmbedder(embedder), [embedder]);

  async function loadCorpus(): Promise<SampleCorpus> {
    if (corpus) return corpus;
    const response = await fetch("/sample-corpus.json");
    if (!response.ok) throw new Error(`加载示例语料失败：HTTP ${response.status}`);
    const value = (await response.json()) as SampleCorpus;
    setCorpus(value);
    return value;
  }

  function corpusItems(value: SampleCorpus): Chunk[] {
    return value.documents.flatMap((document) =>
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

  async function importChunks(items: Chunk[]) {
    if (!embedder) {
      setError("模型尚未就绪");
      return;
    }
    if (!items.length) {
      setError("没有可导入的文本");
      return;
    }
    const capped = items.slice(0, 512);
    const truncated = items.length > capped.length;
    setError("");
    setNotice("");
    setImportState({ running: true, stage: "embedding", done: 0, total: capped.length, message: "浏览器端向量化" });
    try {
      const vectors = await embedTexts(embedder, capped.map((item) => item.text), {
        batchSize: 16,
        onProgress: (done, total) =>
          setImportState({
            running: true,
            stage: "embedding",
            done,
            total,
            message: `浏览器端向量化 ${done}/${total}`,
          }),
      });
      const importItems: ImportItem[] = capped.map((item, index) => ({
        ...item,
        embedding: vectors[index],
      }));
      setImportState({
        running: true,
        stage: "refreshing",
        done: importItems.length,
        total: importItems.length,
        message: "写入 seekdb 并刷新索引",
      });
      const result = await api.importItems(collection, importItems, true);
      const imported = result.imported;
      const latestCount = result.count;
      setStats({ collection, exists: true, count: latestCount });
      setImportState({
        running: false,
        stage: "done",
        done: importItems.length,
        total: importItems.length,
        message: `已导入 ${imported} 条，当前 ${latestCount} 条`,
      });
      setNotice(
        `导入完成：${imported} 条文本块，集合 ${latestCount} 条${
          truncated ? "（单次最多 512 条，已截断）" : ""
        }`,
      );
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : String(cause);
      setImportState({ ...EMPTY_IMPORT, message: "导入失败" });
      setError(message);
    }
  }

  async function importSample() {
    try {
      const value = await loadCorpus();
      await importChunks(corpusItems(value));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function importFiles(files: FileList | null) {
    if (!files?.length) return;
    try {
      const texts = await Promise.all(
        Array.from(files).map(async (file) => ({
          name: file.name,
          content: await file.text(),
        })),
      );
      const items = texts.flatMap((file) => chunkDocument(file.name, file.content));
      await importChunks(items);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (fileInput.current) fileInput.current.value = "";
    }
  }

  async function runSearch() {
    if (!embedder || !query.trim()) return;
    setSearching(true);
    setError("");
    setNotice("");
    try {
      const vectors = await embedTexts(embedder, [query.trim()], { batchSize: 1 });
      const result = await api.search(collection, query.trim(), vectors[0], limit);
      setSearchResult(result);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setSearching(false);
    }
  }

  async function refreshIndex() {
    try {
      const result = await api.refresh(collection);
      setNotice(`索引已刷新（${result.durationMs} ms）`);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  async function resetCollection() {
    if (!window.confirm(`确定清空 ${collection} 吗？该操作会删除集合中的向量与原文。`)) return;
    try {
      await api.reset(collection);
      setStats({ collection, exists: true, count: 0 });
      setSearchResult(null);
      setNotice("知识库已清空");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  }

  const info = embedder?.info ?? null;
  const busy = importState.running;
  const ready = modelState === "ready" && !!embedder;
  const progressPercent =
    importState.total > 0 ? Math.round((importState.done / importState.total) * 100) : 0;

  return (
    <div className="shell">
      <header className="hero">
        <div className="hero-main">
          <p className="eyebrow">seekdb × 阶跃星辰 · 端侧 RAG</p>
          <h1>端侧知识库</h1>
          <p className="lede">
            文档在浏览器本地向量化，向量、原文与元数据写入 seekdb，查询由 seekdb
            完成全文与向量混合检索。
          </p>
        </div>
        <div className="hero-side">
          <div className={`badge ${info?.backend === "webgpu" ? "badge-gpu" : "badge-wasm"}`}>
            <span className="badge-dot" />
            {modelState === "loading" ? "模型加载中" : modelState === "error" ? "模型不可用" : backendLabel(info)}
            {info ? <strong>{precisionLabel(info)}</strong> : null}
          </div>
          <a className="ghost-link" href="/benchmark.html">
            性能与质量基准
          </a>
        </div>
      </header>

      {modelState !== "ready" ? (
        <section className="card model-card">
          <div className="section-title">
            <h2>{modelState === "error" ? "模型加载失败" : "正在加载端侧模型"}</h2>
            <span className="muted">{MODEL_LABEL}</span>
          </div>
          {modelState === "error" ? (
            <p className="error">{modelError}</p>
          ) : (
            <>
              <div className="progress">
                <div className="progress-bar" style={{ width: "35%" }} />
              </div>
              <p className="muted">{modelProgress}</p>
            </>
          )}
        </section>
      ) : null}

      <main className="grid">
        <section className="card">
          <div className="section-title">
            <h2>知识库</h2>
            <span className="muted">{collection}</span>
          </div>
          <p className="muted">
            {info?.backend === "webgpu"
              ? `WebGPU · ${precisionLabel(info)} · ${info.adapter ?? "适配器未知"}`
              : `WASM · ${precisionLabel(info)} · 无 WebGPU 时自动降级`}
          </p>

          <div className="actions">
            <button className="primary" type="button" onClick={importSample} disabled={!ready || busy}>
              加载示例语料
            </button>
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              disabled={!ready || busy}
            >
              导入 txt / md
            </button>
            <input
              ref={fileInput}
              className="hidden"
              type="file"
              accept=".txt,.md,.markdown,text/plain,text/markdown"
              multiple
              onChange={(event) => void importFiles(event.target.files)}
            />
          </div>

          {busy || importState.stage === "done" ? (
            <div className="import-status">
              <div className="progress">
                <div className="progress-bar" style={{ width: `${progressPercent}%` }} />
              </div>
              <p className="muted">{importState.message}</p>
            </div>
          ) : null}

          <dl className="stats">
            <div>
              <dt>集合</dt>
              <dd>{collection || "—"}</dd>
            </div>
            <div>
              <dt>文本块</dt>
              <dd>{stats?.count ?? 0}</dd>
            </div>
            <div>
              <dt>模型</dt>
              <dd>{MODEL_LABEL}</dd>
            </div>
            <div>
              <dt>模型版本</dt>
              <dd className="mono">{info?.revision?.slice(0, 12) ?? "—"}</dd>
            </div>
          </dl>

          <div className="actions secondary">
            <button type="button" onClick={() => void refreshIndex()} disabled={!ready || busy}>
              刷新索引
            </button>
            <button type="button" className="danger" onClick={() => void resetCollection()} disabled={!ready || busy}>
              清空知识库
            </button>
          </div>

          {notice ? <p className="notice">{notice}</p> : null}
          {error ? <p className="error">{error}</p> : null}
        </section>

        <section className="card">
          <div className="section-title">
            <h2>检索</h2>
            <span className="muted">seekdb hybridSearch · RRF</span>
          </div>
          <form
            className="search"
            onSubmit={(event) => {
              event.preventDefault();
              void runSearch();
            }}
          >
            <input
              type="search"
              value={query}
              placeholder="例如：How does seekdb combine full-text and vector results?"
              onChange={(event) => setQuery(event.target.value)}
              disabled={!ready || searching}
            />
            <select
              value={limit}
              onChange={(event) => setLimit(Number(event.target.value))}
              disabled={!ready || searching}
            >
              <option value={5}>5 条</option>
              <option value={10}>10 条</option>
              <option value={20}>20 条</option>
            </select>
            <button className="primary" type="submit" disabled={!ready || searching || !query.trim()}>
              {searching ? "检索中" : "检索"}
            </button>
          </form>

          {searchResult ? (
            <div className="search-meta">
              <span>
                {searchResult.mode === "hybrid" ? "全文 + 向量混合检索" : "向量回退检索"} ·{" "}
                {searchResult.durationMs} ms
                {searchResult.fullTextQuery ? ` · 关键词：${searchResult.fullTextQuery}` : ""}
              </span>
              <span>{searchResult.results.length} 条结果</span>
            </div>
          ) : (
            <p className="muted">导入示例语料后，输入自然语言问题即可检索证据原文。</p>
          )}

          <ol className="results">
            {searchResult?.results.map((result) => (
              <li key={`${result.id}-${result.rank}`}>
                <div className="result-head">
                  <span className="rank">#{result.rank}</span>
                  <strong>{String(result.metadata.title ?? "未命名文档")}</strong>
                  <span className="distance">
                    {result.distance === null ? "—" : result.distance.toFixed(4)}
                  </span>
                </div>
                <p className="source">
                  {String(result.metadata.source ?? "")} · chunk{" "}
                  {String(result.metadata.chunkIndex ?? "?")}
                </p>
                <p className="snippet">{result.text}</p>
              </li>
            ))}
          </ol>
        </section>
      </main>

      <footer className="footer">
        <span>推理：Transformers.js · 模型 {MODEL_LABEL}</span>
        <span>存储与检索：seekdb 1.4 · seekdb-js</span>
      </footer>
    </div>
  );
}

const MODEL_LABEL = "all-MiniLM-L6-v2";

createRoot(document.getElementById("root")!).render(<App />);
