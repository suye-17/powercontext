# 复现步骤

以下命令在 `stepfun/` 目录执行。默认 seekdb 监听 `127.0.0.1:2881`，应用监听 `127.0.0.1:8787`。

## 0. 环境

- Node.js 20+（本环境 24.18.0）
- seekdb 1.4+（Docker，或本地 `seekdb` 可执行文件）
- Chrome / Edge 最新版；WebGPU 可选

## 1. 启动 seekdb

Docker：

```bash
docker run -d --name stepfun-seekdb -p 2881:2881 \
  -v "$PWD/.runtime/seekdb-docker:/var/lib/oceanbase" oceanbase/seekdb:latest
```

或本地二进制：

```bash
SEEKDB_BIN=/path/to/seekdb npm run db:start
```

已有实例：复制 `.env.example` 为 `.env`，填写 `SEEKDB_HOST` / `SEEKDB_PORT` / `SEEKDB_USER` / `SEEKDB_PASSWORD` / `SEEKDB_DATABASE`。

## 2. 安装、构建、启动

```bash
npm install
npm run build
npm start
```

打开 <http://127.0.0.1:8787>。

## 3. 导入示例语料

点击“加载示例语料”。预期：

- 文档 17 篇、文本块 70 个；
- 浏览器完成向量化后写入 seekdb；
- “文本块”统计显示 `70`；
- 徽标显示 `WASM INT8`（无 WebGPU）或 `WebGPU INT8/FP16`（有 WebGPU）。

## 4. 检索

输入：

```text
How does hybridSearch combine full-text and vector search results?
```

预期：返回 `hybrid-sample` 文档的文本块；结果区显示“全文 + 向量混合检索”、关键词、耗时与原文。再输入：

```text
How does REFRESH_INDEX incrementally refresh a vector index?
```

预期：返回 `refresh-index` 文档中包含 `REFRESH_INDEX` 的文本块。

## 5. 增量导入

准备 `audit.txt`：

```text
The rotation policy keeps the audit log for ninety days. Audit records are immutable.
```

通过“导入 txt / md”上传，等待“文本块”统计从 70 增至 71。搜索：

```text
How long is the audit log kept?
```

预期：新文本块可检索，结果包含 `rotation policy`。

## 6. 重置

```bash
sample/reset.sh
```

或在界面点击“清空知识库”。重置只清空集合，不删除 seekdb 数据目录。

## 7. 基准

打开 <http://127.0.0.1:8787/benchmark.html>：

1. 勾选 `FP32 基线 · WASM` 与 `INT8 · WASM`，点击“运行基准”。
2. 在支持 WebGPU 的 Chrome/Edge 中勾选 `FP16 · WebGPU`；无硬件 WebGPU 时会显示降级或错误。
3. 导出 JSON / CSV，或查看 `evidence/benchmarks/` 中已保存的实测结果。

命令行方式（Playwright）：

```bash
npm run bench -- --mode=wasm --config=int8-wasm
npm run bench -- --mode=webgpu --config=int8-webgpu
```

## 8. 测试

```bash
npm test
npm run test:integration
npm run test:e2e
```

- `test:integration` 与 `test:e2e` 需要 seekdb 已启动。
- 首次端到端测试会下载模型；之后使用浏览器缓存。
- 全量测试与基准矩阵可由评审/作者手动执行；本仓库 `evidence/` 保存了实际运行结果。
