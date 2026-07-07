from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


MAX_COMPACT_ITEMS = 40
MAX_COMPACT_TEXT = 3000

from .resources import packaged_skills_root


def run_code_health_scan(
    project_root: Path,
    target_rel: str,
    out_path: Path,
    *,
    timeout_seconds: int = 300,
    install_project_deps: bool = False,
    sandbox_mode: str = "host",
) -> dict[str, Any]:
    """Run the migrated deterministic code-health toolkit.

    The toolkit is copied from the demo skills into the package and is invoked
    by the backend, not by the model. That keeps CLI and web usage predictable
    while still giving the agent real tool evidence.
    """
    sandbox_mode = (sandbox_mode or "host").strip().lower()
    if sandbox_mode in {"docker", "auto"}:
        try:
            from .sandbox import run_code_health_scan_in_docker

            return run_code_health_scan_in_docker(
                project_root,
                target_rel,
                out_path,
                timeout_seconds=timeout_seconds,
                install_project_deps=install_project_deps,
            )
        except Exception as exc:
            if sandbox_mode == "docker":
                result = _fallback_result(project_root, target_rel, f"Docker sandbox failed: {exc}")
                _write_json(out_path, result)
                return result
            docker_error = str(exc)
        else:
            docker_error = ""
    else:
        docker_error = ""

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
                    "scanner_mode": "host",
                    "exit_code": completed.returncode,
                    "summary_output": (completed.stdout or "")[-12000:],
                    "invoked_by": "aperio_agent_backend.scanner",
                }
            )
            if docker_error:
                data["backend_invocation"]["docker_fallback_reason"] = docker_error
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
    """Keep agent context compact while the full JSON remains on disk."""
    discovery = scan_result.get("discovery", {}) if isinstance(scan_result, dict) else {}
    tools = scan_result.get("tools", {}) if isinstance(scan_result, dict) else {}
    return {
        "schema_version": "code-health-compact-v1",
        "source_schema_version": scan_result.get("schema_version"),
        "project_root": scan_result.get("project_root"),
        "target": scan_result.get("target"),
        "target_rel": scan_result.get("target_rel") or scan_result.get("target"),
        "backend_invocation": scan_result.get("backend_invocation", {}),
        "discovery": _compact_discovery(discovery),
        "setup": scan_result.get("setup", {}),
        "coverage_notes": scan_result.get("coverage_notes") or scan_result.get("limitations") or {},
        "tool_coverage": scan_result.get("tool_coverage", {}),
        "findings_summary": scan_result.get("findings_summary", {}),
        "findings": _compact_findings(scan_result.get("findings", [])),
        "tools": {name: _compact_tool(name, value) for name, value in sorted(tools.items()) if isinstance(value, dict)},
        "context_policy": {
            "full_raw_path": "/outputs/code_health/raw/tool_results.json",
            "model_facing_path": "/outputs/code_health/raw/tool_results.compact.json",
            "note": "Agents should cite this compact evidence file. The full raw JSON is retained for audit/download, not repeated model context.",
        },
    }


def write_compact_scan_summary(path: Path, summary: dict[str, Any] | None) -> None:
    _write_json(path, summary or {})


