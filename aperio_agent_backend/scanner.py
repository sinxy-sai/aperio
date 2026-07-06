from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from .resources import packaged_skills_root


def run_code_health_scan(
    project_root: Path,
    target_rel: str,
    out_path: Path,
    *,
    timeout_seconds: int = 300,
    install_project_deps: bool = False,
) -> dict[str, Any]:
    """Run the migrated deterministic code-health toolkit.

    The toolkit is copied from the demo skills into the package and is invoked
    by the backend, not by the model. That keeps CLI and web usage predictable
    while still giving the agent real tool evidence.
    """
    script = packaged_skills_root() / "code-health" / "code-health-toolkit" / "scripts" / "run_checks.py"
    if not script.exists():
        result = _fallback_result(project_root, target_rel, "code-health toolkit script is missing")
        _write_json(out_path, result)
        return result

    command = [
        sys.executable,
        str(script),
        "--project-root",
        str(project_root),
        "--target",
        target_rel or ".",
        "--out",
        str(out_path),
        "--summary",
    ]
    if install_project_deps:
        command.append("--install-project-deps")

    try:
        completed = subprocess.run(
            command,
            cwd=str(project_root),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(30, timeout_seconds),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        result = _fallback_result(
            project_root,
            target_rel,
            f"code-health toolkit timed out after {timeout_seconds}s",
            output=_decode_timeout_output(exc.stdout),
        )
        _write_json(out_path, result)
        return result

    if out_path.exists():
        try:
            data = json.loads(out_path.read_text(encoding="utf-8"))
            data.setdefault("backend_invocation", {})
            data["backend_invocation"].update(
                {
                    "exit_code": completed.returncode,
                    "summary_output": (completed.stdout or "")[-12000:],
                    "invoked_by": "aperio_agent_backend.scanner",
                }
            )
            _write_json(out_path, data)
            return data
        except json.JSONDecodeError:
            pass

    result = _fallback_result(
        project_root,
        target_rel,
        "code-health toolkit did not produce valid JSON",
        output=(completed.stdout or "")[-12000:],
        exit_code=completed.returncode,
    )
    _write_json(out_path, result)
    return result


def compact_scan_summary(scan_result: dict[str, Any]) -> dict[str, Any]:
    """Keep agent prompt context compact while the full JSON remains on disk."""
    discovery = scan_result.get("discovery", {}) if isinstance(scan_result, dict) else {}
    return {
        "schema_version": scan_result.get("schema_version"),
        "project_root": scan_result.get("project_root"),
        "target_rel": scan_result.get("target_rel") or scan_result.get("target"),
        "python_files": len(discovery.get("python_files", []) or []),
        "test_files": len(discovery.get("test_files", []) or []),
        "dependency_files": discovery.get("dependency_files", [])[:20] if isinstance(discovery, dict) else [],
        "tool_coverage": scan_result.get("tool_coverage", {}),
        "findings_summary": scan_result.get("findings_summary", {}),
        "limitations": scan_result.get("limitations") or scan_result.get("coverage_notes") or {},
    }


def _fallback_result(
    project_root: Path,
    target_rel: str,
    reason: str,
    *,
    output: str = "",
    exit_code: int | None = None,
) -> dict[str, Any]:
    target = (project_root / (target_rel or ".")).resolve()
    python_files = []
    if target.exists():
        search_root = target if target.is_dir() else target.parent
        python_files = [str(path) for path in sorted(search_root.rglob("*.py"))[:200]]
    result: dict[str, Any] = {
        "schema_version": "code-health-tools-fallback-v1",
        "project_root": str(project_root),
        "target": str(target),
        "target_rel": target_rel or ".",
        "discovery": {
            "project_root": str(project_root),
            "target": str(target),
            "target_exists": target.exists(),
            "python_files": python_files,
            "test_files": [],
            "dependency_files": [
                str(path)
                for name in ("pyproject.toml", "requirements.txt", "environment.yml", "package.json")
                for path in [project_root / name]
                if path.exists()
            ],
        },
        "tool_coverage": {
            "code_health_toolkit": {
                "status": "failed",
                "reason": reason,
            }
        },
        "findings": [],
        "findings_summary": {"total": 0},
        "limitations": [
            reason,
            "Only lightweight file discovery was available; no deterministic scanner findings should be inferred.",
        ],
    }
    if output:
        result["scanner_output_tail"] = output[-12000:]
    if exit_code is not None:
        result["scanner_exit_code"] = exit_code
    return result


def _write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _decode_timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
