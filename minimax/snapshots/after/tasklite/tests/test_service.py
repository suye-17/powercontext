import pytest

from tasklite.models import Task
from tasklite.service import add_task, list_tasks


def make_tasks() -> list[Task]:
    return [
        Task("b", "Write spec", 3),
        Task("a", "Read spec", 3),
        Task("c", "Archive old", 5, archived=True),
        Task("d", "Low priority", 1),
        Task("e", "High priority", 5),
    ]


def test_list_tasks_excludes_archived_by_default():
    result = list_tasks(make_tasks())

    assert [task.id for task in result] == ["e", "a", "b", "d"]


def test_list_tasks_include_archived():
    result = list_tasks(make_tasks(), include_archived=True)

    assert [task.id for task in result] == ["c", "e", "a", "b", "d"]


def test_list_tasks_sorts_by_priority_desc_then_id():
    result = list_tasks(make_tasks())

    assert [task.priority for task in result] == [5, 3, 3, 1]
    assert [task.id for task in result] == ["e", "a", "b", "d"]


def test_add_task_appends_valid_task():
    tasks: list[Task] = []

    task = add_task(tasks, "New task", 2)

    assert task.id == "1"
    assert tasks == [task]


def test_list_tasks_filters_by_min_priority():
    result = list_tasks(make_tasks(), min_priority=3)

    assert [task.id for task in result] == ["e", "a", "b"]


def test_list_tasks_min_priority_none_keeps_all():
    result = list_tasks(make_tasks(), min_priority=None)

    assert [task.id for task in result] == ["e", "a", "b", "d"]


@pytest.mark.parametrize("priority", [-1, 6, True, 3.5, "3"])
def test_add_task_rejects_invalid_priority(priority):
    with pytest.raises(ValueError):
        add_task([], "Invalid", priority)


@pytest.mark.parametrize("priority", [0, 5])
def test_add_task_accepts_boundary_priorities(priority):
    task = add_task([], "Boundary", priority)

    assert task.priority == priority
