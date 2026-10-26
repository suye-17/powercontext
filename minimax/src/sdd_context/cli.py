from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from .config import Settings, load_settings
from .ingest import ingest_project
from .powercontext import PowerContextClient, PowerContextError
from .render import to_json, to_markdown
from .select import build_context
from .store import Store


def _resolve_settings(args: argparse.Namespace) -> Settings:
    settings = load_settings(getattr(args, "env_file", None))
    updates: dict[str, object] = {}
    if getattr(args, "db_path", None):
        updates["db_path"] = Path(args.db_path).expanduser().resolve()
    if getattr(args, "server_url", None):
        updates["powercontext_server_url"] = args.server_url.rstrip("/")
    return replace(settings, **updates) if updates else settings


def _ingest(args: argparse.Namespace, settings: Settings) -> int:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        print(f"project root not found: {root}", file=sys.stderr)
        return 1
    documents, chunks = ingest_project(root)
    with Store(settings.db_path) as store:
        store.replace_all(documents, chunks)
        counts = store.counts()
    print(
        json.dumps(
            {
                "root": str(root),
                "db_path": str(settings.db_path),
                "documents": len(documents),
                "chunks": len(chunks),
                "counts": counts,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _build(args: argparse.Namespace, settings: Settings) -> int:
    state: dict[str, object] = {}
    if not args.skip_state:
        client = PowerContextClient(settings.powercontext_server_url)
        scope_id = settings.powercontext_scope_id or client.ensure_scope()
        state = client.latest_state(scope_id, args.task_id or settings.powercontext_task_id)
    with Store(settings.db_path) as store:
        pack = build_context(store, args.task, args.top_k, state)
    output = to_json(pack) if args.format == "json" else to_markdown(pack)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    print(output)
    return 0


def _state_write(args: argparse.Namespace, settings: Settings) -> int:
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    byte_count = len(text.encode("utf-8"))
    if byte_count > 8192:
        print(f"state text is too large: {byte_count} bytes (max 8192)", file=sys.stderr)
        return 1
    client = PowerContextClient(settings.powercontext_server_url)
    scope_id = settings.powercontext_scope_id or client.ensure_scope()
    result = client.remember(scope_id, text, kind=args.kind)
    print(
        json.dumps(
            {"scope_id": scope_id, "bytes": byte_count, "kind": args.kind, "result": result},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _state_read(args: argparse.Namespace, settings: Settings) -> int:
    client = PowerContextClient(settings.powercontext_server_url)
    scope_id = settings.powercontext_scope_id or client.ensure_scope()
    task_id = args.task_id or settings.powercontext_task_id
    print(json.dumps(client.latest_state(scope_id, task_id), ensure_ascii=False, indent=2))
    return 0


def _reset(_args: argparse.Namespace, settings: Settings) -> int:
    with Store(settings.db_path) as store:
        store.reset()
        counts = store.counts()
    print(json.dumps({"db_path": str(settings.db_path), "counts": counts}, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--env-file", help="Path to an env file; defaults to SDD_ENV_FILE or minimax/.env")
    common.add_argument("--db-path", help="Override the embedded seekdb directory")
    common.add_argument("--server-url", help="Override the PowerContext server URL")

    parser = argparse.ArgumentParser(prog="sdd-context", parents=[common])
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest_parser = subparsers.add_parser("ingest", parents=[common])
    ingest_parser.add_argument("--root", default="demo/tasklite", help="Project root to index")

    build_parser = subparsers.add_parser("build", parents=[common])
    build_parser.add_argument("--task", required=True, help="Development task text")
    build_parser.add_argument("--top-k", type=int, default=3)
    build_parser.add_argument("--format", choices=("json", "md"), default="json")
    build_parser.add_argument("--output")
    build_parser.add_argument("--task-id")
    build_parser.add_argument("--skip-state", action="store_true")

    state_parser = subparsers.add_parser("state", parents=[common])
    state_subparsers = state_parser.add_subparsers(dest="state_command", required=True)
    write_parser = state_subparsers.add_parser("write", parents=[common])
    write_parser.add_argument("--file", required=True)
    write_parser.add_argument("--kind", default="sdd-task-state")
    read_parser = state_subparsers.add_parser("read", parents=[common])
    read_parser.add_argument("--task-id")

    subparsers.add_parser("reset", parents=[common])

    args = parser.parse_args(argv)
    settings = _resolve_settings(args)

    try:
        if args.command == "ingest":
            return _ingest(args, settings)
        if args.command == "build":
            return _build(args, settings)
        if args.command == "state" and args.state_command == "write":
            return _state_write(args, settings)
        if args.command == "state" and args.state_command == "read":
            return _state_read(args, settings)
        if args.command == "reset":
            return _reset(args, settings)
    except PowerContextError as error:
        print(str(error), file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
