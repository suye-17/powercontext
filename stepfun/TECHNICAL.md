# 一页技术说明

## 1. 模型与量化

| 项目 | 内容 |
| --- | --- |
| 模型 | `Xenova/all-MiniLM-L6-v2` |
| Revision | `751bff37182d3f1213fa05d7196b954e230abad9` |
| 维度 / 池化 | 384 / mean pooling + L2 normalize |
| FP32 | `model.onnx`，90,387,606 B，压缩比 1.00× |
| FP16 | `model_fp16.onnx`，45,297,825 B，压缩比 2.00× |
| INT8 | `model_quantized.onnx`，22,972,370 B，压缩比 3.93× |
| 量化方式 | 直接使用模型仓库提供的 ONNX FP16 / INT8(q8) 权重，由 Transformers.js 按 `dtype` 选择；未在应用内重新训练 |
| 体积实测 | 按 revision 对 HF 文件 HEAD 实测：`evidence/model-sizes.json`（与上表字节一致） |

质量对照（同一语料 17 篇 / 70 块 / 25 条查询，Recall@5 与 nDCG@5）：

| 精度 | Recall@5 | nDCG@5 | 相对 FP32 |
| --- | ---: | ---: | --- |
| FP32 基线 | 80.0% | 58.5% | — |
| INT8 | 84.0% | 63.2% | +4.0pp / +4.7pp |
| FP16 | 未在本机执行 | 未在本机执行 | SwiftShader 不支持 `shader-f16` |

INT8 在该演示集上没有质量下降；小集合上略高属于采样波动，不宣称量化提升质量。

## 2. 端侧运行

- 运行时：Transformers.js `4.3.1`，`feature-extraction` pipeline；文档与查询都在浏览器执行。
- 后端选择：`navigator.gpu.requestAdapter()` 可用时优先 `webgpu + fp16`；设备拒绝 FP16 时尝试 `webgpu + q8`；仍失败则降级 `wasm + q8`。界面显示 `WebGPU/WASM`、`FP16/INT8` 与适配器名称。
- 降级验证：在 Playwright 中把 `navigator.gpu` 置空，应用显示 `WASM INT8`，导入、检索与结果展示正常（`evidence/screenshots/e2e-wasm-search.png`）。
- 无云端推理：浏览器网络来源仅本地 API、Hugging Face 模型文件与 CDN（`evidence/network/`）；后端没有模型依赖。

## 3. seekdb 使用

- 通过 `seekdb`（seekdb-js）`1.4.0` 以 server 模式连接本地 seekdb 1.4.0。
- 集合：HNSW 384 维 `cosine`；全文索引 `space` analyzer；`embeddingFunction: null`，向量由浏览器提供。实际落库配置见 `evidence/db/sdk-collections.json`。
- 写入：`upsert`（重复 ID 幂等），最后一批后 `refresh_index()`。
- 混合检索：问题经离线关键词提取后作为 `whereDocument.$contains` 全文通道，查询向量作为 `knn.queryEmbeddings`，`rank: { rrf: {} }` 由 seekdb 完成融合；客户端不分别取两路结果再拼合。
- 增量与状态：导入批次 1 → 查询；批次 2 → 查询；`stepfun_kb_int8` 计数从 1 增至 2。重启 seekdb 与应用后计数仍为 2，检索结果一致（`evidence/persistence/`）。

## 4. 实测性能

测试条件：Ubuntu 24.04（WSL2）、Node 24.18.0、Playwright Chromium、同一 70 块语料与 25 条查询、batch size 16、每次查询重复 5 次取中位数、seekdb 1.4.0 本地 server。

| 配置 | 模型体积 | 文档向量化 | 写入 + 刷新 | 查询向量化 | seekdb 检索（服务端） | 端到端 | Recall@5 | nDCG@5 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| FP32 基线 · WASM | 90.4 MB | 63.2 s（1.11 块/秒） | 986 ms | 22.3 ms | 6 ms | 38.6 ms | 80.0% | 58.5% |
| INT8 · WASM | 23.0 MB | 39.8 s（1.76 块/秒） | 1,047 ms | 13.8 ms | 6 ms | 29.1 ms | 84.0% | 63.2% |
| INT8 · WebGPU（SwiftShader） | 23.0 MB | 321.7 s（0.22 块/秒） | 1,202 ms | 187.4 ms | 7 ms | 205.4 ms | 84.0% | 61.4% |
| FP16 · WebGPU | 45.3 MB | 未执行 | — | — | — | — | — | — |

- 模型下载 / 加载：FP32 12.9 s / 15.6 s；INT8 7.1 s / 8.0 s（WASM）、6.5 s / 8.0 s（WebGPU，SwiftShader）。下载为本次页面模型、tokenizer 与 config 资源耗时之和；加载为 pipeline 初始化总耗时。加载与预热单独计时，查询指标为预热后 5 次中位数，原始样本保存在每个基准 JSON 的 `perQuery[].raw`。
- “seekdb 检索（服务端）”为 `hybridSearch` 的服务端耗时；客户端往返见 JSON 的 `searchMsMedian`（约 14–15 ms）。端到端包含查询向量化、客户端往返与一次渲染，因此不是“查询向量化 + 服务端检索”的简单相加。
- 三组基准使用同一 Chromium 构建（见 JSON `userAgent`）；WebGPU 为 SwiftShader 软件适配器。硬件 FP16 需在支持 `shader-f16` 的设备补跑。
- 优化点：batch 16 向量化、128 条/批写入、仅在最后一批刷新索引、问题离线关键词提取、复用同一集合。
- 重要限制：本机 WebGPU 为 SwiftShader 软件适配器，INT8 WebGPU 的端到端延迟不代表硬件 WebGPU；硬件 FP16 行需在支持 `shader-f16` 的 Chrome/Edge 上补跑，命令与页面已提供。

## 5. 复现与证据

- 快速开始、基准命令与目录见 [README.md](README.md)；干净状态步骤见 [sample/steps.md](sample/steps.md)。
- 基准原始 JSON / 日志：`evidence/benchmarks/`；截图：`evidence/screenshots/`；数据库配置：`evidence/db/`；重启对比：`evidence/persistence/`；网络来源：`evidence/network/`。
- 语料来自 OceanBase seekDB 文档（Apache-2.0），每块保留来源路径；未使用答案或预解析正文作为运行时输入。
