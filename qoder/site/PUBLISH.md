# 发布到 Qoder Sites

`site/` 是纯静态站点：`index.html` + `assets/`，没有构建步骤，也不依赖外部 CDN。发布前先在浏览器直接打开 `qoder/site/index.html` 检查内容和相对路径。

## 1. 发布前替换的信息

| 位置 | 当前内容 | 需要替换为 |
| --- | --- | --- |
| 页脚「代码仓库」 | 已指向 `github.com/suye-17/powercontext` 的 `feat/qoder-project` 分支 `qoder/` 目录 | 该分支必须已推送到公开仓库，评审无需登录即可访问；分支名变化时同步修改 |
| `assets/screenshots/` | 5 张本机真实运行截图（页面展示工作台与检索调试 2 张） | 如界面有改动，用最新截图替换；不要用示意图冒充截图 |

截图已嵌入页面 gallery（工作台与检索调试）并在「产品」区块引用，无需再手工添加。真实截图可从运行中的 `http://127.0.0.1:8080/`、`/debug/retrieval`、`/ask`、`/memory`、`/library` 获取。页面示例数据来自 `sample/`，不包含个人信息。

## 2. 用个人账号发布

1. 打开 Qoder 桌面端，确认登录的是**个人账号**。企业账号无法把站点设为公开可见。
2. 进入 Qoder Sites，新建站点，内容目录选择仓库中的 `qoder/site/`。
3. 站点标题建议：`研读 PaperDesk · 论文阅读工作台`。描述使用页面里的 meta description。
4. 发布后把可见性设为「任何人可访问 / Public」，不要只设置为「仅自己」或「组织内」。
5. 复制完整公开 URL。

如果 Qoder Sites 的入口名称或按钮与上面不同，以桌面端当前的发布流程为准；关键是内容目录、公开可见性和最终 URL。

## 3. 无痕核验

1. 打开一个新的无痕/隐私窗口，确认没有登录 Qoder 或其他相关账号。
2. 访问公开 URL，确认首页完整显示，CSS、favicon、导航和锚点正常。
3. 滚动到「证据」「运行」「Built with Qoder」区块，确认没有登录墙或 404。
4. 截图保存：
   - 无痕窗口中的首页；
   - Qoder Sites 的公开可见性设置；
   - 发布成功后的 URL。
5. 把截图放入 `qoder/evidence/qoder/screenshots/`，并在 `evidence/qoder/README.md` 填写 URL、UID 与截图文件名。

## 4. 发布后

- 任何内容修改都要重新发布，线上版本不会自动更新。
- 评审结束前不要暂停或删除站点。
- 站点不包含密钥、个人信息、内网地址或本地绝对路径。
- 如果站点使用自定义域名，确保域名同样无需登录即可访问，并在提交材料中写明。

## 5. 提交材料对应

| 材料 | 位置 |
| --- | --- |
| 站点源文件 | `qoder/site/` |
| 公开 URL | 填写到 `evidence/qoder/README.md` 的账号一节 |
| 无痕截图 | `evidence/qoder/screenshots/` |
| 可见性截图 | `evidence/qoder/screenshots/` |
| 开发记录与 UID | `evidence/qoder/README.md` |
