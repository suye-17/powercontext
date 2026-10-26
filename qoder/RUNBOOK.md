# 运行手册

本手册覆盖安装、启动、验证、重置和排错。所有命令都从仓库根目录开始；`qoder/` 是提交目录。

## 1. 环境要求

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 操作系统 | Linux 或 macOS | `pylibseekdb` 仅提供这两个平台的 wheel |
| Python | >= 3.11 | 开发与验证使用 3.12.3 |
| uv | 任意近期版本 | 用于创建应用虚拟环境并锁定依赖 |
| Docker | 不需要 | seekdb 以嵌入式库运行，不依赖容器 |
| 模型服务 | 可选 | 缺失时自动回退为全文检索 + 抽取式摘要 |

不需要预装 OceanBase 或单独部署 seekdb。PowerContext 以 Python 依赖安装在应用虚拟环境中。

## 2. 安装

```bash
cd qoder/app
uv sync --extra dev --extra server
```

产物：

- `qoder/app/.venv/`：应用、测试、PowerContext 服务端与 CLI；
- `qoder/app/uv.lock`：锁定依赖版本，`uv sync` 会按锁文件安装。

运行测试：

```bash
.venv/bin/python -m pytest -q
```

## 3. 配置

```bash
cd qoder
cp .env.example .env
```

`.env` 已被忽略，不会进入仓库。`paperdesk` CLI、`scripts/run_scenario.sh` 与 `scripts/start_powercontext.sh` 都会自动读取 `qoder/.env`；已存在的环境变量优先，不会被覆盖。完整场景会断言真实嵌入与 LLM 输出，因此需要可用的 MiniMax 密钥；无密钥时应用仍可启动，但场景会在断言处失败。

| 变量 | 必填 | 作用 |
| --- | --- | --- |
| `MINIMAX_API_KEY` | 使用模型时必填 | 生成与嵌入的 API 密钥 |
| `MINIMAX_BASE_URL` | 使用模型时必填 | OpenAI 兼容接口地址 |
| `MINIMAX_MODEL` | 使用模型时必填 | 综述使用的对话模型 |
| `PAPERDESK_EMBEDDING_MODEL` | 否 | 默认 `embo-01` |
| `PAPERDESK_EMBEDDING_DIM` | 否 | 默认 `1536`，必须与模型输出一致 |
| `PAPERDESK_LLM_MODE` | 否 | `auto`（默认）、`llm`、`extractive` |
| `PAPERDESK_DATA_DIR` | 否 | 默认 `qoder/.runtime`，必须在项目目录内 |
| `PAPERDESK_POWERCONTEXT_URL` | 否 | 默认 `http://127.0.0.1:8011` |
| `PAPERDESK_POWERCONTEXT_PORT` | 否 | 默认 `8011` |
| `PAPERDESK_WEB_PORT` | 否 | 默认 `8080` |

PowerContext 的嵌入由 `scripts/start_powercontext.sh` 根据 `MINIMAX_*` 自动配置为 `minimax:embo-01`（1536 维）。如需改用其他服务，可在 `.env` 中直接设置 `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_*`。

## 4. 一键验证

```bash
cd qoder
scripts/run_scenario.sh
```

脚本会：

1. 停止旧进程并清空 `qoder/.runtime/`；
2. 启动 PowerContext；
3. 导入 6 篇笔记 / 30 个分块 / 30 个向量；
4. 执行检索、过滤、记忆写入、计划、综述与会话写回；
5. 重启 PowerContext，验证记忆与计划复用；
6. 运行关键词与混合检索评测，断言混合模式 `embedding=true`，并检查 `recall@5 >= 0.9`、`MRR >= 0.6`；
7. 启动 Web 工作台，请求 8 个页面，断言全部 200。

脚本还会断言导入数量与向量数、过滤查询的命中范围、`mode=llm` 且覆盖率不低于 50% 的综述、4 条记忆与 progress revision 2，并把本轮状态写入 `evidence/run-status.txt`。

成功后终端输出：

```text
scenario complete
  PowerContext: http://127.0.0.1:8011
  Web:          http://127.0.0.1:8080
  evidence:     /.../qoder/evidence
```

浏览器打开 <http://127.0.0.1:8080/> 核对「继续上次」卡片、资料库检索、检索调试和记忆页。逐项期望结果见 [sample/scenario/steps.md](sample/scenario/steps.md)。

