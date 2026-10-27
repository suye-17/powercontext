# 技术说明

## 1. 节点流（29 节点 / 40 条连线）

```
开始（代号表单）
└ 代号与轮次（代码）        规范化代号、生成两个账本的幂等键、计算轮次 1-3
  ├ 建账本·世界（HTTP）      POST /v1/scopes   幂等键 memory-forgery:world
  │ └ 建账本·玩家（HTTP）    POST /v1/scopes   幂等键 memory-forgery:player:<代号>
  │   └ 解析两个 scope（代码）
  │     ├ 读账本·世界（HTTP）  POST /v1/memory/entries/list
  │     └ 读账本·玩家（HTTP）  POST /v1/memory/entries/list
  │       └ 整理账本（代码）   按 kind 分类：前任档案 / 登记档案 / 主张 / 是否已对账
  │         └ 首访？（条件）
  │           ├ true  → 登记档案（HTTP，kind=truth）
  │           └ false ┐
  │                   └ 按话语召回（HTTP）  POST /v1/context/prepare（query=本轮话语）
  │                     ├ 取召回上下文（代码）→ 第三句？（条件）
  │                     └ 召回降级（代码，prepare 失败时用空上下文）→ 第三句？（条件）
  │                       ├ 否 → 修复师归档（LLM）→ 解析归档（代码）→ 记下主张（HTTP，kind=claim）→ 回复·归档
  │                       └ 是 → 已对过账？（条件）
  │                             ├ 是 → 回复·已合上
  │                             └ 否 → 终局对账（LLM）→ 整理对账（代码）
  │                                   → 记下末句主张（HTTP，kind=claim）
  │                                   → 记下对账结论（HTTP，kind=verdict）
  │                                   → 有矛盾？（条件）
  │                                       ├ 是 → 记下矛盾（HTTP，kind=contradiction）
  │                                       │      → 记下前科（HTTP，世界账本，kind=ledger）→ 回复·对账
  │                                       └ 否 → 回复·对账
  └ 建账本失败 / 读账本失败 → 回复·账本翻不开（人设化降级，不抛栈）
```

6 个 HTTP 节点都配置了 `error_strategy: fail-branch`、2 次重试与 30 秒超时，因此任何一次服务抖动都不会中断对话。

## 2. Dify 侧实现要点

