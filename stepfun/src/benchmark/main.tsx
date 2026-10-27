import { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import "../app.css";
import "./benchmark.css";
import { detectWebGPU } from "../lib/embeddings";
import { BENCH_CONFIGS, benchError, runBenchmark, type BenchConfig, type BenchResult } from "./runner";

declare global {
  interface Window {
    __BENCH_RESULT__?: BenchResult[];
  }
}

function formatBytes(value: number): string {
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function formatMs(value: number): string {
  return `${value.toFixed(1)} ms`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function csvCell(value: string | number): string {
  const text = String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

function BenchmarkApp() {
  const params = new URLSearchParams(window.location.search);
  const onlyConfig = params.get("config");
  const autorun = params.get("autorun") === "1";
  const [webgpu, setWebgpu] = useState<{ available: boolean; adapter?: string } | null>(null);
  const [selected, setSelected] = useState<string[]>(
    onlyConfig ? [onlyConfig] : ["fp32-wasm", "int8-wasm"],
  );
  const [running, setRunning] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const [results, setResults] = useState<BenchResult[]>([]);
  const [error, setError] = useState("");
  const started = useRef(false);

  useEffect(() => {
    detectWebGPU().then(setWebgpu).catch(() => setWebgpu({ available: false }));
  }, []);

  useEffect(() => {
    if (onlyConfig || webgpu === null || webgpu.available) return;
    setSelected((current) => current.filter((id) => !id.endsWith("-webgpu")));
  }, [webgpu, onlyConfig]);

  function addLog(message: string) {
    setLogs((current) => [...current, message]);
  }

  async function run(configsToRun: BenchConfig[]) {
    if (running || !configsToRun.length) return;
    setRunning(true);
    setError("");
    setLogs([]);
    setResults([]);
    window.__BENCH_RESULT__ = [];
    const collected: BenchResult[] = [];
    for (const config of configsToRun) {
      try {
        addLog(`开始 ${config.label}`);
        const result = await runBenchmark(config, addLog);
        collected.push(result);
        setResults([...collected]);
        window.__BENCH_RESULT__ = [...collected];
        addLog(
          `完成 ${config.label}: Recall@5 ${formatPercent(result.quality.recall)} / E2E ${formatMs(
            result.timings.endToEndMsMedian,
          )}`,
        );
      } catch (cause) {
        const message = benchError(config, cause);
        addLog(`失败 ${message}`);
        setError(message);
        break;
      }
    }
    setRunning(false);
  }

  useEffect(() => {
    if (!autorun || !webgpu || started.current) return;
    started.current = true;
    const configsToRun = BENCH_CONFIGS.filter((config) => selected.includes(config.id));
    void run(configsToRun);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autorun, webgpu]);

  const chosen = BENCH_CONFIGS.filter((config) => selected.includes(config.id));

  function toggle(id: string) {
    setSelected((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );
  }

  function exportJson() {
    const blob = new Blob([JSON.stringify(results, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "stepfun-benchmark.json";
    link.click();
    URL.revokeObjectURL(url);
  }

  function exportCsv() {
    const header = [
      "config",
      "backend",
      "precision",
      "model_bytes",
      "compression_ratio",
      "model_load_ms",
      "model_download_ms",
      "embedding_ms",
      "embedding_throughput",
      "import_refresh_ms",
      "query_embedding_ms",
      "search_ms",
      "end_to_end_ms",
      "recall_at_5",
      "ndcg_at_5",
      "webgpu_adapter",
    ];
    const rows = results.map((result) =>
      [
        result.config.id,
        result.config.backend,
        result.config.precision,
        result.model.sizeBytes,
        result.model.compressionRatio.toFixed(3),
        result.model.loadMs,
        result.model.downloadMs.toFixed(1),
        result.timings.documentEmbeddingMs.toFixed(1),
        result.timings.documentEmbeddingThroughput.toFixed(3),
        result.timings.importRefreshMs.toFixed(1),
        result.timings.queryEmbeddingMsMedian.toFixed(1),
        result.timings.searchMsMedian.toFixed(1),
        result.timings.endToEndMsMedian.toFixed(1),
        result.quality.recall.toFixed(4),
        result.quality.ndcg.toFixed(4),
        result.environment.webgpuAdapter ?? "",
      ]
        .map(csvCell)
        .join(","),
    );
    const blob = new Blob([[header.join(","), ...rows].join("\n")], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "stepfun-benchmark.csv";
    link.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="shell">
      <header className="hero bench-hero">
        <div className="hero-main">
          <p className="eyebrow">seekdb × 阶跃星辰 · 基准</p>
          <h1>性能与质量基准</h1>
          <p className="lede">
            同一语料、同一查询集，对比 FP32 基线、INT8 + WASM 与 FP16 + WebGPU。
            每项重复 5 次取中位数，结果写入可下载的 JSON / CSV。
          </p>
        </div>
        <div className="hero-side">
          <div className={`badge ${webgpu?.available ? "badge-gpu" : "badge-wasm"}`}>
            <span className="badge-dot" />
            {webgpu === null ? "检测 WebGPU" : webgpu.available ? "WebGPU 可用" : "WebGPU 不可用"}
          </div>
          <a className="ghost-link" href="/">
            返回知识库
          </a>
        </div>
      </header>

      <section className="card bench-controls">
        <div className="section-title">
          <h2>测试配置</h2>
          <span className="muted">
            {webgpu?.adapter ? `适配器：${webgpu.adapter}` : "WebGPU 适配器未检测到"}
          </span>
        </div>
        <div className="bench-checks">
          {BENCH_CONFIGS.map((config) => {
            const disabled = config.backend === "webgpu" && webgpu !== null && !webgpu.available;
            return (
              <label className={`bench-check ${disabled ? "disabled" : ""}`} key={config.id}>
                <input
                  type="checkbox"
                  checked={selected.includes(config.id)}
                  disabled={disabled || running}
                  onChange={() => toggle(config.id)}
                />
                <span>{config.label}</span>
              </label>
            );
          })}
        </div>
        <div className="actions">
          <button
            className="primary"
            type="button"
            disabled={running || chosen.length === 0}
            onClick={() => void run(chosen)}
          >
            {running ? "运行中" : "运行基准"}
          </button>
          <button type="button" disabled={!results.length} onClick={exportJson}>
            导出 JSON
          </button>
          <button type="button" disabled={!results.length} onClick={exportCsv}>
            导出 CSV
          </button>
        </div>
        {error ? <p className="error">{error}</p> : null}
        <p className="muted">
          WebGPU 在无硬件 GPU 的环境中可能是 SwiftShader 软件适配器；性能结论以运行时记录的
          environment.webgpuAdapter 为准，不把软件适配器数字当作硬件结果。
        </p>
      </section>

      {logs.length ? (
        <section className="card">
          <div className="section-title">
            <h2>运行日志</h2>
            <span className="muted">{logs.length} 条</span>
          </div>
          <pre className="bench-log">{logs.join("\n")}</pre>
        </section>
      ) : null}

      {results.map((result) => (
        <section className="card bench-result" key={result.config.id}>
          <div className="section-title">
            <h2>{result.config.label}</h2>
            <span className="muted">
              {result.config.backend} · {result.config.precision}
            </span>
          </div>
          <div className="bench-metrics">
            <div>
              <span>模型体积</span>
              <strong>{formatBytes(result.model.sizeBytes)}</strong>
            </div>
            <div>
              <span>压缩比</span>
              <strong>{result.model.compressionRatio.toFixed(2)}×</strong>
            </div>
            <div>
              <span>模型下载</span>
              <strong>{formatMs(result.model.downloadMs)}</strong>
            </div>
            <div>
              <span>加载</span>
              <strong>{formatMs(result.model.loadMs)}</strong>
            </div>
            <div>
              <span>向量化吞吐</span>
              <strong>{result.timings.documentEmbeddingThroughput.toFixed(1)} 块/秒</strong>
            </div>
            <div>
              <span>写入 + 刷新</span>
              <strong>{formatMs(result.timings.importRefreshMs)}</strong>
            </div>
            <div>
              <span>查询向量化</span>
              <strong>{formatMs(result.timings.queryEmbeddingMsMedian)}</strong>
            </div>
            <div>
              <span>seekdb 检索</span>
              <strong>{formatMs(result.timings.searchServerMsMedian)}</strong>
            </div>
            <div>
              <span>端到端</span>
              <strong>{formatMs(result.timings.endToEndMsMedian)}</strong>
            </div>
            <div>
              <span>检索模式</span>
              <strong>
                {Object.entries(result.timings.searchModes)
                  .map(([name, count]) => `${name} × ${count}`)
                  .join(" / ")}
              </strong>
            </div>
            <div>
              <span>Recall@5</span>
              <strong>{formatPercent(result.quality.recall)}</strong>
            </div>
            <div>
              <span>nDCG@5</span>
              <strong>{formatPercent(result.quality.ndcg)}</strong>
            </div>
          </div>
          <p className="muted">
            语料 {result.corpus.documents} 篇 / {result.corpus.chunks} 块 /{" "}
            {result.corpus.queries} 个查询 · 设备 {result.environment.platform} · 并发{" "}
            {result.environment.hardwareConcurrency} · 适配器{" "}
            {result.environment.webgpuAdapter ?? "无"}
          </p>
          <details>
            <summary>逐查询结果</summary>
            <table className="bench-table">
              <thead>
                <tr>
                  <th>查询</th>
                  <th>模式</th>
                  <th>全文关键词</th>
                  <th>Recall@5</th>
                  <th>nDCG@5</th>
                  <th>检索耗时</th>
                  <th>端到端</th>
                </tr>
              </thead>
              <tbody>
                {result.quality.perQuery.map((row) => (
                  <tr key={row.id}>
                    <td>{row.question}</td>
                    <td>{row.searchMode}</td>
                    <td className="mono">{row.fullTextQuery || "—"}</td>
                    <td>{formatPercent(row.recall)}</td>
                    <td>{formatPercent(row.ndcg)}</td>
                    <td>{formatMs(row.searchMs)}</td>
                    <td>{formatMs(row.endToEndMs)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </section>
      ))}

      <footer className="footer">
        <span>推理：Transformers.js · 模型 all-MiniLM-L6-v2</span>
        <span>重排与融合：seekdb hybridSearch · RRF</span>
      </footer>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<BenchmarkApp />);
