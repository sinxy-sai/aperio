from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from .config import (
    APERIO_HOME,
    WORKSPACE_ROOT,
    get_api_key,
    get_base_url,
    get_engine_name,
    get_install_project_deps,
    get_model_name,
)
from .runner import run_agent


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        return repl()
    if args.command == "init":
        return init_config(force=args.force)
    if args.command == "run":
        return run_once(args.message, approval_mode=args.approval_mode, timeout_seconds=args.timeout)
    if args.command == "serve":
        return serve(host=args.host, port=args.port, reload=args.reload)
    if args.command == "doctor":
        return doctor()

    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aperio",
        description="Aperio local agent. Run `aperio` with no arguments to start an interactive chat.",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser("init", help="Create ~/.aperio/.env")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config")

    run_parser = subparsers.add_parser("run", help="Run one prompt and exit")
    run_parser.add_argument("message", nargs="+", help="Prompt text")
    run_parser.add_argument("--approval-mode", choices=("approve", "reject"), default="approve")
    run_parser.add_argument("--timeout", type=int, default=900)

    serve_parser = subparsers.add_parser("serve", help="Start the local Web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8088)
    serve_parser.add_argument("--reload", action="store_true")

    subparsers.add_parser("doctor", help="Check configuration")
    return parser


def repl() -> int:
    print("Aperio Agent")
    print("Type /help for commands, /exit to quit.")
    print(f"Workspace: {WORKSPACE_ROOT}")
    if not get_api_key():
        print("Config: missing DEEPSEEK_API_KEY. Run `aperio init` first.")

    while True:
        try:
            message = input("\naperio> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not message:
            continue
        if message in {"/exit", "/quit", "exit", "quit"}:
            return 0
        if message == "/help":
            _print_repl_help()
            continue
        if message == "/doctor":
            doctor()
            continue
        if message == "/init":
            init_config(force=False)
            continue

        result = run_agent(message)
        print()
        print(result.answer)
        if result.artifacts:
            print("\nArtifacts:")
            for artifact in result.artifacts:
                print(f"  - {WORKSPACE_ROOT / result.run_id / artifact.path}")


def run_once(message_parts: list[str], approval_mode: str, timeout_seconds: int) -> int:
    message = " ".join(message_parts).strip()
    result = run_agent(message, approval_mode=approval_mode, timeout_seconds=timeout_seconds)
    print(result.answer)
    if result.artifacts:
        print("\nArtifacts:")
        for artifact in result.artifacts:
            print(f"  - {WORKSPACE_ROOT / result.run_id / artifact.path}")
    return 0 if result.ok else 1


def init_config(force: bool = False) -> int:
    APERIO_HOME.mkdir(parents=True, exist_ok=True)
    target = APERIO_HOME / ".env"
    if target.exists() and not force:
        print(f"Config already exists: {target}")
        print("Use `aperio init --force` to overwrite it.")
        return 0

    template = Path(__file__).resolve().parent / ".env.example"
    if template.exists():
        shutil.copyfile(template, target)
    else:
        target.write_text(
            "DEEPSEEK_API_KEY=\n"
            "APERIO_ENGINE=deepagents\n"
            "APERIO_MODEL=openai:deepseek-v4-flash\n"
            "APERIO_BASE_URL=https://api.deepseek.com\n"
            "APERIO_INSTALL_PROJECT_DEPS=0\n",
            encoding="utf-8",
        )
    print(f"Created config: {target}")
    return 0


def serve(host: str, port: int, reload: bool = False) -> int:
    try:
        import uvicorn
    except ImportError:
        print("uvicorn is not installed. Install with `pip install aperio-agent[web]`.", file=sys.stderr)
        return 1

    uvicorn.run("aperio_agent_web.app:app", host=host, port=port, reload=reload)
    return 0


def doctor() -> int:
    print("Aperio doctor")
    print(f"Home:      {APERIO_HOME}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print(f"Engine:    {get_engine_name()}")
    print(f"Model:     {get_model_name()}")
    print(f"Base URL:  {get_base_url()}")
    print(f"Install deps for scan: {'yes' if get_install_project_deps() else 'no'}")
    print(f"API key:   {'configured' if get_api_key() else 'missing'}")
    return 0 if get_api_key() else 1


def _print_repl_help() -> None:
    print(
        "Commands:\n"
        "  /help    Show this help\n"
        "  /doctor  Check configuration\n"
        "  /init    Create ~/.aperio/.env\n"
        "  /exit    Quit\n"
        "\n"
        "Any other input is sent to the agent."
    )


if __name__ == "__main__":
    raise SystemExit(main())
