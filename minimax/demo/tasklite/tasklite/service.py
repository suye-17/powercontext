from collections.abc import Iterable

from .models import Task


def list_tasks(
    tasks: Iterable[Task],
    *,
    include_archived: bool = False,
    min_priority: int | None = None,
) -> list[Task]:
    visible = [
        task
        for task in tasks
        if (include_archived or not task.archived)
        and (min_priority is None or task.priority >= min_priority)
    ]
    return sorted(visible, key=lambda task: (-task.priority, task.id))


def add_task(tasks: list[Task], title: str, priority: int) -> Task:
    if type(priority) is not int or not 0 <= priority <= 5:
        raise ValueError("priority must be an integer between 0 and 5")
    task = Task(id=str(len(tasks) + 1), title=title, priority=priority)
    tasks.append(task)
    return task