模型的措辞每次可能不同，但引用编号必须对应检索分块，`coverage` 应不低于 0.50；低于阈值时自动回退为抽取式（覆盖率 0.50），并在 `fallback_reason` 说明原因。

## 5. 分步运行

只启动 PowerContext：

```bash
cd qoder
scripts/start_powercontext.sh
scripts/stop_powercontext.sh
```

命令行操作（会自动读取 `qoder/.env`）：

```bash
cd qoder/app
export PAPERDESK_DATA_DIR="$(cd .. && pwd)/.runtime"
.venv/bin/paperdesk ingest --json
.venv/bin/paperdesk search "hybrid fusion"
.venv/bin/paperdesk plan
.venv/bin/paperdesk digest rag-survey
.venv/bin/paperdesk answer "rerank 在 RAG 里解决什么问题？"
.venv/bin/paperdesk eval --queries ../sample/eval/queries.json
.venv/bin/paperdesk memory check
.venv/bin/paperdesk schema
```

启动 Web：

```bash
cd qoder/app
.venv/bin/paperdesk serve --host 127.0.0.1 --port 8080
```

停止 Web：结束该进程，或执行 `qoder/sample/scenario/reset.sh`。

## 6. 运行时目录

```text
qoder/.runtime/
├── seekdb/              # 嵌入式 seekdb 数据文件
├── powercontext/        # PowerContext 持久化数据
├── powercontext.log
├── powercontext.pid
├── web.log
└── web.pid
```

`PAPERDESK_DATA_DIR` 默认是 `qoder/.runtime`。seekdb 需要项目本地数据目录；不要指向 `/tmp` 或网络文件系统。重置脚本只接受 `qoder/.runtime`（或其子目录）与 `qoder/app/.runtime`，拒绝 `qoder/` 本身及源码目录；清空外部路径必须显式设置 `PAPERDESK_ALLOW_EXTERNAL_RESET=1`。

## 7. 重置

```bash
cd qoder
sample/scenario/reset.sh
```

这会停止 Web 与 PowerContext，并删除 `.runtime/`。下一次导入会重新创建表、scope 和全部记忆，得到与验证样例一致的初始状态。

## 8. 排错

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `PowerContext 未就绪` | 服务未启动或端口被占用 | 运行 `scripts/start_powercontext.sh`，查看 `.runtime/powercontext.log` |
| 写入记忆返回 503 `rate limit exceeded` | 嵌入服务限流 | 等待 20–60 秒重试；脚本已带退避重试并关闭后台 dream 任务 |
| 记忆检索 `mode=text` 且 `hits=0` | 嵌入不可用，客户端回退 fts | 检查 `.env` 的 MiniMax 配置与 PowerContext 日志；恢复后重跑场景 |
| 综述显示 `extractive` | 模型未配置或调用失败 | 检查 `MINIMAX_*`；`fallback_reason` 会说明原因 |
| `embedding_error` 非空 | 嵌入服务不可用 | 场景会在导入断言处失败；恢复服务后重跑，不要在无向量状态下继续 |
| 向量维度不匹配 | 更换模型但沿用旧数据库 | 清空 `.runtime/` 后重新导入 |
| 8080 已被占用 | 旧 Web 进程未退出 | 执行 `reset.sh`；脚本在启动前也会检查端口 |
| `uv` 不在 PATH | 安装在 `~/.local/bin` | `export PATH="$HOME/.local/bin:$PATH"` |
| 导入后检索为空 | 资料库路径不对 | 确认 `PAPERDESK_LIBRARY` 指向 `qoder/sample/library` |

## 9. 干净环境自检

- 仓库中没有绝对路径、密钥或 `.runtime/` 内容；`.env.example` 只含占位符。
- 从新克隆的仓库执行第 2、3、4 节即可复现验证样例。
- 重置脚本恢复 seekdb 数据、PowerContext scope 和样例状态。
- 评测语料与查询集固定在 `sample/` 下，阈值断言与模型措辞无关。
- Web 页面不依赖外部 CDN 或图片；`app/src/paperdesk/static/styles.css` 为本地文件。

## 10. 站点发布

静态站点位于 `qoder/site/`。发布步骤、可见性设置与无痕核验见 [site/PUBLISH.md](site/PUBLISH.md)。
