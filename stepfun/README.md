# 端侧知识库：浏览器向量化 + seekdb 混合检索

seekdb × 阶跃星辰赛题提交。应用在浏览器内用 Transformers.js 生成文档与查询向量，把向量、原文和元数据经 seekdb-js 写入 seekdb；查询由 seekdb 的 `hybridSearch()` 在数据库内完成全文与向量 RRF 融合。

## 功能

- 导入 `.txt` / `.md` 文件或一键加载示例语料；浏览器端分块、批处理向量化。
- 端侧后端选择：WebGPU FP16 → INT8 WebGPU → INT8 WASM 自动降级，界面显示当前后端、精度与适配器。
- seekdb 集合：HNSW 384 维 cosine + 全文索引（`space` analyzer）；`embeddingFunction: null`，向量只来自浏览器。
- 增量导入使用 `upsert`，每次导入后 `refresh_index()`；集合统计、刷新与清空。
- 基准页：FP32 / INT8 / FP16 矩阵、Recall@5 / nDCG@5、分阶段计时、JSON / CSV 导出。

## 快速开始

### 1. 启动 seekdb

二选一。

Docker：

```bash
docker run -d --name stepfun-seekdb -p 2881:2881 \
  -v "$PWD/.runtime/seekdb-docker:/var/lib/oceanbase" oceanbase/seekdb:latest
```

已有本地 seekdb 1.4+ 可执行文件：

```bash
SEEKDB_BIN=/path/to/seekdb npm run db:start
```

连接已运行的 seekdb / OceanBase：

```bash
cp .env.example .env
# 修改 SEEKDB_HOST / SEEKDB_PORT / SEEKDB_USER / SEEKDB_PASSWORD / SEEKDB_DATABASE
```

### 2. 安装并启动应用

```bash
npm install
npm run build
npm start
```

打开 <http://127.0.0.1:8787>。开发模式：

```bash
npm run dev   # API 127.0.0.1:8787 + Vite 127.0.0.1:5173
```

### 3. 使用

1. 点击“加载示例语料”，等待浏览器向量化与 seekdb 写入完成。
2. 输入自然语言问题，例如 `How does hybridSearch combine full-text and vector search results?`。
3. 导入自己的 `.txt` / `.md` 验证增量导入；可刷新索引或清空知识库。
4. 打开 <http://127.0.0.1:8787/benchmark.html> 运行性能与质量基准。

示例语料：17 篇 OceanBase seekDB 文档摘录、70 个文本块、25 条带标注查询。应用本身接受任意文本文件，不依赖 gold 或预解析正文。

## 模型与量化

- 模型：`Xenova/all-MiniLM-L6-v2`，revision `751bff37182d3f1213fa05d7196b954e230abad9`。
- 维度 384，`mean` pooling，L2 normalize。
- 同一口径的实际文件体积与压缩比：

| 精度 | 文件 | 体积 | 压缩比 |
| --- | --- | ---: | ---: |
| FP32 | `onnx/model.onnx` | 90,387,606 B | 1.00× |
| FP16 | `onnx/model_fp16.onnx` | 45,297,825 B | 2.00× |
| INT8 | `onnx/model_quantized.onnx` | 22,972,370 B | 3.93× |

- WebGPU 优先尝试 FP16；设备不支持 FP16 时尝试 INT8 WebGPU，再降级 WASM INT8。界面明确显示后端与精度。
- 首次加载从 Hugging Face 下载模型；模型文件由浏览器缓存，后续加载通常更快。
- 文档与查询向量化全部在浏览器执行；Node 后端只接收向量，不加载模型、不调用任何推理 API。

## seekdb 数据层

集合配置（`evidence/db/sdk-collections.json` 为实际落库记录）：

```json
{
  "vectorIndex": { "hnsw": { "dimension": 384, "distance": "cosine" } },
  "fulltextIndex": { "analyzer": "space", "properties": {} },
  "version": "v2"
}
```

- 写入：`collection.upsert({ ids, embeddings, documents, metadatas })`。
- 检索：对问题做离线关键词提取，交给 `whereDocument.$contains` 走全文通道；查询向量交给 `knn.queryEmbeddings`；`rank: { rrf: {} }` 由 seekdb 融合，客户端不分别取两路再拼合。
- 刷新：导入最后一批后调用 `collection.refresh_index()`。
- 持久化：数据位于 seekdb 数据目录，重启 seekdb 与应用后集合和检索结果保持一致。

## 语料与许可

示例语料摘自 OceanBase seekDB 文档（Apache-2.0，`oceanbase/oceanbase-skills`）。每个文本块在 `public/sample-corpus.json` 中保留 `source` 路径；许可说明同时写入该文件的 `license` 字段。25 条查询的 `relevant` 字段指向对应文本块。

## 测试

在 seekdb 已启动的前提下：

```bash
npm test                    # 分块、指标、全文关键词、WebGPU 检测
npm run test:integration    # seekdb 实际写入 / 混合检索 / 刷新
npm run test:e2e            # 构建后运行 Playwright 增量导入与检索
```

本环境已执行：14 个单元测试通过、3 个 seekdb 集成测试通过、1 个端到端测试通过（两次增量导入、两次检索、混合检索与网络来源记录）。

## 基准

```bash
# WASM 配置
npm run bench -- --mode=wasm --config=fp32-wasm
npm run bench -- --mode=wasm --config=int8-wasm

# WebGPU 配置（需要支持 WebGPU 的 Chromium / Chrome / Edge）
npm run bench -- --mode=webgpu --config=fp16-webgpu
npm run bench -- --mode=webgpu --config=int8-webgpu
```

本机实测汇总见 [TECHNICAL.md](TECHNICAL.md) 与 `evidence/benchmarks/`。本机 WebGPU 为 SwiftShader 软件适配器，不支持 FP16；INT8 WebGPU 已实跑，硬件 FP16 结果需在支持 `shader-f16` 的设备补跑。

## 证据

| 证据 | 位置 |
| --- | --- |
| 基准 JSON / 日志 | `evidence/benchmarks/` |
| 应用与基准截图 | `evidence/screenshots/` |
| 网络来源（仅本地 + HF/CDN） | `evidence/network/` |
| seekdb 集合配置与表清单 | `evidence/db/` |
| seekdb 重启前后对比 | `evidence/persistence/` |
| 模型文件体积 HEAD 实测 | `evidence/model-sizes.json` |
| 端到端测试日志 | `evidence/logs/e2e.log` |

证据索引与环境说明见 [evidence/README.md](evidence/README.md)。

## 目录结构

```text
stepfun/
├── server/             # Express API、seekdb-js 数据层
├── src/                # 知识库 UI、端侧 embedding、基准页
├── public/             # 示例语料
├── tests/              # 单元与 seekdb 集成测试
├── e2e/                # Playwright 端到端
├── scripts/            # seekdb 启动、开发、基准、证据
├── sample/             # 复现步骤与重置脚本
└── evidence/           # 实测证据
```

## 限制与说明

- 本机没有可用的硬件 GPU WebGPU；WebGPU 运行使用 SwiftShader，性能数字不等同于硬件结果。FP16 在该适配器上不可用，已记录失败日志与截图。
- `seekdb-js` 的 embedded 原生扩展在 Node 24 下会段错误，因此使用 seekdb server 模式；启动脚本支持 Docker 或 `SEEKDB_BIN` 本地二进制。
- 首次加载模型需要访问 Hugging Face；预取后可离线使用。
- 基准查询集为 25 条演示查询，质量为该集合上的实测值，不代表生产语料。
