| 项目 | 内容 |
| --- | --- |
| 编号 | `setup-00-repository-fork` |
| 总分 | 10 分 |
| 任务 | 创建 GitHub fork，并提交仓库 URL |
| 提交产物 | `https://github.com/OWNER/REPOSITORY` |
| 上游仓库 | [oceanbase/powercontext](https://github.com/oceanbase/powercontext)，GitHub repository ID 为 `1093180734` |


## 一、题目背景
后续代码题通过产品仓库和 commit 接收实现。本题检查提交仓库是否保留 GitHub 上的 PowerContext fork 关联，完成后即可获得准备题的 10 分。

## 二、题目内容
在 GitHub 创建 [PowerContext](https://github.com/oceanbase/powercontext) 的公开 fork，提交 fork 仓库的 HTTPS URL。允许仓库改名，也允许从同一 PowerContext fork 网络中的仓库再次 fork。

组织方通过 GitHub 仓库元数据核对以下条件：

1. 仓库存在且公开可访问，`private` 为 `false`，`visibility` 为 `public`。
2. `fork` 为 `true`。
3. `source.id` 为 `1093180734`，即整个 fork 网络的源仓库是 PowerContext。

GitHub 的 `parent` 表示直接父仓库，`source` 表示 fork 网络的源仓库；间接 fork 无需直接以 PowerContext 为父仓库。字段语义见 [GitHub 官方说明](https://docs.github.com/en/rest/repos/repos#get-a-repository)。

将代码复制后独立创建仓库、fork 其他项目或提交上游仓库本身均不满足条件。本题不要求修改产品源码、创建 PR、提交新 commit 或证明 GitHub 账户所有权。

## 三、提交方式
提交仓库 URL，允许末尾的 `.git` 和单个 `/`。URL 使用 `https://github.com/OWNER/REPOSITORY` 格式，不包含认证信息、端口、查询参数、片段或分支路径。仓库名称不要求为 `powercontext`。

```latex
https://github.com/participant/powercontext
https://github.com/participant/memory-project.git
```

单独提交本题时只需 URL。与代码题一同提交时，继续提供代码题要求的完整 commit；本题不根据源码差异判分。

## 四、评分标准
全部条件满足时获得 10 分，否则获得 0 分。本题未通过不扣减其他题目的得分。

组织方在接收提交时采集 GitHub 证据，保存本次响应和采集时间，再离线判分。重判沿用这次证据；仓库后续变更通过新的提交重新采集。GitHub 返回 404 时视为仓库不存在或不能公开访问，计 0 分。超时、限流、认证故障、服务错误或缺失证据属于采集异常，等待组织方重试，不直接计 0 分。

## 五、公开测试与提交前检查
使用 GitHub CLI 查看仓库元数据，将示例中的 owner 和仓库名替换为自己的 fork：

```bash
gh api repos/participant/powercontext \
  --jq '{id, private, visibility, fork, parent_id: .parent.id, source_id: .source.id}'
```

确认仓库公开、`fork` 为 `true`、`source_id` 为 `1093180734`。组织方的离线验收使用同一次采集的原始元数据检查公开访问与 fork 网络，参赛者无需生成或上传证据文件。
