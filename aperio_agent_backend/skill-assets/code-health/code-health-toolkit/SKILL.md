---
name: code-health-toolkit
description: Reusable code-health scanning toolkit for Python projects. Use when running deterministic code quality, typing, security, dependency, documentation, complexity, secret, pytest, and coverage checks with ruff, mypy, bandit, pip-audit, deptry, interrogate, radon, detect-secrets, pytest, and coverage; emits a portable JSON schema with tool coverage and normalized findings.
---

## Role

Use this skill when deterministic code-health evidence is needed before LLM review. The bundled script is portable: it can run inside Aperio's Docker sandbox or directly in another Python project environment.

## Execution Order

When this skill is used as the first stage of a code-health workflow, run the toolkit script before reading source files. Do not call `ls`, `glob`, `grep`, or `read_file` to explore the target code before the script has produced `tool_results.json`.

After `tool_results.json` exists, prefer the generated `tool_results.compact.json` for model-facing analysis. Use its `discovery`, `tool_coverage`, `findings`, and compact `tools` summaries to decide which files, if any, need targeted follow-up reading. The full `tool_results.json` is retained for audit/download and should not be read into model context.

## Script

Run:

```bash
python scripts/run_checks.py \
  --project-root <project-root> \
  --target <target-relative-path> \
  --out <output-json-path> \
  --summary
```

Optional flags:

- `--install-project-deps`: install the target project with `python -m pip install -e .` before dependency-required checks.
- `--out <path>`: write the full JSON result to a file while also printing JSON to stdout.
- `--summary`: when `--out` is used, print only a compact summary to stdout while preserving the full JSON in the output file.

Parameter rules:

- Replace every `<...>` placeholder from the current task context. Do not copy placeholders literally.
- `--project-root` is the root of the project being scanned, usually the directory that contains dependency manifests such as `pyproject.toml`, `requirements.txt`, or `poetry.lock`.
- `--target` is a path relative to `--project-root`. Use the target requested by the user, the orchestrator, or the local policy. Use `.` only when the whole project should be scanned.
- Do not assume a fixed target such as `app`, `src`, or `app/core`; those are project-specific choices.
- `--out` is the workflow's raw evidence file path. In a report workflow, write the full result to a stable JSON artifact before drafting analysis.

In an environment that exposes this skill at `/skills`, run:

```bash
python /skills/code-health/code-health-toolkit/scripts/run_checks.py \
  --project-root <sandbox-project-root> \
  --target <target-relative-path> \
  --out <output-json-path> \
  --summary
```

In Aperio-like sandboxes, `/skills` is usually mounted read-only and `/outputs` is usually mounted to the local run workspace. Read the active orchestrator instructions or local policy to determine the actual project root, target path, and output path. Do not call individual scanners directly when this script is available; use the script as the stable entrypoint.

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