def _compact_discovery(discovery: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(discovery, dict):
        return {}
    python_files = discovery.get("python_files", []) or []
    test_files = discovery.get("test_files", []) or []
    dependency_files = discovery.get("dependency_files", []) or []
    readme_files = discovery.get("readme_files", []) or []
    return {
        "target_exists": discovery.get("target_exists"),
        "python_file_count": len(python_files),
        "test_file_count": len(test_files),
        "test_support_file_count": len(discovery.get("test_support_files", []) or []),
        "dependency_files": dependency_files[:MAX_COMPACT_ITEMS],
        "readme_files": readme_files[:MAX_COMPACT_ITEMS],
        "sample_python_files": python_files[:MAX_COMPACT_ITEMS],
        "sample_test_files": test_files[:MAX_COMPACT_ITEMS],
    }


def _compact_findings(findings: Any) -> list[dict[str, Any]]:
    if not isinstance(findings, list):
        return []
    selected = _select_compact_findings(findings)
    compact = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        compact.append(_compact_finding(item))
    return compact


def _select_compact_findings(findings: list[Any]) -> list[dict[str, Any]]:
    """Keep important findings even when noisy lint output is first in raw order."""
    budget = MAX_COMPACT_ITEMS * 2
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(item: Any) -> None:
        if not isinstance(item, dict):
            return
        key = str(item.get("id") or (item.get("source_tool"), item.get("rule_id"), item.get("location"), item.get("message")))
        if key in seen:
            return
        seen.add(key)
        selected.append(item)

    for item in findings:
        if isinstance(item, dict) and item.get("in_target"):
            add(item)
    for severity in ("Critical", "High", "Medium"):
        for item in findings:
            if isinstance(item, dict) and item.get("severity") == severity:
                add(item)
    for item in findings:
        if len(selected) >= budget:
            break
        add(item)

    return selected


def _compact_finding(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "source_tool": item.get("source_tool"),
        "rule_id": item.get("rule_id"),
        "category": item.get("category"),
        "severity": item.get("severity"),
        "location": item.get("location"),
        "scope": item.get("scope"),
        "in_target": item.get("in_target"),
        "message": _trim_text(item.get("message"), 400),
        "recommendation": _trim_text(item.get("recommendation"), 400),
        "confidence": item.get("confidence"),
    }


def _compact_tool(name: str, data: dict[str, Any]) -> dict[str, Any]:
    result = {
        "available": data.get("available"),
        "skipped": data.get("skipped", False),
        "timed_out": data.get("timed_out", False),
        "exit_code": data.get("exit_code"),
        "command": data.get("command"),
        "reason": data.get("reason"),
        "note": data.get("note"),
        "output_truncated": data.get("output_truncated", False),
    }
    if name == "ruff":
        result["summary"] = _compact_ruff(data)
    elif name == "bandit":
        result["summary"] = _compact_bandit(data)
    elif name == "radon":
        result["summary"] = _compact_radon(data)
    elif name == "detect_secrets":
        result["summary"] = _compact_detect_secrets(data)
    elif name == "interrogate":
        result["summary"] = _compact_interrogate(data)
    elif name == "deptry":
        result["summary"] = _compact_deptry(data)
    elif name == "pip_audit":
        result["summary"] = _compact_pip_audit(data)
    elif name in {"mypy", "pytest", "coverage"}:
        result["summary"] = _compact_text_tool(data)
    else:
        result["summary"] = _compact_text_tool(data)

    if name == "radon":
        result["component_statuses"] = data.get("component_statuses")
    return {key: value for key, value in result.items() if value not in (None, "", [], {})}


def _compact_ruff(data: dict[str, Any]) -> dict[str, Any]:
    items = data.get("json")
    if not isinstance(items, list):
        return _compact_text_tool(data)
    by_rule = _count_by(items, "code")
    return {
        "total": len(items),
        "by_rule": by_rule,
        "examples": [
            {
                "code": item.get("code"),
                "filename": item.get("filename"),
                "location": item.get("location"),
                "message": _trim_text(item.get("message"), 240),
            }
            for item in items[:MAX_COMPACT_ITEMS]
            if isinstance(item, dict)
        ],
    }


def _compact_bandit(data: dict[str, Any]) -> dict[str, Any]:
    parsed = data.get("json")
    results = parsed.get("results", []) if isinstance(parsed, dict) else []
    if not isinstance(results, list):
        return _compact_text_tool(data)
    return {
        "total": len(results),
        "by_severity": _count_by(results, "issue_severity"),
        "by_confidence": _count_by(results, "issue_confidence"),
        "by_test_id": _count_by(results, "test_id"),
        "examples": [
            {
                "test_id": item.get("test_id"),
                "severity": item.get("issue_severity"),
                "confidence": item.get("issue_confidence"),
                "filename": item.get("filename"),
                "line_number": item.get("line_number"),
                "issue_text": _trim_text(item.get("issue_text"), 300),
            }
            for item in results[:MAX_COMPACT_ITEMS]
            if isinstance(item, dict)
        ],
    }


def _compact_radon(data: dict[str, Any]) -> dict[str, Any]:
    cc = ((data.get("cc") or {}).get("json") or {}) if isinstance(data.get("cc"), dict) else {}
    mi = ((data.get("mi") or {}).get("json") or {}) if isinstance(data.get("mi"), dict) else {}
    raw = ((data.get("raw") or {}).get("json") or {}) if isinstance(data.get("raw"), dict) else {}

    blocks = []
    if isinstance(cc, dict):
        for filename, items in cc.items():
            for item in items or []:
                if isinstance(item, dict):
                    blocks.append(
                        {
                            "filename": filename,
                            "name": item.get("name"),
                            "type": item.get("type"),
                            "lineno": item.get("lineno"),
                            "rank": item.get("rank"),
                            "complexity": item.get("complexity"),
                        }
                    )
    blocks.sort(key=lambda item: int(item.get("complexity") or 0), reverse=True)

    mi_items = []
    if isinstance(mi, dict):
        for filename, value in mi.items():
            score = value.get("mi") if isinstance(value, dict) else value
            mi_items.append({"filename": filename, "mi": score})
    mi_items.sort(key=lambda item: float(item.get("mi") or 100))

    return {
        "complexity_hotspots": blocks[:MAX_COMPACT_ITEMS],
        "worst_maintainability_files": mi_items[:MAX_COMPACT_ITEMS],
        "raw_summary": raw.get("summary") if isinstance(raw, dict) else None,
    }


def _compact_detect_secrets(data: dict[str, Any]) -> dict[str, Any]:
    parsed = data.get("json")
    results = parsed.get("results", {}) if isinstance(parsed, dict) else {}
    secret_count = 0
    examples = []
    if isinstance(results, dict):
        for filename, items in results.items():
            for item in items or []:
                secret_count += 1
                if len(examples) < MAX_COMPACT_ITEMS and isinstance(item, dict):
                    examples.append(
                        {
                            "filename": filename,
                            "line_number": item.get("line_number"),
                            "type": item.get("type"),
                        }
                    )
    return {
        "secret_count": secret_count,
        "files_with_secrets": len(results) if isinstance(results, dict) else 0,
        "examples": examples,
    }


def _compact_interrogate(data: dict[str, Any]) -> dict[str, Any]:
    output = str(data.get("output") or "")
    return {
        "total_line": _find_line(output, "TOTAL"),
        "output_tail": _trim_text(output[-MAX_COMPACT_TEXT:], MAX_COMPACT_TEXT),
    }


def _compact_deptry(data: dict[str, Any]) -> dict[str, Any]:
    json_file = data.get("json_file")
    items = json_file.get("data", []) if isinstance(json_file, dict) else []
    if not isinstance(items, list):
        return _compact_text_tool(data)
    return {
        "total": len(items),
        "by_code": _count_by_nested(items, ("error", "code")),
        "examples": items[:MAX_COMPACT_ITEMS],
        "output_tail": _trim_text(str(data.get("output") or "")[-MAX_COMPACT_TEXT:], MAX_COMPACT_TEXT),
    }


def _compact_pip_audit(data: dict[str, Any]) -> dict[str, Any]:
    parsed = data.get("json")
    dependencies = parsed.get("dependencies", []) if isinstance(parsed, dict) else []
    vulnerabilities = []
    if isinstance(dependencies, list):
        for dep in dependencies:
            if not isinstance(dep, dict):
                continue
            for vuln in dep.get("vulns", []) or []:
                vulnerabilities.append(
                    {
                        "dependency": dep.get("name"),
                        "version": dep.get("version"),
                        "id": vuln.get("id"),
                        "aliases": vuln.get("aliases"),
                        "fix_versions": vuln.get("fix_versions"),
                    }
                )
    return {
        "dependency_count": len(dependencies) if isinstance(dependencies, list) else 0,
        "vulnerability_count": len(vulnerabilities),
        "examples": vulnerabilities[:MAX_COMPACT_ITEMS],
        "output_tail": _trim_text(str(data.get("output") or "")[-MAX_COMPACT_TEXT:], MAX_COMPACT_TEXT),
    }


def _compact_text_tool(data: dict[str, Any]) -> dict[str, Any]:
    output = str(data.get("output") or "")
    lines = [line for line in output.splitlines() if line.strip()]
    return {
        "line_count": len(lines),
        "head": lines[:20],
        "tail": lines[-40:],
        "output_tail": _trim_text(output[-MAX_COMPACT_TEXT:], MAX_COMPACT_TEXT),
    }


def _count_by(items: list[Any], key: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        value = str(item.get(key) or "unknown")
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:MAX_COMPACT_ITEMS])


def _count_by_nested(items: list[Any], path: tuple[str, ...]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        value: Any = item
        for part in path:
            value = value.get(part) if isinstance(value, dict) else None
        name = str(value or "unknown")
        counts[name] = counts.get(name, 0) + 1
    return dict(sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:MAX_COMPACT_ITEMS])


def _find_line(text: str, needle: str) -> str:
    for line in text.splitlines():
        if needle in line:
            return line.strip()
    return ""


def _trim_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[: limit - 20].rstrip() + "...[truncated]"


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
