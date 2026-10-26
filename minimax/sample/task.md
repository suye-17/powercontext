# 验证任务：tasklite 优先级过滤与校验

## 要改什么

在 `tasklite` 业务工程中完成两项规格：

- `SPEC-003`：`list_tasks` 的 `min_priority` 参数只返回 `priority >= min_priority` 的任务。
- `SPEC-004`：`add_task` 的 `priority` 必须是 `0..5` 的整数，否则抛出 `ValueError`。

## 必须保持的原有行为

- 默认排除 `archived=True` 的任务。
- `include_archived=True` 时包含归档任务。
- 按 `priority` 降序、`id` 升序排序。
- 合法任务仍能正常创建，id 从当前列表长度递增。

## 目标测试

- `test_list_tasks_filters_by_min_priority`
- `test_list_tasks_min_priority_none_keeps_all`
- `test_add_task_rejects_invalid_priority`
- `test_add_task_accepts_boundary_priorities`

## 回归测试

- `test_list_tasks_excludes_archived_by_default`
- `test_list_tasks_include_archived`
- `test_list_tasks_sorts_by_priority_desc_then_id`
- `test_add_task_appends_valid_task`
