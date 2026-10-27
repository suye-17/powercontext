# 冒烟证据：Dify Cloud → PowerContext 连通性

- 日期：2026-10-27
- 应用：`dsl/smoke.yml`（Chatflow，无 LLM 节点，不消耗模型）
- 环境：Dify Cloud（WebApp 运行）+ PowerContext 1.2.0（本机 `127.0.0.1:8000`，经 cloudflared 临时隧道暴露为 HTTPS）
- 目的：验证 Dify Cloud 出站可访问隧道域名、`{{#env.POWERCONTEXT_BASE_URL#}}` 在 HTTP 节点 URL 中可用、`version: 0.4.0` 形状的 DSL 可导入运行

## 实际运行结果（WebApp 回复原文）

```
PowerContext 连通成功。响应体：

{"scope_id":"scp_15w4er001wkb4r2w5sw8x6n14d","title":"冒烟·记忆伪造师","summary":"Dify 连通性冒烟（幂等创建）","parent_scope_id":null,"context_references":[],"external_references":[],"version":1}
```

## 幂等复跑

相同幂等键再次创建，返回同一个 `scope_id`（`rerun.json`）：

```
{"scope_id":"scp_15w4er001wkb4r2w5sw8x6n14d", ... "version":1}
```

## 结论

| 检查项 | 结果 |
| --- | --- |
| DSL 可导入（Dify Cloud） | 通过 |
| Dify Cloud 出站 -> 隧道 -> 本机 PowerContext | 通过（HTTP 200 + 完整 JSON） |
| 环境变量插值用于 HTTP 节点 URL | 通过 |
| 应用能接收输入并返回结果（基础验收 1） | 通过 |
| 幂等键未产生重复 scope | 通过 |
