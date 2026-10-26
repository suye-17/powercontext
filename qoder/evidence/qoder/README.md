# Qoder 开发记录

本目录用于存放「项目主要由 Qoder 完成」的可核对材料。代码、运行证据和发布站点分别在其他目录；这里只记录开发过程。

## 需要发布者补充的材料

| 材料 | 状态 | 放置位置 |
| --- | --- | --- |
| Qoder 账号 UID | 待填写 | 本文件「账号」一节 |
| 需求拆解与方案对话截图 | 待补充 | `evidence/qoder/screenshots/` |
| 关键实现对话截图 | 待补充 | `evidence/qoder/screenshots/` |
| 调试与验证对话截图 | 待补充 | `evidence/qoder/screenshots/` |
| 站点发布与无痕访问截图 | 待补充 | `evidence/qoder/screenshots/` |

截图要求：能看到 Qoder 界面、对话内容与时间；不要包含 API 密钥、令牌、个人信息或与项目无关的聊天。

## 账号

- 平台：Qoder 桌面端（个人账号）
- UID：`待填写`
- 公开站点 URL：`待填写`
- 开发周期：2026-10-26
- 提交材料中不得包含 `qoder/.env`、`.runtime/`、`.agents/` 或本地工作日志；凭据只保留在被忽略的本地文件中。

## 开发过程与可核对位置

| 阶段 | Qoder 中的工作 | 对应产物 |
| --- | --- | --- |
| 需求拆解 | 阅读赛题与仓库约束，确定「seekdb 检索 + PowerContext 记忆」的双技术方案，明确验收四项与不做什么 | `README.md`、`sample/scenario/steps.md` |
| 方案设计 | 设计 `pd_documents` / `pd_chunks` / `pd_meta`、记忆 kind、继续卡片与引用校验流程 | `app/src/paperdesk/store.py`、`memory.py`、`service.py` |
| 实现 | 生成解析器、seekdb 存取与混合检索、嵌入客户端、PowerContext 客户端、FastAPI 页面、CLI 与测试 | `app/src/paperdesk/`、`app/tests/` |
| 调试 | 修复 Jinja `TemplateResponse` 调用、继续卡片排序、记忆测试中的可变 payload、RRF 权重与评测期望 | 对应测试与 `app/src/paperdesk/` 的最终实现 |
| 验证 | 运行 57 项测试，执行完整场景脚本，检查 Web 页面、记忆重启复用与引用映射 | `evidence/` 全部目录 |
| 发布（待执行） | 使用个人 Qoder 账号发布 `site/`，设置公开可见并在无痕窗口核验 | `site/PUBLISH.md`、本目录截图 |

## 代码对应关系

提交后在此补充 commit 与功能范围的对应表，便于评审把对话记录、diff 和运行结果串起来：

| commit | 范围 | 关键文件 |
| --- | --- | --- |
| `待填写` | PaperDesk 应用、样例、证据、文档与站点 | `app/src/paperdesk/`、`app/tests/`、`scripts/`、`sample/`、`evidence/`、`README.md`、`RUNBOOK.md`、`site/` |

## 建议截取的对话

1. 赛题约束与方案对比：为什么同时使用 seekdb 与 PowerContext，以及两者边界。
2. 首次失败与修复：Jinja 模板调用错误导致 500，定位后改为 `TemplateResponse(request, name, context)`。
3. 检索质量调试：等权 RRF 让泛化嵌入压过精确术语，改为关键词 8:1 加权并用评测集验证。
4. PowerContext 联调：scope 幂等键冲突、artifact ETag 修订、服务重启后继续卡片变化。
5. 完整场景：`scripts/run_scenario.sh` 的断言输出与 8 个页面 200 的结果。

## 说明

- 上述阶段、失败与修复均对应本仓库的实际代码和证据，不使用事后编造的截图或运行记录。
- 单元测试使用 fake transport 隔离外部服务；发布用的证据目录只保留真实服务与真实模型调用。
- 开发记录中的密钥、令牌和模型请求头需要打码后再提交。
