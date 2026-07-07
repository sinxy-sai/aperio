from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.completion import CompleteEvent, Completer, Completion
    from prompt_toolkit.document import Document
except ImportError:  # pragma: no cover - fallback for minimal installs
    PromptSession = None
    CompleteEvent = None
    Completer = object
    Completion = None
    Document = None

from .config import (
    APERIO_HOME,
    WORKSPACE_ROOT,
    get_amap_api_key,
    get_api_key,
    get_base_url,
    get_enable_mcp_tools,
    get_engine_name,
    get_install_project_deps,
    get_model_name,
    get_scan_sandbox_mode,
)
from .resources import packaged_skills_root
from .runner import run_agent


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    usage: str = ""
    aliases: tuple[str, ...] = ()


@dataclass
class ReplState:
    approval_mode: str = "prompt"
    timeout_seconds: int = 900
    last_message: str = ""
    last_result: Any | None = None
    history: list[dict[str, str]] = field(default_factory=list)


COMMAND_SPECS = [
    CommandSpec("/help", "查看命令", aliases=("/?",)),
    CommandSpec("/exit", "退出 CLI", aliases=("/quit", "/q")),
    CommandSpec("/doctor", "检查环境和配置"),
    CommandSpec("/init", "创建 ~/.aperio/.env", "/init [--force]"),
    CommandSpec("/config", "查看 CLI、模型和运行配置", aliases=("/status",)),
    CommandSpec("/workspace", "显示运行工作区", aliases=("/pwd",)),
    CommandSpec("/approval", "查看或设置审批模式", "/approval prompt|approve|reject"),
    CommandSpec("/timeout", "查看或设置超时秒数", "/timeout <seconds>"),
    CommandSpec("/runs", "列出最近运行", "/runs [n]", aliases=("/ls",)),
    CommandSpec("/artifacts", "列出产物和 trace 文件", "/artifacts [run_id|last]", aliases=("/files",)),
    CommandSpec("/skills", "列出可用 skills", "/skills [filter]"),
    CommandSpec("/last", "重新打印上次回答", aliases=("/answer",)),
    CommandSpec("/history", "查看当前 CLI 会话历史", "/history [n]"),
    CommandSpec("/clear", "清空当前 CLI 会话历史"),
    CommandSpec("/retry", "重新运行上一条 prompt"),
    CommandSpec("/serve", "启动本地 Web UI", "/serve [port]", aliases=("/web",)),
]


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
    run_parser.add_argument("--approval-mode", choices=("prompt", "approve", "reject"), default="approve")
    run_parser.add_argument("--timeout", type=int, default=900)

    serve_parser = subparsers.add_parser("serve", help="Start the local Web UI")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8088)
    serve_parser.add_argument("--reload", action="store_true")

    subparsers.add_parser("doctor", help="Check configuration")
    return parser


def repl() -> int:
    state = ReplState()
    _print_welcome(state)
    if not get_api_key():
        print("Config: missing DEEPSEEK_API_KEY. Run `aperio init` first.")
    session = _make_prompt_session()

    while True:
        try:
            message = _prompt(session).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if not message:
            continue
        if message in {"exit", "quit"}:
            return 0
        if message.startswith("/"):
            exit_code = _handle_repl_command(message, state)
            if exit_code is not None:
                return exit_code
            continue

        _run_repl_message(message, state)


def _run_repl_message(message: str, state: ReplState) -> None:
    result = run_agent(
        message,
        approval_mode=state.approval_mode,
        timeout_seconds=state.timeout_seconds,
    )
    state.last_message = message
    state.last_result = result
    state.history.append({"role": "user", "text": message})
    state.history.append({"role": "assistant", "text": result.answer})
    print()
    print(result.answer)
    _print_artifacts(result.run_id, result.artifacts)


def _handle_repl_command(command_line: str, state: ReplState) -> int | None:
    try:
        parts = shlex.split(command_line)
    except ValueError as exc:
        print(f"Command parse error: {exc}")
        return None
    if not parts:
        return None

    command = parts[0].lower()
    args = parts[1:]
    if command in {"/exit", "/quit", "/q"}:
        return 0
    if command in {"/help", "/?"}:
        _print_repl_help()
    elif command == "/doctor":
        doctor()
    elif command == "/init":
        init_config(force="--force" in args)
    elif command in {"/config", "/status"}:
        _print_repl_status(state)
    elif command in {"/workspace", "/pwd"}:
        print(WORKSPACE_ROOT)
    elif command == "/approval":
        _set_approval_mode(args, state)
    elif command == "/timeout":
        _set_timeout(args, state)
    elif command in {"/runs", "/ls"}:
        _print_recent_runs(_parse_limit(args, default=10, maximum=50))
    elif command in {"/artifacts", "/files"}:
        _print_run_artifacts(args, state)
    elif command == "/skills":
        _print_skills(args)
    elif command in {"/last", "/answer"}:
        _print_last_result(state)
    elif command == "/history":
        _print_history(state, _parse_limit(args, default=12, maximum=100))
    elif command == "/clear":
        state.history.clear()
        print("Cleared in-memory CLI history.")
    elif command == "/retry":
        if not state.last_message:
            print("No previous prompt to retry.")
        else:
            _run_repl_message(state.last_message, state)
    elif command in {"/serve", "/web"}:
        port = _parse_port(args, default=8088)
        serve(host="127.0.0.1", port=port, reload=False)
    else:
        print(f"Unknown command: {command}. Type /help for commands.")
    return None


