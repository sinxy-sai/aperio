#!/usr/bin/env python3
"""Portable code-health scanner used."""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


DEPENDENCY_NAMES = {
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "poetry.lock",
    "uv.lock",
    "Pipfile",
    "Pipfile.lock",
}

EXCLUDED_DIR_NAMES = {
    ".agents",
    ".codex",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "agent-chat-ui-main",
    "build",
    "demo",
    "dist",
    "docs",
    "full-stack-fastapi-template-master",
    "hatch_pet_runs",
    "htmlcov",
    "node_modules",
    "playwright-report",
    "workspace",
}

EXCLUDED_SCAN_PATTERN = (
    r"(^|/)(\.agents|\.codex|\.git|\.mypy_cache|\.pytest_cache|\.ruff_cache|\.venv|__pycache__|"
    r"agent-chat-ui-main|build|demo|dist|docs|full-stack-fastapi-template-master|hatch_pet_runs|"
    r"htmlcov|node_modules|playwright-report|workspace)/|"
    r"(^|/)(\.coverage\.json|\.deptry\.json|\.secrets\.baseline)$"
)


def iter_project_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    root = root.resolve()
    for path in sorted(root.rglob("*")):
        try:
            relative_parts = path.resolve().relative_to(root).parts
        except ValueError:
            relative_parts = path.parts
        if any(part in EXCLUDED_DIR_NAMES for part in relative_parts):
            continue
        if path.is_file():
            files.append(path)
    return files


def relative_path(path: str, root: str) -> str:
    path_obj = Path(path).resolve()
    root_obj = Path(root).resolve()
    try:
        rel = path_obj.relative_to(root_obj)
        return "." if str(rel) == "." else str(rel).replace("\\", "/")
    except ValueError:
        return path


def parse_json_output(output: str) -> Any:
    stripped = output.strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        starts = [idx for idx in (stripped.find("{"), stripped.find("[")) if idx >= 0]
        if starts:
            try:
                return json.loads(stripped[min(starts):])
            except json.JSONDecodeError:
                return None
    return None