| 要点 | 做法 |
| --- | --- |
| DSL 版本 | `version: 0.4.0`，`kind: app`，`app.mode: advanced-chat`，`dependencies: []`（无插件） |
| 服务地址 | 只用环境变量 `{{#env.POWERCONTEXT_BASE_URL#}}`，DSL 中不含任何域名或凭据；环境变量为 secret 型，节点日志自动打码 |
| 轮次 | `sys.dialogue_count`（Chatflow 系统变量），代码节点把 1/2/3 及以后归一到 1-3 |
| 本轮话语 | `sys.query`，代码节点截断到 2000 字符后插入请求体 |
| JSON 请求体 | 由代码节点生成整段 JSON 字符串，HTTP 节点以空 key 原样发送——避免在 YAML 里手拼 JSON 时的转义漏洞 |
| 字符串插值进 JSON | 所有自由文本都先经 `json.dumps(...)[1:-1]` 转义（引号、换行、反斜杠），再插进 JSON 模板 |
| 条件运算符 | 字符串比较用 `is` / `is not`，数字比较用 `=`；`=` 只接受 number/boolean（首轮实测踩过） |
| 模型输出容错 | 剥掉 ``` 围栏后按首尾花括号取 JSON；取不到就按散文处理，主张一律回落到玩家原话 |
| 已对账判定 | 玩家账本里存在 `verdict` 条目即视为已对账，避免重复结算（不依赖"有没有矛盾"） |

## 3. PowerContext 接口用法（对照 v1.2.0 契约实测）

| 用途 | 接口 | 请求 | 实测结果 |
| --- | --- | --- | --- |
| 建账本 | `POST /v1/scopes` | `{title, summary, idempotency_key}` | 201；同键同载荷恒返回同一 `scope_id`（幂等）；同键异载荷 409 |
| 读账本 | `POST /v1/memory/entries/list` | `{scope_id}` | 200，`entries[]` 无分页字段；最新条目在前 |
| 召回 | `POST /v1/context/prepare` | `{scope_id, query, max_bytes}` | 200，`status: ready\|empty`；`content` 被 `BEGIN_/END_POWERCONTEXT_PREPARED_CONTEXT_V1` 包裹；无模型时走全文检索，空结果是正常结果 |
| 写入 | `POST /v1/memory/remember` | `{scope_id, kind, text, reason}` | 200；`kind` 1-128 自由字符串；`text` 超 8192 UTF-8 字节返回 422 |

契约冒烟（`evidence/contract/`）：幂等、409、四接口、8192 字节上限、kind 超长，共 10 项全部通过。

## 4. 幂等与一致性

- 两个 scope 的请求载荷由代号确定性生成（同一代号每次发出完全相同的 title/summary/key），因此重复开局不会新建 scope，也不会触发 409。
- 播种 `truth` 由"账本里没有 `truth`"驱动，天然幂等。
- 终局结算写 4 条（末句主张、对账结论、矛盾、前科），顺序固定；重复进入对账会被 `verdict` 拦在"已对过账"分支。
- `seed.sh` 先读总账本再决定是否写入，重复执行不会产生重复档案。

## 5. 与赛题要求的对应

| 赛题条目 | 落点 |
| --- | --- |
| 3.1-1 能运行 | `dsl/memory-forgery.yml` 可导入；只需环境变量 + 一次模型选择即可发布运行 |
| 3.1-2 能体验主要功能 | `sample/steps.md` 四场景，覆盖四个好玩点 |
| 3.1-3 技术结合有效 | `evidence/run-01/` 有两侧记录：Dify 侧节点执行截图、PowerContext 侧逐条读写 JSON 与请求时间线，按场景互相对应 |
| 3.1-4 可复现 | README 第 4 节从安装到启动的完整命令；`reset.sh` 一键回到初始状态；不依赖作者本机任何未交付状态 |
| 4.1 DSL | `dsl/memory-forgery.yml`（主应用）、`dsl/smoke.yml`（连通性自检，可选）；导入顺序无依赖 |
| 4.2 运行说明 | `README.md`；`.env.example` 只有占位值 |
| 4.3 验证样例与证据 | `sample/`、`scripts/`、`evidence/` |
| 4.4 依赖补充 | 预置记忆由 `scripts/seed.sh` 初始化；无知识库、无插件、无自定义前后端 |

## 6. 规模与成本

- 每轮对话 4 次 PowerContext 读调用 + 1 次写调用；终局多 3 次写调用。单轮整条链路的服务端耗时实测 0.1-1.5 秒（见 `evidence/run-01/09-request-timeline.md`）。
- 玩家账本每次会话最多约 8 条；总账本随玩家人数线性增长，注入提示词时只取最近 5 条。
- 模型调用：每轮 1 次（归档）或每局 1 次（终局对账）；PowerContext 侧 0 次。

## 7. 提交前自检（对照赛题）

### 3.1 基础验收

| # | 要求 | 状态 | 依据 |
| --- | --- | --- | --- |
| 1 | DSL 可导入，应用能接收输入并返回结果 | 通过 | `dsl/memory-forgery.yml` 在 Dify Cloud 导入并运行；`dsl/smoke.yml` 亦验证导入 |
| 2 | 至少一组完整验证样例 | 通过 | `sample/steps.md` 四场景；`evidence/run-01/` 为一次完整三句话对局 |
| 3 | 技术结合有效且可核对 | 通过 | PowerContext 侧：`evidence/run-01/03-player-entries.json`（claim×3、verdict、contradiction）、`04-world-entries.json`（前科）、`05-prepare-recall.json`（召回）；Dify 侧：节点执行与对话截图 |
| 4 | 过程可复现 | 通过 | `README.md` 第 4 节；`scripts/seed.sh`（幂等播种）、`scripts/reset.sh`（清零）；不依赖作者本机状态 |

### 4.1–4.4 提交材料

| 条目 | 状态 | 位置 |
| --- | --- | --- |
| 4.1 DSL（含用途、导入顺序、Dify 版本、重绑地址步骤） | 通过 | `dsl/memory-forgery.yml`（主应用）、`dsl/smoke.yml`（可选自检）；README 4.4 |
| 4.2 运行说明（创意/玩法/分工、环境与版本、模型、完整步骤、入口、网络与限制、占位值） | 通过 | `README.md`、`.env.example` |
| 4.3 验证样例与证据（初始状态、输入、预期、实际结果、技术作用与记录、恢复方法） | 通过 | `sample/steps.md`、`evidence/`、`scripts/reset.sh` |
| 4.4 依赖补充（预置记忆初始化、无知识库/无插件/无自定义前后端） | 通过 | `scripts/seed.sh`；README 第 3 节声明 |

### 提交红线

- DSL 中不含任何真实域名或凭据：`grep -c trycloudflare dsl/memory-forgery.yml` 结果为 0，环境变量只有占位值。
- 无 `dependencies`（不依赖插件市场）；无自定义插件包、无自定义后端。
- 判题分支：`2026-competition-ecosystem`，产物全部位于 `dify/`。