def _set_approval_mode(args: list[str], state: ReplState) -> None:
    if not args:
        print(f"approval_mode = {state.approval_mode}")
        return
    value = args[0].strip().lower()
    if value not in {"prompt", "approve", "reject"}:
        print("Usage: /approval prompt|approve|reject")
        return
    state.approval_mode = value
    print(f"approval_mode = {state.approval_mode}")


def _set_timeout(args: list[str], state: ReplState) -> None:
    if not args:
        print(f"timeout_seconds = {state.timeout_seconds}")
        return
    try:
        value = int(args[0])
    except ValueError:
        print("Usage: /timeout <seconds>")
        return
    if value < 30 or value > 3600:
        print("Timeout must be between 30 and 3600 seconds.")
        return
    state.timeout_seconds = value
    print(f"timeout_seconds = {state.timeout_seconds}")


def _print_repl_status(state: ReplState) -> None:
    print("Aperio CLI status")
    print(f"Home:      {APERIO_HOME}")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print(f"Engine:    {get_engine_name()}")
    print(f"Model:     {get_model_name()}")
    print(f"Approval:  {state.approval_mode}")
    print(f"Timeout:   {state.timeout_seconds}s")
    print(f"MCP tools: {'enabled' if get_enable_mcp_tools() else 'disabled'}")
    print(f"API key:   {'configured' if get_api_key() else 'missing'}")


def _print_recent_runs(limit: int) -> None:
    runs = _recent_run_roots(limit)
    if not runs:
        print("No runs found.")
        return
    for run_root in runs:
        performance = _read_json(run_root / "performance.json")
        route = performance.get("route", "unknown")
        ok = performance.get("ok")
        duration = performance.get("duration_seconds", 0)
        status = "ok" if ok is True else "failed" if ok is False else "unknown"
        print(f"{run_root.name}  {status}  {route}  {duration}s")


def _print_run_artifacts(args: list[str], state: ReplState) -> None:
    run_id = args[0] if args else "last"
    if run_id == "last":
        run_id = getattr(state.last_result, "run_id", "") or _latest_run_id()
    if not run_id:
        print("No run id available.")
        return
    run_root = WORKSPACE_ROOT / run_id
    if not run_root.exists() or not run_root.is_dir():
        print(f"Run not found: {run_id}")
        return
    artifacts = []
    for path in sorted(run_root.rglob("*")):
        if not path.is_file():
            continue
        if path.name in {"performance.json", "observability.json"} or path.suffix.lower() in {".md", ".json", ".txt"}:
            artifacts.append(path)
    if not artifacts:
        print(f"No artifacts found for {run_id}.")
        return
    for artifact in artifacts[:80]:
        print(f"  - {artifact}")


def _print_last_result(state: ReplState) -> None:
    if not state.last_result:
        print("No answer yet.")
        return
    print(state.last_result.answer)
    _print_artifacts(state.last_result.run_id, state.last_result.artifacts)


def _print_history(state: ReplState, limit: int) -> None:
    if not state.history:
        print("No in-memory CLI history.")
        return
    for item in state.history[-limit:]:
        text = item["text"].replace("\n", " ")
        if len(text) > 120:
            text = text[:117] + "..."
        print(f"{item['role']}: {text}")


def _print_welcome(state: ReplState) -> None:
    skills = _discover_skills()
    print("Aperio Agent")
    print("本地多 Agent 工作台。直接输入问题开始任务，输入 / 查看命令，输入 $ 查看 skills。")
    print(f"Workspace: {WORKSPACE_ROOT}")
    print(f"Model: {get_model_name()} | Approval: {state.approval_mode} | Timeout: {state.timeout_seconds}s")
    if skills:
        preview = ", ".join(skill["name"] for skill in skills[:4])
        suffix = "" if len(skills) <= 4 else f" ... +{len(skills) - 4}"
        print(f"Skills: {preview}{suffix}")
    print("常用：/help  /skills  /doctor  /runs  /artifacts  /exit")


def _make_prompt_session() -> Any:
    if PromptSession is None:
        return None
    return PromptSession(
        completer=AperioCompleter(),
        complete_while_typing=True,
        mouse_support=_mouse_support_enabled(),
    )


def _mouse_support_enabled() -> bool:
    return os.environ.get("APERIO_CLI_MOUSE", "").strip().lower() in {"1", "true", "yes", "on"}