def run_command(
    command: str,
    cwd: Path,
    max_output: int = 20000,
    timeout_seconds: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    executable = shlex.split(command)[0] if command.strip() else ""
    if not executable or shutil.which(executable) is None:
        return {
            "available": False,
            "command": command,
            "cwd": str(cwd),
            "reason": "command not installed",
        }
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            shell=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
        output = completed.stdout or ""
        result: dict[str, Any] = {
            "available": True,
            "command": command,
            "cwd": str(cwd),
            "exit_code": completed.returncode,
            "output": output[:max_output],
            "output_truncated": len(output) > max_output,
            "note": note,
        }
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        result = {
            "available": True,
            "command": command,
            "cwd": str(cwd),
            "exit_code": 124,
            "output": output[:max_output],
            "output_truncated": len(output) > max_output,
            "note": note,
            "timeout_seconds": timeout_seconds,
            "timed_out": True,
        }
    if timeout_seconds is not None:
        result["timeout_seconds"] = timeout_seconds
    parsed = parse_json_output(result.get("output", ""))
    if parsed is not None:
        result["json"] = parsed
    return result


def run_python_module(
    module: str,
    args: str,
    cwd: Path,
    max_output: int = 20000,
    timeout_seconds: int | None = None,
    note: str = "",
) -> dict[str, Any]:
    command = f"python -m {module} {args}".strip()
    if importlib.util.find_spec(module) is None:
        return {
            "available": False,
            "command": command,
            "cwd": str(cwd),
            "reason": "python module not installed",
        }
    return run_command(command, cwd, max_output=max_output, timeout_seconds=timeout_seconds, note=note)


def skipped_command(command: str, reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "command": command,
        "skipped": True,
        "reason": reason,
    }


def discover_project_files(project_root: Path, target: Path) -> dict[str, Any]:
    test_names = {"tests", "test", "__tests__"}
    result: dict[str, Any] = {
        "project_root": str(project_root),
        "target": str(target),
        "target_exists": target.exists(),
        "python_files": [],
        "test_files": [],
        "test_support_files": [],
        "dependency_files": [],
        "readme_files": [],
    }
    if target.exists():
        result["python_files"] = [str(path) for path in iter_project_files(target) if path.suffix == ".py"]
    if project_root.exists():
        project_files = iter_project_files(project_root)
        result["test_files"] = [
            str(path)
            for path in project_files
            if path.name.startswith("test_") or path.name.endswith("_test.py")
        ]
        result["test_support_files"] = [
            str(path)
            for path in project_files
            if path.suffix == ".py"
            if any(part in test_names for part in path.parts)
            and not (path.name.startswith("test_") or path.name.endswith("_test.py"))
        ]
        result["dependency_files"] = [
            str(path)
            for path in project_files
            if path.name in DEPENDENCY_NAMES
        ]
        result["readme_files"] = [
            str(path)
            for path in project_files
            if path.name.lower().startswith("readme")
        ]
    return result


def read_json_file(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"ok": False, "reason": f"{path} was not generated"}
    try:
        return {"ok": True, "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"ok": False, "reason": str(exc)}


def coverage_json_result(cwd: Path) -> dict[str, Any]:
    path = cwd / ".coverage.json"
    if not path.exists():
        return {"ok": False, "reason": ".coverage.json was not generated"}
    data = json.loads(path.read_text(encoding="utf-8"))
    totals = data.get("totals", {})
    files = data.get("files", {})
    return {
        "ok": True,
        "totals": totals,
        "file_count": len(files),
        "lowest_coverage_files": sorted(
            [
                {
                    "file": name,
                    "percent_covered": details.get("summary", {}).get("percent_covered"),
                    "missing_lines": details.get("summary", {}).get("missing_lines"),
                }
                for name, details in files.items()
            ],
            key=lambda item: item["percent_covered"] if item["percent_covered"] is not None else 101,
        )[:10],
    }


def strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text or "")


def rel_location(path: str | None, project_root: Path, line: int | None = None, column: int | None = None) -> str:
    if not path:
        base = "unknown"
    else:
        base = relative_path(str((project_root / path).resolve() if not Path(path).is_absolute() else path), str(project_root))
    if line is not None:
        base += f":{line}"
        if column is not None:
            base += f":{column}"
    return base


def finding_scope(location: str, target_rel: str) -> tuple[str, bool]:
    """Classify whether a normalized finding is inside the requested scan target."""
    normalized = (location or "unknown").replace("\\", "/").strip()
    path = normalized.split(":", 1)[0].strip()
    target = target_rel.replace("\\", "/").strip().strip("/")

    if not path or path == "unknown":
        return "unknown", False
    if target and (path == target or path.startswith(f"{target}/")):
        return "target", True
    if path == "tests" or path.startswith(("tests/", "test/", "__tests__/")):
        return "tests", False
    if "/alembic/" in f"/{path}/" or path.startswith(("alembic/", "migrations/")):
        return "migration", False
    if path in {"dependency", "dependencies"} or path.endswith(
        (
            "pyproject.toml",
            "requirements.txt",
            "requirements-dev.txt",
            "poetry.lock",
            "uv.lock",
            "Pipfile",
            "Pipfile.lock",
        )
    ):
        return "dependency_manifest", False
    return "project_context", False


def add_finding(
    findings: list[dict[str, Any]],
    tool: str,
    severity: str,
    location: str,
    message: str,
    *,
    rule_id: str = "",
    category: str = "quality",
    evidence_type: str = "tool_fact",
    confidence: str = "medium",
    recommendation: str = "",
    raw: dict[str, Any] | None = None,
    target_rel: str = "",
) -> None:
    index = 1 + sum(1 for item in findings if item.get("source_tool") == tool)
    scope, in_target = finding_scope(location, target_rel)
    findings.append(
        {
            "id": f"CH-{tool.upper().replace('_', '-')}-{index:03d}",
            "source_tool": tool,
            "rule_id": rule_id,
            "category": category,
            "severity": severity,
            "location": location,
            "scope": scope,
            "in_target": in_target,
            "message": message,
            "evidence_type": evidence_type,
            "confidence": confidence,
            "recommendation": recommendation,
            "raw": raw or {},
        }
    )


def normalize_findings(tool_results: dict[str, Any], project_root: Path) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    tools = tool_results.get("tools", {})
    target_rel = str(tool_results.get("target_rel") or "")

    for item in tools.get("ruff", {}).get("json", []) or []:
        location = item.get("location") or {}
        add_finding(
            findings,
            "ruff",
            "Low",
            rel_location(item.get("filename"), project_root, location.get("row"), location.get("column")),
            item.get("message", "ruff finding"),
            rule_id=item.get("code") or "ruff",
            category="lint",
            confidence="high",
            recommendation="按 ruff 规则修复，或保留有明确理由的例外。",
            raw=item,
            target_rel=target_rel,
        )

    mypy_pattern = re.compile(r"^(?P<file>.+?):(?P<line>\d+): (?P<level>error|note): (?P<message>.+?)(?:\s+\[(?P<code>[^\]]+)\])?$")
    for line in strip_ansi(tools.get("mypy", {}).get("output", "")).splitlines():
        match = mypy_pattern.match(line.strip())
        if not match or match.group("level") != "error":
            continue
        code = match.group("code") or "mypy"
        severity = "Low" if code in {"unused-ignore", "untyped-decorator"} else "Medium"
        add_finding(
            findings,
            "mypy",
            severity,
            rel_location(match.group("file"), project_root, int(match.group("line"))),
            match.group("message"),
            rule_id=code,
            category="type",
            confidence="medium",
            recommendation="复核类型注解、第三方类型桩和 ignore；当前 mypy 使用 --ignore-missing-imports。",
            raw={"line": line.strip()},
            target_rel=target_rel,
        )

    bandit_json = tools.get("bandit", {}).get("json") or {}
    for item in bandit_json.get("results", []) if isinstance(bandit_json, dict) else []:
        add_finding(
            findings,
            "bandit",
            str(item.get("issue_severity", "MEDIUM")).title(),
            rel_location(item.get("filename"), project_root, item.get("line_number")),
            item.get("issue_text", "bandit security finding"),
            rule_id=item.get("test_id") or "bandit",
            category="security",
            confidence=str(item.get("issue_confidence", "MEDIUM")).lower(),
            recommendation="结合上下文确认是否可利用；真实问题应按 Bandit 建议修复并补安全测试。",
            raw=item,
            target_rel=target_rel,
        )

    pip_json = tools.get("pip_audit", {}).get("json") or {}
    dependencies = pip_json.get("dependencies", []) if isinstance(pip_json, dict) else []
    for dep in dependencies:
        for vuln in dep.get("vulns", []) or []:
            vuln_id = vuln.get("id") or (vuln.get("aliases") or ["pip-audit"])[0]
            add_finding(
                findings,
                "pip_audit",
                "High",
                dep.get("name") or "dependency",
                f"{dep.get('name')} {dep.get('version', '')} 存在已知漏洞 {vuln_id}".strip(),
                rule_id=vuln_id,
                category="dependency",
                confidence="high",
                recommendation="升级到修复版本；如无 fix version，查阅公告并制定缓解方案。",
                raw={"dependency": dep.get("name"), "version": dep.get("version"), "vulnerability": vuln},
                target_rel=target_rel,
            )

    deptry_data = tools.get("deptry", {}).get("json_file", {}).get("data", [])
    for item in deptry_data if isinstance(deptry_data, list) else []:
        error = item.get("error", {})
        code = error.get("code") or "DEPTRY"
        location = item.get("location", {})
        add_finding(
            findings,
            "deptry",
            "Low" if code == "DEP002" else "Medium",
            rel_location(location.get("file"), project_root, location.get("line"), location.get("column")),
            error.get("message", "dependency declaration issue"),
            rule_id=code,
            category="dependency",
            confidence="medium",
            recommendation="同步代码导入和依赖声明；项目依赖未安装时传递依赖判断可能不完整。",
            raw=item,
            target_rel=target_rel,
        )

    interrogate_output = tools.get("interrogate", {}).get("output", "")
    match = re.search(r"TOTAL\s+\|\s+\d+\s+\|\s+\d+\s+\|\s+\d+\s+\|\s+(?P<percent>\d+(?:\.\d+)?)%", interrogate_output)
    if match:
        percent = float(match.group("percent"))
        if percent < 60:
            add_finding(
                findings,
                "interrogate",
                "Medium" if percent < 30 else "Low",
                tool_results.get("target_rel") or "target",
                f"docstring 覆盖率较低：{percent:g}%",
                rule_id="docstring-coverage",
                category="documentation",
                confidence="high",
                recommendation="为公开 API、配置入口和安全相关函数补充 docstring。",
                raw={"coverage_percent": percent},
                target_rel=target_rel,
            )

    radon_json = tools.get("radon", {}).get("cc", {}).get("json")
    rank_severity = {"C": "Low", "D": "Medium", "E": "High", "F": "High"}
    seen: set[tuple[Any, ...]] = set()
    if isinstance(radon_json, dict):
        for filename, blocks in radon_json.items():
            for block in blocks or []:
                key = (filename, block.get("name"), block.get("lineno"), block.get("type"))
                if key in seen:
                    continue
                seen.add(key)
                rank = str(block.get("rank", "A"))
                complexity = int(block.get("complexity", 0) or 0)
                if rank not in rank_severity and complexity < 10:
                    continue
                add_finding(
                    findings,
                    "radon",
                    rank_severity.get(rank, "Low"),
                    rel_location(filename, project_root, block.get("lineno")),
                    f"{block.get('type', 'block')} `{block.get('name', '<unknown>')}` 圈复杂度 {complexity}，等级 {rank}",
                    rule_id=f"radon-cc-{rank}",
                    category="maintainability",
                    confidence="high",
                    recommendation="拆分条件分支，提取职责明确的小函数，并补充复杂分支测试。",
                    raw=block,
                    target_rel=target_rel,
                )

    secrets_json = tools.get("detect_secrets", {}).get("json") or {}
    secret_results = secrets_json.get("results", {}) if isinstance(secrets_json, dict) else {}
    for filename, items in secret_results.items():
        for item in items or []:
            add_finding(
                findings,
                "detect_secrets",
                "Low" if str(filename).startswith("tests/") else "Medium",
                rel_location(item.get("filename") or filename, project_root, item.get("line_number")),
                f"疑似密钥：{item.get('type', 'secret')}",
                rule_id="detect-secrets",
                category="security",
                confidence="medium",
                recommendation="人工复核是否为真实密钥；真实密钥应立即轮换并迁移到密钥管理或环境变量。",
                raw={key: value for key, value in item.items() if key != "hashed_secret"},
                target_rel=target_rel,
            )

    coverage_summary = tools.get("coverage", {}).get("summary", {})
    totals = coverage_summary.get("totals", {}) if coverage_summary.get("ok") else {}
    percent = totals.get("percent_covered")
    if isinstance(percent, (int, float)) and percent < 60:
        add_finding(
            findings,
            "coverage",
            "Medium",
            "tests",
            f"测试覆盖率较低：{percent:.1f}%",
            rule_id="coverage-low",
            category="test",
            confidence="high",
            recommendation="优先补充核心路径、错误分支和安全边界测试。",
            raw={"percent_covered": percent},
            target_rel=target_rel,
        )

    return findings


def _single_tool_status(result: dict[str, Any]) -> str:
    if result.get("skipped"):
        return "skipped"
    if result.get("timed_out"):
        return "timed_out"
    if not result.get("available", False):
        return "unavailable"
    if result.get("exit_code") == 0:
        return "completed"
    if result.get("exit_code") is not None:
        return "completed_nonzero_exit"
    return "unknown"


def _composite_tool_status(result: dict[str, Any]) -> tuple[str, list[str]]:
    component_statuses: list[str] = []
    for component in ("cc", "mi", "raw", "run", "json_export"):
        child = result.get(component)
        if isinstance(child, dict):
            component_statuses.append(_single_tool_status(child))

    if not component_statuses:
        return _single_tool_status(result), component_statuses
    if any(status == "completed" for status in component_statuses):
        if any(status in {"timed_out", "unavailable", "unknown"} for status in component_statuses):
            return "partial", component_statuses
        return "completed", component_statuses
    if any(status == "completed_nonzero_exit" for status in component_statuses):
        return "completed_nonzero_exit", component_statuses
    if any(status == "timed_out" for status in component_statuses):
        return "timed_out", component_statuses
    if all(status == "skipped" for status in component_statuses):
        return "skipped", component_statuses
    if all(status == "unavailable" for status in component_statuses):
        return "unavailable", component_statuses
    return "unknown", component_statuses


def tool_coverage_summary(tools: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    summary: dict[str, dict[str, Any]] = {}
    for name, result in tools.items():
        status, component_statuses = _composite_tool_status(result)
        summary[name] = {
            "status": status,
            "available": result.get("available", False),
            "exit_code": result.get("exit_code"),
            "skipped": result.get("skipped", False),
            "timed_out": result.get("timed_out", False),
            "reason": result.get("reason", ""),
            "command": result.get("command", ""),
        }
        if component_statuses:
            summary[name]["component_statuses"] = component_statuses
    return summary


def findings_summary(findings: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_severity_in_target: dict[str, int] = {}
    by_category: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    for finding in findings:
        by_tool[finding["source_tool"]] = by_tool.get(finding["source_tool"], 0) + 1
        by_severity[finding["severity"]] = by_severity.get(finding["severity"], 0) + 1
        if finding.get("in_target"):
            by_severity_in_target[finding["severity"]] = by_severity_in_target.get(finding["severity"], 0) + 1
        by_category[finding["category"]] = by_category.get(finding["category"], 0) + 1
        scope = finding.get("scope", "unknown")
        by_scope[scope] = by_scope.get(scope, 0) + 1
    return {
        "total": len(findings),
        "target_total": sum(1 for finding in findings if finding.get("in_target")),
        "project_context_total": sum(1 for finding in findings if not finding.get("in_target")),
        "by_tool": by_tool,
        "by_severity": by_severity,
        "by_severity_in_target": by_severity_in_target,
        "by_category": by_category,
        "by_scope": by_scope,
    }


def run_checks(project_root: Path, target: Path, install_project_deps: bool) -> dict[str, Any]:
    project_root = project_root.resolve()
    if not target.is_absolute():
        target = project_root / target
    target = target.resolve()
    target_rel = relative_path(str(target), str(project_root))
    discovery = discover_project_files(project_root, target)
    dependency_files = discovery.get("dependency_files", [])
    test_files = discovery.get("test_files", [])

    if install_project_deps:
        dependency_install = run_command(
            "python -m pip install --disable-pip-version-check -e .",
            project_root,
            max_output=20000,
            timeout_seconds=180,
        )
    else:
        dependency_install = skipped_command(
            "python -m pip install --disable-pip-version-check -e .",
            "disabled by default; use --install-project-deps to install project dependencies",
        )
    project_deps_ready = dependency_install.get("exit_code") == 0

    if not project_deps_ready:
        pip_audit = skipped_command(
            "pip-audit . --format json --progress-spinner off",
            "project dependencies are not installed; skipped to avoid slow or incomplete dependency audit",
        )
    elif dependency_files:
        pip_audit = run_command("pip-audit . --format json --progress-spinner off", project_root, max_output=30000, timeout_seconds=90)
    else:
        pip_audit = skipped_command("pip-audit . --format json --progress-spinner off", "no dependency manifest found under the project root")

    if not project_deps_ready:
        pytest_result = skipped_command(
            "python -m pytest --maxfail=20 --disable-warnings -q",
            "project dependencies are not installed; skipped because pytest would fail during import collection",
        )
        coverage_result = skipped_command(
            "python -m coverage run -m pytest --maxfail=20 --disable-warnings -q",
            "project dependencies are not installed; skipped because coverage depends on pytest execution",
        )
    elif test_files:
        pytest_result = run_python_module("pytest", "--maxfail=20 --disable-warnings -q", project_root, max_output=30000, timeout_seconds=120)
        if pytest_result.get("available") and pytest_result.get("exit_code") in (0, 1):
            coverage_run = run_python_module("coverage", "run -m pytest --maxfail=20 --disable-warnings -q", project_root, max_output=30000, timeout_seconds=180)
            if coverage_run.get("available"):
                coverage_json = run_python_module("coverage", "json -o .coverage.json", project_root, max_output=12000, timeout_seconds=60)
                coverage_result = {
                    "available": coverage_run.get("available", False),
                    "command": "python -m coverage run -m pytest --maxfail=20 --disable-warnings -q && python -m coverage json -o .coverage.json",
                    "cwd": str(project_root),
                    "run": coverage_run,
                    "json_export": coverage_json,
                    "summary": coverage_json_result(project_root),
                    "note": "Coverage reflects the discovered pytest suite only; import failures or missing project dependencies can make it incomplete.",
                }
            else:
                coverage_result = coverage_run
        else:
            coverage_result = skipped_command(
                "python -m coverage run -m pytest --maxfail=20 --disable-warnings -q",
                "pytest was unavailable or failed before coverage could be collected",
            )
    else:
        pytest_result = skipped_command("python -m pytest --maxfail=20 --disable-warnings -q", "no pytest test files discovered under the project root")
        coverage_result = skipped_command("python -m coverage run -m pytest --maxfail=20 --disable-warnings -q", "no pytest test files discovered under the project root")

    deptry_result = run_command("deptry . --json-output .deptry.json --extend-exclude node_modules --extend-exclude dist --extend-exclude build", project_root, max_output=30000, timeout_seconds=90)
    if deptry_result.get("available"):
        deptry_result["json_file"] = read_json_file(project_root / ".deptry.json")

    radon_cc = run_command(f"radon cc {shlex.quote(target_rel)} --json --show-complexity", project_root, max_output=30000, timeout_seconds=60)
    if radon_cc.get("available"):
        radon_result = {
            "available": True,
            "command": f"radon cc/mi/raw {shlex.quote(target_rel)} --json",
            "cwd": str(project_root),
            "cc": radon_cc,
            "mi": run_command(f"radon mi {shlex.quote(target_rel)} --json --show", project_root, max_output=30000, timeout_seconds=60),
            "raw": run_command(f"radon raw {shlex.quote(target_rel)} --json --summary", project_root, max_output=30000, timeout_seconds=60),
            "note": "radon is dependency-free AST/static metric analysis for complexity and maintainability.",
        }
    else:
        radon_result = radon_cc

    tools = {
        "ruff": run_command(f"ruff check {shlex.quote(target_rel)} --output-format json --no-cache", project_root, timeout_seconds=60),
        "mypy": run_command(
            f"mypy {shlex.quote(target_rel)} --hide-error-context --no-error-summary --no-incremental --ignore-missing-imports",
            project_root,
            timeout_seconds=90,
        ),
        "bandit": run_command(f"bandit -r {shlex.quote(target_rel)} -f json -q", project_root, max_output=30000, timeout_seconds=60),
        "pip_audit": pip_audit,
        "pytest": pytest_result,
        "coverage": coverage_result,
        "deptry": deptry_result,
        "interrogate": run_command(f"interrogate --fail-under=0 --verbose {shlex.quote(target_rel)}", project_root, max_output=20000, timeout_seconds=60),
        "radon": radon_result,
        "detect_secrets": run_command(
            "detect-secrets scan --all-files --exclude-files "
            + shlex.quote(EXCLUDED_SCAN_PATTERN)
            + " .",
            project_root,
            max_output=50000,
            timeout_seconds=60,
        ),
    }

    result: dict[str, Any] = {
        "schema_version": "code-health-tools-v5",
        "project_root": str(project_root),
        "target": str(target),
        "target_rel": target_rel,
        "coverage_notes": {
            "mypy_mode": "lightweight_ignore_missing_imports",
            "mypy_limitation": "Project dependencies are not installed by default, so mypy runs with --ignore-missing-imports. Treat mypy findings as partial type-check coverage, not a full CI-equivalent type check.",
            "dependency_install_default": "disabled",
            "dependency_install_enable": "use --install-project-deps",
            "dependency_required_tools": ["pip_audit", "pytest", "coverage"],
            "pytest_coverage_limitation": "pytest and coverage use the discovered project test suite. If project dependencies are not installed, import failures can make test and coverage results incomplete.",
            "deptry_limitation": "deptry can read dependency declarations from project manifests, but transitive dependency analysis can be incomplete when project dependencies are not installed.",
        },
        "discovery": discovery,
        "setup": {"dependency_install": dependency_install},
        "tools": tools,
    }
    result["tool_coverage"] = tool_coverage_summary(tools)
    result["findings"] = normalize_findings(result, project_root)
    result["findings_summary"] = findings_summary(result["findings"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic code-health checks and emit JSON.")
    parser.add_argument("--project-root", default=".", help="Project root for dependency manifests and test execution.")
    parser.add_argument("--target", default=".", help="Code path to scan.")
    parser.add_argument("--install-project-deps", action="store_true", help="Install project dependencies before dependency-required tools.")
    parser.add_argument("--out", default="", help="Optional output JSON path.")
    parser.add_argument("--summary", action="store_true", help="Print a compact summary instead of the full JSON result.")
    args = parser.parse_args()

    result = run_checks(Path(args.project_root), Path(args.target), args.install_project_deps)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text, encoding="utf-8")
    if args.summary:
        summary = {
            "saved_to": args.out,
            "schema_version": result.get("schema_version"),
            "project_root": result.get("project_root"),
            "target": result.get("target"),
            "target_rel": result.get("target_rel"),
            "python_files": len(result.get("discovery", {}).get("python_files", [])),
            "test_files": len(result.get("discovery", {}).get("test_files", [])),
            "dependency_files": result.get("discovery", {}).get("dependency_files", []),
            "tool_coverage_status": {
                name: item.get("status")
                for name, item in result.get("tool_coverage", {}).items()
            },
            "findings_summary": result.get("findings_summary", {}),
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
