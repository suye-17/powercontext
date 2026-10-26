import argparse
import json
from pathlib import Path

from .models import Task
from .service import add_task, list_tasks


def _load(path: str) -> list[Task]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Task(**item) for item in data]


def _save(path: str, tasks: list[Task]) -> None:
    payload = [task.__dict__ for task in tasks]
    Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tasklite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--include-archived", action="store_true")
    list_parser.add_argument("--min-priority", type=int)
    list_parser.add_argument("--data", default="fixtures/tasks.json")

    add_parser = subparsers.add_parser("add")
    add_parser.add_argument("--title", required=True)
    add_parser.add_argument("--priority", type=int, required=True)
    add_parser.add_argument("--data", default="fixtures/tasks.json")

    args = parser.parse_args(argv)
    tasks = _load(args.data)

    if args.command == "list":
        result = list_tasks(
            tasks,
            include_archived=args.include_archived,
            min_priority=args.min_priority,
        )
        print(json.dumps([task.__dict__ for task in result], ensure_ascii=False))
        return 0

    task = add_task(tasks, args.title, args.priority)
    _save(args.data, tasks)
    print(json.dumps(task.__dict__, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
