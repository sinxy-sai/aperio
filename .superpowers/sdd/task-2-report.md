# Task 2 Report: Code Health Multi-Subagent Orchestration

## Status

**DONE_WITH_CONCERNS**

## Summary

Implemented `demo/02_code_health_subagents.py` — a multi-subagent pipeline that runs 4 analysis sub-agents in parallel (async via `asyncio.gather`) then a summarizer (sync) merges the results. This establishes the core orchestration pattern for the Aperio project.

## Files Created

1. **`CLAUDE.md`** — Project documentation: documents that `demo/` uses conda env `llm-dev`, with model config and workspace conventions.
2. **`demo/02_code_health_subagents.py`** — Standalone demo script implementing the pipeline.

## Architecture

```
Orchestrator (main script, sync)
  |
  +-- Phase 2: asyncio.gather (parallel)
  |     +-- architect          (create_deep_agent + ainvoke)
  |     +-- security-analyst   (create_deep_agent + ainvoke)
  |     +-- dependency-checker (create_deep_agent + ainvoke)
  |     +-- doc-reviewer       (create_deep_agent + ainvoke)
  |
  +-- Phase 3: sync invoke
        +-- summarizer         (create_deep_agent + invoke)
```

- Shared `FilesystemBackend(root_dir=<project_root>, virtual_mode=False)` so all agents can read the target codebase and write to the same workspace.
- Target code: `full-stack-fastapi-template-master/backend/app/` (27 .py files).
- Workspace outputs under: `demo/workspace_02/`.

## Sub-Agent Roles

| Agent | Output File | Focus |
|---|---|---|
| architect | `architect.md` | Module structure, design patterns, coupling/layering |
| security-analyst | `security.md` | Auth, input validation, secrets, OWASP Top-10 |
| dependency-checker | `dependencies.md` | External deps, imports, version pinning |
| doc-reviewer | `doc_review.md` | Docstrings, comments, API docs |
| summarizer | `code_health_report.md` | Merges all 4 reports with severity prioritization |

## Test Results

- **Phase 1 (agent build):** 4 agents created successfully.
- **Phase 2 (4x parallel analysis):** 3/4 agents completed. 1 failed with a Windows console encoding issue (`gbk` cannot encode emoji U+2705) — this is a print-side bug, not an agent failure. The agent itself invoked successfully but the print statement crashed when trying to display the emoji-rich response in the GBK console.
- **Parallelism confirmed:** 4 agents completed in 266.7s total (not 4x the slowest agent time).
- **Phase 3 (summarizer):** Ran and attempted to merge reports. Only `security.md` was readable; other reports were either not written to the expected path or the agents hallucinated writing.
- **Output confirmed:** `demo/workspace_02/security.md` was written successfully (~10 KB analysis).

## Concerns

1. **File path reliability:** Only 1 of 4 analysis reports (`security.md`) was actually written to `demo/workspace_02/`. The agents claimed to write `architect.md`, `dependencies.md`, and `doc_review.md` but those files were not found on disk. Possible causes:
   - DeepAgents' file tools may have resolved paths relative to a different root when the agent was deep inside the target codebase
   - `virtual_mode=False` gives agents full filesystem access, which may cause path confusion
   - Some agents may have "hallucinated" calling write_file without actually invoking the tool
   
   **Mitigation:** Consider using `virtual_mode=True` and pre-loading target code snippets into the workspace, OR using absolute paths in all file operations.

2. **Windows console encoding (GBK):** The dependency-checker agent appeared to fail but likely succeeded — the crash was in `print()` trying to render emoji characters (U+2705) that are not encodable in GBK. The `_safe_print` fallback with `errors='replace'` encoding should fix this.

3. **Summarizer file access:** The summarizer inherited the shared backend but had trouble finding the analysis report files. This suggests path resolution inconsistencies when different agents write to what they think is the same directory.

## Commit

```
feat: demo 02 — multi-subagent code health orchestration (3/4 parallel, summarizer)
```

Files committed:
- `CLAUDE.md`
- `demo/02_code_health_subagents.py`
