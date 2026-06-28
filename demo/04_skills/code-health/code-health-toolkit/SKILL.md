---
name: code-health-toolkit
description: Reusable code-health scanning toolkit for Python projects. Use when running deterministic code quality, typing, security, dependency, documentation, complexity, secret, pytest, and coverage checks with ruff, mypy, bandit, pip-audit, deptry, interrogate, radon, detect-secrets, pytest, and coverage; emits a portable JSON schema with tool coverage and normalized findings.
---

## Role

Use this skill when deterministic code-health evidence is needed before LLM review. The bundled script is portable: it can run inside Aperio's Docker sandbox or directly in another Python project environment.

## Execution Order

When this skill is used as the first stage of a code-health workflow, run the toolkit script before reading source files. Do not call `ls`, `glob`, `grep`, or `read_file` to explore the target code before the script has produced `tool_results.json`.

After `tool_results.json` exists, use its `discovery`, `tool_coverage`, and `findings` fields to decide which files, if any, need targeted follow-up reading.

## Script

Run:

```bash
python scripts/run_checks.py --project-root . --target app
```

Optional flags:

- `--install-project-deps`: install the target project with `python -m pip install -e .` before dependency-required checks.
- `--out <path>`: write the full JSON result to a file while also printing JSON to stdout.
- `--summary`: when `--out` is used, print only a compact summary to stdout while preserving the full JSON in the output file.

In an environment that exposes this skill at `/skills`, run:

```bash
python /skills/code-health/code-health-toolkit/scripts/run_checks.py \
  --project-root /workspace/project \
  --target app/core \
  --out /outputs/code_health/raw/tool_results.json \
  --summary
```

In Aperio, `/skills` is mounted read-only and `/outputs` is mounted to the local run workspace. Do not call individual scanners directly when this script is available; use the script as the stable entrypoint.

## Output Schema

The script emits JSON with these top-level fields:

- `schema_version`: schema identifier.
- `project_root`: project root used for commands.
- `target`: scan target path.
- `target_rel`: target relative to project root.
- `coverage_notes`: limitations for mypy, dependency install, pytest, coverage, and deptry.
- `discovery`: discovered Python files, tests, dependency files, and README files.
- `setup.dependency_install`: dependency installation result or skip reason.
- `tools`: raw outputs from ruff, mypy, bandit, pip-audit, pytest, coverage, deptry, interrogate, radon, and detect-secrets.
- `tool_coverage`: compact status table for each tool.
- `findings`: normalized tool-derived findings.
- `findings_summary`: counts by tool, severity, and category.

## Evidence Rules

- Treat `findings` as deterministic tool evidence, not final review judgment.
- Treat skipped, timed-out, or unavailable tools as coverage limits.
- Do not claim dependency safety when pip-audit is skipped or timed out.
- Do not treat mypy with `--ignore-missing-imports` as full CI type checking.
- Treat detect-secrets results as suspected secrets requiring human review.
