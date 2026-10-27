# Qoder 开发记录

本目录用于存放「项目主要由 Qoder 完成」的可核对材料。代码、运行证据和发布站点分别在其他目录；这里只记录开发过程。

## 提交材料清单

| 材料 | 状态 | 放置位置 |
| --- | --- | --- |
| Qoder 账号 UID | 已填写 | 本文件「账号」一节 |
| 需求拆解与方案对话截图 | 已补充 | `evidence/qoder/screenshots/develop-1-requirements.png` |
| 关键实现截图 | 已补充 | `evidence/qoder/screenshots/develop-2-coding.png` |
| 调试与验证对话截图 | 已补充 | `evidence/qoder/screenshots/develop-3-debugging.png`、`develop-4-verification.png` |
| 站点公开可见性截图 | 已补充 | `evidence/qoder/screenshots/visibility.png` |
| 已发布站点截图 | 已补充 | `evidence/qoder/screenshots/published-home.png` |
| 无痕访问截图 | 已补充 | `evidence/qoder/screenshots/incognito.png` |

截图内容对应：

| 文件 | 内容 |
| --- | --- |
| `develop-1-requirements.png` | 需求拆解、5 点确认与技术选型对比（生成式综述 + 确定性回退） |
| `develop-2-coding.png` | Qoder 编辑器中的实现工作与 `app/src/paperdesk/` 源码结构 |
| `develop-3-debugging.png` | seekdb 卡死、503 导致 revision 漂移、完整场景跑通与子代理复核 |
| `develop-4-verification.png` | 复核补漏与最终验证结果（59 项测试、场景断言、证据一致性） |
| `visibility.png` | Qoder Sites 分享面板显示「任何人都可访问」 |
| `published-home.png` | 已登录状态打开已发布站点首页 |
| `incognito.png` | InPrivate 无痕窗口打开同一 URL，无登录墙 |

截图要求：能看到 Qoder 界面与对话内容；不要包含 API 密钥、令牌、个人信息或与项目无关的聊天。开发时间以本文件的开发周期与下方 commit 对应关系为准。

## 账号

- 平台：Qoder 桌面端（个人账号）
- UID：`01a177f6-8352-7dca-aad8-af82f8efed66`
- 公开站点 URL：`https://paperdesk-reading-u6gf1w4ntck.qoder.website/`
- 发布方式：Qoder Sites，内容目录为站点根目录（含 `index.html` 与 `assets/`），可见范围设为「所有人可访问」
- 开发周期：2026-10-26 至 2026-10-27
- 提交材料中不得包含 `qoder/.env`、`.runtime/`、`.agents/` 或本地工作日志；凭据只保留在被忽略的本地文件中。

## 发布与访问核验

- 2026-10-27 由个人账号通过 Qoder Sites 发布，发布内容为 `site/` 根目录的 `index.html` 与 `assets/`。
- 无痕窗口（InPrivate）无需登录即可打开首页，导航、样式、截图和「Made with Qoder」标识正常，见 `screenshots/incognito.png`。
- 已登录状态下的发布页见 `screenshots/published-home.png`。
- 分享面板的可见范围显示「任何人都可访问」，见 `screenshots/visibility.png`。
- 评审结束前不暂停或删除站点；发布后修改内容需再次发布才会更新线上版本。

## 开发过程与可核对位置

| 阶段 | Qoder 中的工作 | 对应产物 |
| --- | --- | --- |
| 需求拆解 | 阅读赛题与仓库约束，确定「seekdb 检索 + PowerContext 记忆」的双技术方案，明确验收四项与不做什么 | `README.md`、`sample/scenario/steps.md` |
| 方案设计 | 设计 `pd_documents` / `pd_chunks` / `pd_meta`、记忆 kind、继续卡片与引用校验流程 | `app/src/paperdesk/store.py`、`memory.py`、`service.py` |
| 实现 | 生成解析器、seekdb 存取与混合检索、嵌入客户端、PowerContext 客户端、FastAPI 页面、CLI 与测试 | `app/src/paperdesk/`、`app/tests/` |
| 调试 | 修复 Jinja `TemplateResponse` 调用、继续卡片排序、记忆测试中的可变 payload、RRF 权重与评测期望；定位 seekdb 重复 DDL 导致查询挂起，以及 503 重试导致的 progress 修订漂移 | 对应测试与 `app/src/paperdesk/` 的最终实现 |
| 验证 | 运行 59 项测试，执行完整场景脚本，检查 Web 页面、记忆重启复用与引用映射 | `evidence/` 全部目录 |
| 发布 | 使用个人 Qoder 账号发布 `site/`，可见范围设为「所有人可访问」，并用无痕窗口核验 | `site/PUBLISH.md`、`evidence/qoder/screenshots/` |

## 代码对应关系

| commit | 范围 | 关键文件 |
| --- | --- | --- |
| `f07c724f` | PaperDesk 应用、样例、场景脚本、文档与站点 | `app/src/paperdesk/`、`app/tests/`、`scripts/`、`sample/`、`README.md`、`RUNBOOK.md`、`site/` |
| `1c4d5923` | 查询证据加严：必须带向量分，失败重试并标记失败状态 | `scripts/run_scenario.sh`、`sample/scenario/steps.md`、`evidence/README.md` |
| `39c85f01` | 表已存在时不再重复执行 DDL，并为 seekdb 连接设置读写超时 | `app/src/paperdesk/store.py` |
| `086af185` | 引用覆盖率断言对齐产品 50% 阈值，同步文档与站点文案 | `scripts/run_scenario.sh`、`RUNBOOK.md`、`sample/scenario/steps.md`、`evidence/README.md`、`site/index.html` |
| `9d014330` | 进度记忆在内容相同时跳过重写，保证重试后 revision 稳定 | `app/src/paperdesk/memory.py`、`app/tests/test_memory.py` |
| `3b97ee2f` | 已存在向量列但索引缺失时自动补建索引 | `app/src/paperdesk/store.py` |
| `4d24f7cf` | 综述证据必须带向量分；重跑时保留已捕获的截图 | `scripts/run_scenario.sh`、`evidence/README.md`、`README.md`、`site/index.html` |
| `6686ef13` | Web 证据拒绝嵌入回退页：资料库页必须带向量分，失败自动重试 | `scripts/run_scenario.sh`、`sample/scenario/steps.md`、`evidence/README.md` |
| `1692f7f9` | 最终一轮完整证据与截图（`6686ef13` 之后重跑） | `evidence/`、`site/assets/screenshots/` |
| `255aad9c` | 记录公开站点 URL、UID 与发布/开发截图 | `evidence/qoder/` |

本轮证据在 `6686ef13` 提交后由 `qoder/scripts/run_scenario.sh` 从空运行时重跑生成，`evidence/run-status.txt` 记录 `commit=6686ef13` 与 `status=complete`；综述证据的 6 条命中同时带 `fts_score` 与 `vector_score`，资料库页记录向量得分且无嵌入回退提示。此前 `305fd7e9` 及更早的证据轮次已被本轮取代。

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