def _prompt(session: Any) -> str:
    if session is None:
        return input("\naperio> ")
    return session.prompt("\naperio> ")


class AperioCompleter(Completer):
    def get_completions(self, document: Any, complete_event: Any) -> Any:
        text = document.text_before_cursor
        token = text.split()[-1] if text.split() else text
        if token.startswith("/"):
            yield from _command_completions(token)
        elif token.startswith("$"):
            yield from _skill_completions(token)


def _command_completions(token: str) -> Any:
    if Completion is None:
        return
    seen: set[str] = set()
    for spec in COMMAND_SPECS:
        for name in (spec.name, *spec.aliases):
            if name in seen or not name.startswith(token):
                continue
            seen.add(name)
            yield Completion(
                name,
                start_position=-len(token),
                display=name,
                display_meta=spec.usage or spec.description,
            )


def _skill_completions(token: str) -> Any:
    if Completion is None:
        return
    query = token[1:].lower()
    for skill in _discover_skills():
        if query and query not in skill["name"].lower() and query not in skill["path"].lower():
            continue
        value = f"${skill['name']}"
        yield Completion(
            value,
            start_position=-len(token),
            display=value,
            display_meta=skill["description"] or skill["path"],
        )


def _print_skills(args: list[str]) -> None:
    query = " ".join(args).strip().lower()
    skills = [
        skill for skill in _discover_skills()
        if not query or query in skill["name"].lower() or query in skill["path"].lower() or query in skill["description"].lower()
    ]
    if not skills:
        print("No skills found.")
        return
    print("Available skills:")
    for skill in skills:
        desc = f" - {skill['description']}" if skill["description"] else ""
        print(f"  ${skill['name']}  ({skill['path']}){desc}")


def _discover_skills() -> list[dict[str, str]]:
    root = packaged_skills_root()
    if not root.exists():
        return []
    skills: list[dict[str, str]] = []
    for skill_file in sorted(root.rglob("SKILL.md")):
        rel_dir = skill_file.parent.relative_to(root).as_posix()
        metadata = _read_skill_metadata(skill_file)
        name = metadata.get("name") or skill_file.parent.name
        skills.append({
            "name": name,
            "path": rel_dir,
            "description": metadata.get("description", ""),
        })
    return skills


def _read_skill_metadata(path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return metadata
    if not lines or lines[0].strip() != "---":
        return metadata
    for line in lines[1:40]:
        if line.strip() == "---":
            break
        key, sep, value = line.partition(":")
        if sep and key.strip() in {"name", "description"}:
            metadata[key.strip()] = value.strip().strip("\"'")
    return metadata


def _print_artifacts(run_id: str, artifacts: Any) -> None:
    if not artifacts:
        return
    print("\nArtifacts:")
    for artifact in artifacts:
        print(f"  - {WORKSPACE_ROOT / run_id / artifact.path}")


def _parse_limit(args: list[str], *, default: int, maximum: int) -> int:
    if not args:
        return default
    try:
        return max(1, min(int(args[0]), maximum))
    except ValueError:
        print(f"Invalid limit, using {default}.")
        return default


def _parse_port(args: list[str], *, default: int) -> int:
    if not args:
        return default
    try:
        port = int(args[0])
    except ValueError:
        print(f"Invalid port, using {default}.")
        return default
    return max(1, min(port, 65535))


def _recent_run_roots(limit: int) -> list[Path]:
    if not WORKSPACE_ROOT.exists():
        return []
    return [item for item in sorted(WORKSPACE_ROOT.iterdir(), reverse=True) if item.is_dir()][:limit]


def _latest_run_id() -> str:
    runs = _recent_run_roots(1)
    return runs[0].name if runs else ""


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


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
            "APERIO_INSTALL_PROJECT_DEPS=0\n"
            "APERIO_SCAN_SANDBOX=host\n"
            "APERIO_ENABLE_MCP=0\n"
            "AMAP_API_KEY=\n",
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
    print(f"Scan sandbox: {get_scan_sandbox_mode()}")
    print(f"Install deps for scan: {'yes' if get_install_project_deps() else 'no'}")
    print(f"MCP tools: {'enabled' if get_enable_mcp_tools() else 'disabled'}")
    print(f"Amap key:  {'configured' if get_amap_api_key() else 'missing'}")
    print(f"API key:   {'configured' if get_api_key() else 'missing'}")
    return 0 if get_api_key() else 1


def _print_repl_help() -> None:
    print("Commands:")
    for spec in COMMAND_SPECS:
        aliases = f" ({', '.join(spec.aliases)})" if spec.aliases else ""
        usage = spec.usage or spec.name
        print(f"  {usage:<26} {spec.description}{aliases}")
    print("\n补全：输入 / 显示命令，输入 $ 显示 skills。其他输入会直接发送给 agent。")


if __name__ == "__main__":
    raise SystemExit(main())
