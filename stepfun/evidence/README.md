# 证据索引

运行环境：Ubuntu 24.04（WSL2）、Node 24.18.0、Playwright Chromium、seekdb 1.4.0 本地 server、Transformers.js 4.3.1。本机 WebGPU 为 SwiftShader 软件适配器，不支持 FP16；硬件 FP16 需在支持 `shader-f16` 的设备补跑。

## 基准

| 文件 | 内容 |
| --- | --- |
| `benchmarks/benchmark-fp32-wasm.json` | FP32 基线 · WASM：Recall@5 80.0%、nDCG@5 58.5%、E2E 38.6 ms |
| `benchmarks/benchmark-int8-wasm.json` | INT8 · WASM：Recall@5 84.0%、nDCG@5 63.2%、E2E 29.1 ms |
| `benchmarks/benchmark-int8-webgpu.json` | INT8 · WebGPU（SwiftShader）：Recall@5 84.0%、nDCG@5 61.4%、E2E 205.4 ms |
| `benchmarks/benchmark-int8-wasm-docker.json` | Docker seekdb（2882）INT8 · WASM：Recall@5 84.0%、nDCG@5 63.2%、E2E 30.1 ms，验证 Docker 启动路径 |
| `benchmarks/fp16-webgpu-docker-state.json` | 用户硬件浏览器在 Docker seekdb（2882）实例上通过应用以 WebGPU FP16 导入示例语料，`stepfun_kb_fp16` 70 条 |
| `benchmarks/benchmark-fp16-webgpu.log` | FP16 · WebGPU 失败记录：适配器不支持 `shader-f16` |
| `benchmarks/*.log` | 每个配置的逐阶段运行日志 |
| `model-sizes.json` | HF 文件 HEAD 实测体积，与 `MODEL_SIZES` 常量一致 |

所有 JSON 包含模型 revision、体积、压缩比、下载/加载时间、分阶段计时、逐查询 Recall/nDCG、全文关键词、检索模式、设备与适配器，以及每次查询 5 次原始样本。三组基准使用同一 Chromium 构建；WebGPU 为 SwiftShader。

## 截图

| 文件 | 内容 |
| --- | --- |
| `screenshots/e2e-wasm-search.png` | WASM INT8 应用：两次增量导入、检索结果、关键词与集合统计 |
| `screenshots/app-webgpu.png` | WebGPU INT8（SwiftShader）应用：后端与适配器徽标、检索结果 |
| `screenshots/benchmark-*.png` | 各基准配置运行页 |
| `screenshots/benchmark-fp16-webgpu-webgpu.png` | FP16 不被 SwiftShader 支持的失败页 |

## 网络

`network/e2e-origins.json`、`network/webgpu-origins.json`：浏览器请求来源。仅本地 API、`huggingface.co` 模型文件、HF CDN 与 jsDelivr；没有云端推理端点。

## 数据库

- `model-sizes.json`：按 revision 对三个 ONNX 文件 HEAD 实测的字节数。
- `db/sdk-collections.json`：seekdb 实际落库的集合配置，HNSW 384 维 cosine、全文 `space` analyzer、v2 schema。
- `db/collection-describe.json`：`stepfun_kb_int8` 的集合描述。
- `db/tables.json`：seekdb 中的表清单（含内部 `c$v2$...` 表与 `sdk_collections`）。

## 持久化

- `persistence/before-restart.json`：重启前 `stepfun_kb_int8` 计数 2，混合检索返回 `audit-...` 与 `demo-...`。
- `persistence/after-restart.json`：重启 seekdb 与应用后计数仍为 2，结果与重启前一致。
- `persistence/after-restart-browser.json` / `.png`：重启后用真实浏览器模型生成查询向量检索，返回 `rotation policy` 文本块，证明持久化对真实查询有效。

## 日志

- `logs/e2e.log`：Playwright 端到端测试通过记录（增量导入 + 检索 + 网络来源）。

## 复跑命令

```bash
npm test
SEEKDB_TEST=1 npx vitest run tests/db.integration.test.ts
npm run test:e2e   # 本机 Playwright 需补齐 Chromium 运行库；标准 Chrome/Edge 无需

npm run bench -- --mode=wasm --config=fp32-wasm
npm run bench -- --mode=wasm --config=int8-wasm
npm run bench -- --mode=webgpu --config=int8-webgpu
npm run bench -- --mode=webgpu --config=fp16-webgpu
```

本机运行 Playwright 需要补齐 Chromium 运行库；评审在标准 Chrome/Edge 上运行不需要。全量测试与硬件 WebGPU/FP16 基准可由评审手动执行。
