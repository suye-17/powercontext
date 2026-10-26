from collections.abc import Iterable

from .models import Task


def list_tasks(
    tasks: Iterable[Task],
    *,
    include_archived: bool = False,
    min_priority: int | None = None,
) -> list[Task]:
    del min_priority
    visible = [task for task in tasks if include_archived or not task.archived]
    return sorted(visible, key=lambda task: (-task.priority, task.id))


def add_task(tasks: list[Task], title: str, priority: int) -> Task:
    task = Task(id=str(len(tasks) + 1), title=title, priority=priority)
    tasks.append(task)
    return task
