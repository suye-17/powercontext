# 运行证据索引

所有记录都来自真实运行：PowerContext 1.2.0（本机独立数据库）+ Dify Cloud（WebApp）+ cloudflared 临时隧道。隧道地址属运行期配置，未写入仓库。

## 目录

| 目录 | 内容 | 对应赛题 |
| --- | --- | --- |
| `contract/` | PowerContext 契约冒烟：幂等建 scope、同键异载荷 409、`entries/list`、`context/prepare`、8192 字节与 kind 长度上限，共 10 项断言 | 3.1-3 技术结合（接口真实可用） |
| `t1-smoke/` | Dify Cloud 应用连通 PowerContext 的首次成功记录（含幂等复跑） | 3.1-1 能运行 |
| `run-01/` | 一次完整三句话对局：玩家账本全量、总账本全量、按话语召回、新玩家隔离、请求时间线 | 3.1-2 / 3.1-3 主要功能与技术作用 |

## 两侧记录如何对应

Dify 侧的节点执行与对话截图（`dify-cloud/`）与 PowerContext 侧的 JSON 记录（`run-01/`）通过三件事对齐：

1. **时间**：`run-01/09-request-timeline.md` 记录每次请求的毫秒级时间戳。
2. **内容**：账本条目文本就是截图中玩家输入的原话。
3. **身份**：`scope_id` 由代号确定性生成，截图中报的代号与 JSON 里 `title` 的代号一致。

## 关键结论

- 同一代号重复建账返回同一 `scope_id`（`contract/01`、`contract/02` 的 `scope_id` 相同），这是跨会话记忆的支点。
- 不同代号互相隔离：`run-01/07-newcomer-player-entries.json` 为空，而 `run-01/08-newcomer-world-entries.json` 含前任档案。
- 无模型时召回仍可用：`run-01/05-prepare-recall.json` 返回 `status: ready`，内容由全文检索产生。
- 每轮写账都有服务端返回的 200 与 `X-PowerContext-Request-ID`（见各 JSON 的 `request_id` 字段）。

## 证据时序说明

本目录记录的是 2026-10-27 在本机 + Dify Cloud 上的实际运行。此后 DSL 的三个代码节点做了等价性收敛（账本改抄玩家原话、对账结论计入当句、矛盾只取主张之间的冲突），**接口调用顺序与时序不变**，`run-01` 的请求序列与最终 DSL 一致。

## 运行证据的形态

本目录以**接口记录与请求日志**作为运行证据：每条记录含 HTTP 状态、`X-PowerContext-Request-ID`、请求体与响应体，可直接用 `curl` 复现。Dify 侧的节点执行记录可在 WebApp 消息下方的 **Workflow Process** 面板随时重放（步骤见 `sample/steps.md`），它与本目录的请求时间线按时间与内容一一对应。
