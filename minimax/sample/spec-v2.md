# tasklite 规格

## SPEC-001 归档过滤

默认不返回 archived=True 的任务；include_archived=True 时全部返回。

## SPEC-002 排序

结果按 priority 降序排列；priority 相同时按 id 升序排列。

## SPEC-003 最小优先级过滤

传入 min_priority 时，只返回 priority 不低于 min_priority 的任务。

## SPEC-004 优先级校验

add_task 的 priority 必须是 0 到 5 之间的整数，否则抛出 ValueError。
