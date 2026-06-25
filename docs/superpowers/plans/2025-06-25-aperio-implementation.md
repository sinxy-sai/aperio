# Aperio Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build Aperio — a dual-module software quality platform (Code Health Diagnosis + PRD Review) powered by DeepAgents multi-agent orchestration.

**Architecture:** Three-phase development: Phase 1 validates all DeepAgents logic in standalone `demo/` scripts (like course exercises); Phase 2 embeds verified logic into the FastAPI full-stack template; Phase 3 polishes UI, tests, and deliverables.

**Tech Stack:** Python 3.10+, DeepAgents (LangGraph), FastAPI + SQLModel, React 19 + TypeScript + shadcn/ui, Docker, LangSmith

---

## Global Constraints

- Model default: `deepseek-v4-flash` via `init_chat_model` (OpenAI-compatible API)
- All demo scripts are standalone `.py` files, runnable with `python demo/xx.py`
- Phase 1 uses `demo/` directory; Phase 2 works inside `full-stack-fastapi-template-master/`
- Commit after every task that produces working code
- Test each demo script before moving to the next

---

## File Structure

```
project/
├── design.md                          # Already exists
├── .gitignore                         # Already exists
│
├── demo/                              # Phase 1 — standalone DeepAgents scripts
│   ├── 01_basic_agent.py             # DeepAgent connectivity test
│   ├── 02_code_health_subagents.py   # Code health: Orchestrator + 4 async sub-agents
│   ├── 03_prd_review_subagents.py    # PRD review: Writer + 4 async sub-agents
│   ├── 04_skills/                    # Skill system validation
│   │   ├── general/                  # 4 general skills
│   │   │   ├── skill-creator/SKILL.md
│   │   │   ├── report-writing/SKILL.md
│   │   │   ├── review-matrix/SKILL.md
│   │   │   └── tool-usage/SKILL.md
│   │   ├── code-health/              # 4 code-health skills
│   │   │   ├── code-architect/SKILL.md
│   │   │   ├── code-security/SKILL.md
│   │   │   ├── code-dependency/SKILL.md
│   │   │   └── code-documentation/SKILL.md
│   │   ├── prd-review/               # 4 PRD review skills
│   │   │   ├── review-tech/SKILL.md
│   │   │   ├── review-ux/SKILL.md
│   │   │   ├── review-test/SKILL.md
│   │   │   └── review-ops/SKILL.md
│   │   └── test_skills.py           # Verify all skills load correctly
│   ├── 05_context_engineering.py     # Write/Select/Compress/Isolate verification
│   ├── 05_context_engineering/       # Generated output files for context eng demo
│   ├── 06_sandbox_hitl.py            # Docker Sandbox + HITL approval
│   ├── 07_middleware_langsmith.py    # PerformanceMiddleware + LangSmith tracing
│   ├── 08_longterm_memory.py         # StoreBackend + /memories/ cross-thread
│   └── 08_longterm_memory/           # Generated memory files
│
└── full-stack-fastapi-template-master/  # Phase 2 — FastAPI template integration
    └── (modified in Phase 2)
```

---

### Task 1: Project Scaffold & Basic Agent Connectivity

**Goal:** Verify DeepAgents can connect to the LLM, execute a simple task, and use `FilesystemBackend` for file I/O. This is the "hello world" that validates the entire environment.

**Dependencies:** None (first task)

**Files:**
- Create: `demo/01_basic_agent.py`

**Interfaces:**
- Produces: N/A (leaf demo script, no downstream consumers)

---

- [ ] **Step 1: Write `demo/01_basic_agent.py`**

```python
"""
Demo 01: Basic DeepAgent connectivity test.
Verifies: model connection, basic tool use, FilesystemBackend, write_todos.
"""
import os
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model


def main():
    # 1. Initialize model (uses DEEPSEEK_API_KEY from env)
    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    # 2. Create backend for file I/O
    backend = FilesystemBackend(root_dir="demo/workspace_01")

    # 3. Create agent with minimal tools
    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt="""You are a helpful assistant. When asked to research,
use write_todos to plan, write files to organize information, and read files to recall.""",
    )

    # 4. Run a simple task
    print("=" * 60)
    print("Demo 01: Basic Agent Connectivity Test")
    print("=" * 60)

    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": "请用中文回答：1) 你是谁？2) 简单介绍一下 LangGraph 框架（50字以内）。"
                "把回答写入 demo/workspace_01/intro.md",
            }
        ]
    })

    # 5. Print result
    final_msg = result["messages"][-1]
    print(f"\nFinal response:\n{final_msg.content}")
    print("\n✅ Demo 01 complete!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
cd "c:/Users/liubingliang/Desktop/bnucode/人工智能交叉学科项目应用实践/project"
python demo/01_basic_agent.py
```

Expected: Agent responds in Chinese, writes `demo/workspace_01/intro.md`.

- [ ] **Step 3: Verify the output file exists**

```bash
ls demo/workspace_01/intro.md && cat demo/workspace_01/intro.md
```

- [ ] **Step 4: Commit**

```bash
git add demo/01_basic_agent.py
git commit -m "feat: demo 01 — basic DeepAgent connectivity test"
```

---

### Task 2: Code Health Module — Multi-Subagent Orchestration

**Goal:** Implement the code health diagnosis pipeline: Orchestrator (sync) spawns 4 sub-agents (async, parallel) for architecture, security, dependency, and documentation analysis, then Summarizer (sync) merges results.

**Dependencies:** Task 1 (environment verified)

**Files:**
- Create: `demo/02_code_health_subagents.py`

**Interfaces:**
- Produces: Verified sub-agent pattern — Orchestrator + N parallel sub-agents + Summarizer

---

- [ ] **Step 1: Write `demo/02_code_health_subagents.py`**

```python
"""
Demo 02: Code Health Multi-Subagent Orchestration.
Architecture: Orchestrator (sync) → 4 sub-agents (async, parallel) → Summarizer (sync)
Sub-agents: architecture, security, dependency, documentation
"""
import os
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from langchain.chat_models import init_chat_model


# ---- System prompts for sub-agents (placeholder — will be replaced by SKILL.md in Task 4) ----

ARCHITECT_PROMPT = """You are a senior software architect. Analyze the given codebase and report on:
1. Directory/module structure — is it well-organized?
2. Module coupling — are there circular dependencies?
3. Layering — is separation of concerns maintained?
Output a structured report with findings ranked by severity (Critical/High/Medium/Low)."""

SECURITY_PROMPT = """You are an application security engineer. Analyze the code for:
1. SQL injection risks
2. Hardcoded secrets/tokens/passwords
3. Insecure deserialization
4. Path traversal
5. Missing input validation
Output findings ranked by severity."""

DEPENDENCY_PROMPT = """You are a dependency management expert. Analyze:
1. Outdated dependencies (check setup.py/pyproject.toml/requirements.txt)
2. Known CVEs in dependency versions
3. License compatibility issues
4. Unused or missing dependencies
Output findings ranked by severity."""

DOCUMENTATION_PROMPT = """You are a technical documentation reviewer. Analyze:
1. README quality and completeness
2. API documentation coverage
3. Code comment density on key functions
4. Missing docstrings
Output findings ranked by severity."""

ORCHESTRATOR_PROMPT = """You are a code health orchestrator. Your job:
1. Use write_todos to plan the 4 analysis tasks
2. Spawn 4 sub-agents in parallel (async) — one per analysis dimension
3. Each sub-agent analyzes the code and writes findings to /workspace/drafts/<dimension>.md
4. After all complete, spawn a summarizer sub-agent to merge results
5. The summarizer writes the final report to /workspace/final_report.md

Available sub-agents and their instructions are configured below. Trust them to do their jobs."""

SUMMARIZER_PROMPT = """You are a report summarizer. Read all 4 draft reports from /workspace/drafts/,
deduplicate overlapping findings, assign final severity ratings, compute a health score (0-100),
and write the merged report to /workspace/final_report.md. Format:

# Code Health Report
**Overall Health Score: XX/100**

## Risk Summary
| Severity | Count |
|----------|-------|
| Critical | N |
| High     | N |
| Medium   | N |
| Low      | N |

## Detailed Findings
(merged and deduplicated from the 4 reports, sorted by severity)

## Improvement Recommendations
(prioritized list)
"""


def main():
    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    # CompositeBackend: sandbox path for code, local FS for drafts and reports
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir="demo/workspace_02"),
    )

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=[
            {
                "name": "architect",
                "description": "Analyze code architecture: structure, coupling, layering",
                "system_prompt": ARCHITECT_PROMPT,
            },
            {
                "name": "security-analyst",
                "description": "Scan code for security vulnerabilities",
                "system_prompt": SECURITY_PROMPT,
            },
            {
                "name": "dependency-checker",
                "description": "Check dependencies for outdated versions, CVEs, license issues",
                "system_prompt": DEPENDENCY_PROMPT,
            },
            {
                "name": "doc-reviewer",
                "description": "Review documentation quality and coverage",
                "system_prompt": DOCUMENTATION_PROMPT,
            },
            {
                "name": "summarizer",
                "description": "Merge analysis reports into a final health report",
                "system_prompt": SUMMARIZER_PROMPT,
            },
        ],
    )

    print("=" * 60)
    print("Demo 02: Code Health Multi-Subagent Orchestration")
    print("=" * 60)

    # For demo, analyze the full-stack-fastapi-template-master code
    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "Analyze the codebase at full-stack-fastapi-template-master/backend/app/ "
                    "for code health across 4 dimensions: architecture, security, dependencies, documentation. "
                    "Spawn 4 parallel sub-agents (architect, security-analyst, dependency-checker, doc-reviewer), "
                    "then use the summarizer to merge findings into a final report at demo/workspace_02/final_report.md. "
                    "Write each sub-agent's draft to demo/workspace_02/drafts/<name>.md."
                ),
            }
        ]
    })

    final_msg = result["messages"][-1]
    print(f"\nFinal response:\n{final_msg.content[:500]}...")
    print("\n✅ Demo 02 complete! Check demo/workspace_02/final_report.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/02_code_health_subagents.py
```

Expected: 4 sub-agents run in parallel, drafts written to `demo/workspace_02/drafts/`, merged report at `demo/workspace_02/final_report.md`.

- [ ] **Step 3: Verify outputs**

```bash
ls demo/workspace_02/drafts/ && ls demo/workspace_02/final_report.md
```

- [ ] **Step 4: Commit**

```bash
git add demo/02_code_health_subagents.py
git commit -m "feat: demo 02 — code health multi-subagent orchestration (4 async + 2 sync)"
```

---

### Task 3: PRD Review Module — Multi-Subagent Orchestration

**Goal:** Implement the PRD review pipeline: Writer (sync) generates PRD → 4 review sub-agents (async, parallel) critique it → Editor (sync) merges feedback into revised PRD + review matrix.

**Dependencies:** Task 2 (sub-agent pattern verified)

**Files:**
- Create: `demo/03_prd_review_subagents.py`

**Interfaces:**
- Produces: Verified PRD review pipeline with review matrix output

---

- [ ] **Step 1: Write `demo/03_prd_review_subagents.py`**

```python
"""
Demo 03: PRD Review Multi-Subagent Orchestration.
Architecture: Writer (sync) → 4 reviewers (async, parallel) → Editor (sync)
Reviewers: tech, ux, test, ops
"""
import os
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model


WRITER_PROMPT = """You are a senior product manager. When given a feature idea, write a structured PRD (Product Requirements Document) with these sections:
1. Background & Goals
2. User Stories
3. Feature Scope (MVP + out of scope)
4. User Interaction Flow
5. Acceptance Criteria
6. Non-functional Requirements
Write the PRD to /workspace/prd_v1.md in Chinese."""

TECH_REVIEW_PROMPT = """You are a senior tech lead reviewing a PRD. Critique:
1. Technical feasibility — can this be built with typical web tech?
2. Architecture implications — significant refactoring needed?
3. API design — are the endpoints sensibly defined?
4. Security considerations — any obvious risks?
Output findings as a structured review with severity ratings."""

UX_REVIEW_PROMPT = """You are a senior UX designer reviewing a PRD. Critique:
1. User flow — is the path minimal and intuitive?
2. Edge cases — loading, empty, error states covered?
3. Consistency — does it match existing patterns?
4. Accessibility considerations
Output findings as a structured review with severity ratings."""

TEST_REVIEW_PROMPT = """You are a senior QA engineer reviewing a PRD. Critique:
1. Testability — are acceptance criteria measurable?
2. Boundary conditions — edge cases identified?
3. Performance requirements — are they specific enough?
4. Regression risk — what might break?
Output findings as a structured review with severity ratings."""

OPS_REVIEW_PROMPT = """You are a product operations manager reviewing a PRD. Critique:
1. Business value — does this align with product goals?
2. Launch strategy — rollout plan, feature flags?
3. Risk assessment — what could go wrong post-launch?
4. Competitive analysis — how does this compare?
Output findings as a structured review with severity ratings."""

ORCHESTRATOR_PROMPT = """You are a PRD review orchestrator. Your job:
1. Use write_todos to plan: write PRD → 4 parallel reviews → merge
2. First, spawn the writer sub-agent to generate the PRD
3. Then spawn 4 review sub-agents in parallel (tech, ux, test, ops)
4. Each reviewer reads the PRD from /workspace/prd_v1.md and writes review to /workspace/drafts/review_<name>.md
5. Finally, spawn the editor sub-agent to merge all feedback into revised PRD + review matrix

Available sub-agents: writer, tech-reviewer, ux-reviewer, test-reviewer, ops-reviewer, editor"""

EDITOR_PROMPT = """You are a PRD editor. Read the original PRD at /workspace/prd_v1.md and all review drafts.
Produce two outputs:
1. /workspace/prd_final.md — revised PRD incorporating valid feedback
2. /workspace/review_matrix.md — a table summarizing all review feedback:

# Review Matrix
| # | Dimension | Severity | Issue | Suggestion | Status |
|---|-----------|----------|-------|------------|--------|
| 1 | Tech      | High     | ...   | ...        | Accepted/Rejected |

For each issue, mark whether it was accepted (incorporated) or rejected (with brief reason)."""


def main():
    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    backend = FilesystemBackend(root_dir="demo/workspace_03")

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=ORCHESTRATOR_PROMPT,
        subagents=[
            {
                "name": "writer",
                "description": "Generate a structured PRD from a feature idea",
                "system_prompt": WRITER_PROMPT,
            },
            {
                "name": "tech-reviewer",
                "description": "Review PRD from technical feasibility perspective",
                "system_prompt": TECH_REVIEW_PROMPT,
            },
            {
                "name": "ux-reviewer",
                "description": "Review PRD from user experience perspective",
                "system_prompt": UX_REVIEW_PROMPT,
            },
            {
                "name": "test-reviewer",
                "description": "Review PRD from QA and testability perspective",
                "system_prompt": TEST_REVIEW_PROMPT,
            },
            {
                "name": "ops-reviewer",
                "description": "Review PRD from business and operations perspective",
                "system_prompt": OPS_REVIEW_PROMPT,
            },
            {
                "name": "editor",
                "description": "Merge reviews into revised PRD and review matrix",
                "system_prompt": EDITOR_PROMPT,
            },
        ],
    )

    print("=" * 60)
    print("Demo 03: PRD Review Multi-Subagent Orchestration")
    print("=" * 60)

    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "I want to add a 'Dark Mode' feature to our web application. "
                    "Please run the full PRD review pipeline: "
                    "writer → tech-reviewer + ux-reviewer + test-reviewer + ops-reviewer (parallel) → editor. "
                    "Write all outputs to demo/workspace_03/."
                ),
            }
        ]
    })

    final_msg = result["messages"][-1]
    print(f"\nFinal response:\n{final_msg.content[:500]}...")
    print("\n✅ Demo 03 complete! Check demo/workspace_03/prd_final.md and review_matrix.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/03_prd_review_subagents.py
```

Expected: Writer produces PRD, 4 reviewers run in parallel, Editor merges into `prd_final.md` and `review_matrix.md`.

- [ ] **Step 3: Verify outputs**

```bash
ls demo/workspace_03/prd_v1.md demo/workspace_03/prd_final.md demo/workspace_03/review_matrix.md
ls demo/workspace_03/drafts/
```

- [ ] **Step 4: Commit**

```bash
git add demo/03_prd_review_subagents.py
git commit -m "feat: demo 03 — PRD review multi-subagent orchestration (4 async reviewers + Writer + Editor)"
```

---

### Task 4: Skill System — All 12 SKILL.md Files

**Goal:** Write all 12 SKILL.md files with proper YAML frontmatter and role-specific content. Verify they load correctly via DeepAgents skill registry.

**Dependencies:** Tasks 2-3 (sub-agent roles defined)

**Files:**
- Create: `demo/04_skills/general/skill-creator/SKILL.md`
- Create: `demo/04_skills/general/report-writing/SKILL.md`
- Create: `demo/04_skills/general/review-matrix/SKILL.md`
- Create: `demo/04_skills/general/tool-usage/SKILL.md`
- Create: `demo/04_skills/code-health/code-architect/SKILL.md`
- Create: `demo/04_skills/code-health/code-security/SKILL.md`
- Create: `demo/04_skills/code-health/code-dependency/SKILL.md`
- Create: `demo/04_skills/code-health/code-documentation/SKILL.md`
- Create: `demo/04_skills/prd-review/review-tech/SKILL.md`
- Create: `demo/04_skills/prd-review/review-ux/SKILL.md`
- Create: `demo/04_skills/prd-review/review-test/SKILL.md`
- Create: `demo/04_skills/prd-review/review-ops/SKILL.md`
- Create: `demo/04_skills/test_skills.py`

**Interfaces:**
- Consumes: Agent role definitions from Tasks 2-3
- Produces: Complete skill registry consumable by all subsequent demos

---

- [ ] **Step 1: Create directory structure**

```bash
mkdir -p demo/04_skills/general/{skill-creator,report-writing,review-matrix,tool-usage}
mkdir -p demo/04_skills/code-health/{code-architect,code-security,code-dependency,code-documentation}
mkdir -p demo/04_skills/prd-review/{review-tech,review-ux,review-test,review-ops}
```

- [ ] **Step 2: Write general skills (4 files)**

Write `demo/04_skills/general/skill-creator/SKILL.md`:

```markdown
---
name: skill-creator
description: Guide for creating effective skills. Use when a task requires expertise beyond the agent's current skills — teaches how to design new skills with proper structure.
triggers:
  - create skill
  - new skill
  - design skill
---

## Overview
This skill enables agents to create or refine other skills. It defines the standard
SKILL.md format: YAML frontmatter (name, description, triggers) followed by Markdown
body (role definition, workflow, checklist, output format).

## When to Use
- An agent needs to perform a task outside its current skill set
- An existing skill needs refinement based on task performance

## SKILL.md Template
```markdown
---
name: <skill-name>
description: <one-line description>
triggers:
  - <trigger keyword 1>
  - <trigger keyword 2>
---

## Role Definition
<Clear definition of the role this skill enables>

## Workflow
<Step-by-step process>

## Checklist
- [ ] <checklist item>

## Output Format
<Expected output structure>
```
```

Write `demo/04_skills/general/report-writing/SKILL.md`:

```markdown
---
name: report-writing
description: Standard format for all Aperio reports — health reports and PRD documents. Ensures consistent structure across all sub-agent outputs.
triggers:
  - write report
  - generate report
  - final report
  - summary
---

## Role Definition
You produce structured, scannable reports following Aperio's standard format.

## Health Report Format
```markdown
# [Title]
**Overall Health Score: XX/100** — [one-line summary]

## Risk Summary
| Severity | Count |
|----------|-------|
| Critical | N |
| High     | N |
| Medium   | N |
| Low      | N |

## Detailed Findings
| # | Severity | Location | Issue | Recommendation |
|---|----------|----------|-------|----------------|

## Trend (if historical data available)
Compared to previous scan: X new issues, Y resolved.

## Recommendations
Prioritized action items.
```

## PRD Format
```markdown
# [Feature Name] PRD

## 1. Background & Goals
## 2. User Stories
## 3. Feature Scope
## 4. User Interaction Flow
## 5. Acceptance Criteria
## 6. Non-functional Requirements
```
```

Write `demo/04_skills/general/review-matrix/SKILL.md`:

```markdown
---
name: review-matrix
description: Standard format for the PRD review matrix — a consolidated table of all reviewer feedback with acceptance/rejection status.
triggers:
  - review matrix
  - merge reviews
  - consolidate feedback
---

## Role Definition
You consolidate multiple review outputs into a standardized review matrix.

## Review Matrix Format
| # | Dimension | Severity | Issue Description | Suggestion | Status |
|---|-----------|----------|-------------------|------------|--------|
| 1 | Tech/UX/Test/Ops | Critical/High/Medium/Low | ... | ... | Accepted/Rejected |

## Severity Guidelines
- **Critical**: Blocking — makes the feature unbuildable or dangerous
- **High**: Major concern — should be addressed before launch
- **Medium**: Improvement opportunity — address if time permits
- **Low**: Nice to have — can be deferred

## Status Guidelines
- **Accepted**: Feedback incorporated into revised PRD
- **Rejected**: Feedback considered but not applied (with brief reason)
```

Write `demo/04_skills/general/tool-usage/SKILL.md`:

```markdown
---
name: tool-usage
description: Guidelines for using tools safely and effectively — file I/O, sandbox commands, timeouts, retries.
triggers:
  - use tools
  - tool call
  - execute command
  - sandbox
---

## File I/O Rules
- Use `write_file` for outputs, `read_file` for inputs, `ls` for exploration
- Never read `.env`, `.git/`, or credential files
- Respect path permissions defined in FilesystemPermission rules

## Sandbox Command Execution
- All code execution MUST happen in Docker sandbox (paths under `/workspace/*/code/`)
- Commands have a 30-second default timeout
- If a command fails, do NOT retry destructive operations

## Retry Strategy
- API call failures: exponential backoff, max 3 retries
- File I/O failures: single retry, then report error
- Sub-agent timeout (>5 min): terminate, continue with remaining results

## Output Hygiene
- Always specify output file paths before starting work
- Clean up temporary files after task completion
```

- [ ] **Step 3: Write code-health skills (4 files)**

Write `demo/04_skills/code-health/code-architect/SKILL.md`:

```markdown
---
name: code-architect
description: Analyze code architecture — directory structure, module coupling, layering, and design patterns.
triggers:
  - architecture analysis
  - code structure
  - module coupling
---

## Role Definition
You are a senior software architect specializing in codebase structure analysis.

## Workflow
1. `ls` the codebase to understand directory structure
2. Identify entry points (main.py, app.py, index.js)
3. Trace module dependencies — look for circular imports
4. Evaluate layering: presentation → business logic → data access
5. Check for God classes, utility dumping grounds, mixed concerns

## Checklist
- [ ] Directory structure is logical and navigable
- [ ] No circular dependencies between modules
- [ ] Clear separation of concerns (presentation / logic / data)
- [ ] Consistent naming conventions
- [ ] Appropriate package/module granularity

## Output Format
| # | Severity | File/Module | Issue | Recommendation |
|---|----------|-------------|-------|----------------|
```

Write `demo/04_skills/code-health/code-security/SKILL.md`:

```markdown
---
name: code-security
description: Scan code for security vulnerabilities — SQL injection, XSS, hardcoded secrets, insecure deserialization, path traversal.
triggers:
  - security scan
  - vulnerability
  - security audit
---

## Role Definition
You are an application security engineer (AppSec) specializing in Python and JavaScript/TypeScript code audit.

## Workflow
1. In Docker sandbox, run `bandit -r /code/` for Python (if applicable)
2. Run `semgrep --config=auto /code/` for multi-language scanning
3. Manually review results — remove false positives
4. Classify: Critical (exploitable remotely) / High (data leak) / Medium (best practice) / Low (informational)

## Checklist
- [ ] SQL injection (string concatenation in queries)
- [ ] XSS (unescaped user input in HTML)
- [ ] Hardcoded secrets (API keys, tokens, passwords)
- [ ] Insecure deserialization (pickle, yaml.load)
- [ ] Path traversal (unsanitized file paths)
- [ ] Missing authentication on sensitive endpoints
- [ ] Known CVEs in dependencies

## Output Format
| # | Severity | File:Line | Issue | Fix |
|---|----------|-----------|-------|-----|
```

Write `demo/04_skills/code-health/code-dependency/SKILL.md`:

```markdown
---
name: code-dependency
description: Analyze project dependencies — outdated versions, known CVEs, license compatibility, unused packages.
triggers:
  - dependency check
  - package audit
  - dependency analysis
---

## Role Definition
You are a dependency management specialist. You analyze package manifests for risks.

## Workflow
1. Locate dependency files: pyproject.toml, requirements.txt, package.json, etc.
2. Check each direct dependency version against latest stable
3. Flag packages with known vulnerabilities (refer to CVE database)
4. Check license compatibility (GPL in proprietary code = risk)
5. Identify unused or missing dependencies

## Checklist
- [ ] Major version gaps (>1 major behind)
- [ ] Known CVE in any dependency
- [ ] Conflicting version constraints
- [ ] License incompatibility
- [ ] Unlisted transitive dependencies

## Output Format
| # | Severity | Package | Current | Latest | Issue | Fix |
|---|----------|---------|---------|--------|-------|-----|
```

Write `demo/04_skills/code-health/code-documentation/SKILL.md`:

```markdown
---
name: code-documentation
description: Review documentation quality — README completeness, API docs, code comments, docstrings.
triggers:
  - documentation review
  - doc quality
  - README check
---

## Role Definition
You are a technical documentation specialist. You evaluate how well a codebase is documented.

## Workflow
1. Check README: does it explain what, why, how to run?
2. Check API documentation: are endpoints documented with params/responses?
3. Check code comments: are complex functions explained?
4. Check docstrings: are public functions covered?
5. Identify documentation gaps

## Checklist
- [ ] README has: project description, setup guide, usage examples
- [ ] Public API functions have docstrings
- [ ] Complex algorithms have inline comments
- [ ] Configuration is documented
- [ ] Contributing guide exists (for open-source projects)

## Output Format
| # | Severity | File/Function | Missing | Suggestion |
|---|----------|---------------|---------|------------|
```

- [ ] **Step 4: Write PRD review skills (4 files)**

Write `demo/04_skills/prd-review/review-tech/SKILL.md`:

```markdown
---
name: review-tech
description: Review PRD from technical feasibility perspective — architecture impact, API design, security considerations.
triggers:
  - technical review
  - tech feasibility
  - architecture review
---

## Role Definition
You are a senior tech lead / architect reviewing product requirements for technical soundness.

## Review Dimensions
1. **Feasibility**: Can this be built with the current tech stack? Any blockers?
2. **Architecture Impact**: Significant refactoring needed? New services?
3. **API Design**: Are endpoints/contracts clearly defined?
4. **Security**: Any obvious security concerns in the design?
5. **Performance**: Are performance expectations realistic?
6. **Dependencies**: New third-party services or libraries needed?

## Output Format
| # | Dimension | Severity | Issue | Suggestion |
|---|-----------|----------|-------|------------|
```

Write `demo/04_skills/prd-review/review-ux/SKILL.md`:

```markdown
---
name: review-ux
description: Review PRD from UX design perspective — user flow, edge cases, consistency, accessibility.
triggers:
  - UX review
  - user experience
  - design review
---

## Role Definition
You are a senior UX designer reviewing product requirements for usability.

## Review Dimensions
1. **User Flow**: Is the path minimal and intuitive? Unnecessary steps?
2. **Edge Cases**: Loading, empty, error, timeout states covered?
3. **Consistency**: Does this match existing interaction patterns?
4. **Accessibility**: Keyboard navigation, screen readers, color contrast?
5. **Information Architecture**: Navigation logical?
6. **Mobile/Responsive**: Does it work on all screen sizes?

## Output Format
| # | Dimension | Severity | Issue | Suggestion |
|---|-----------|----------|-------|------------|
```

Write `demo/04_skills/prd-review/review-test/SKILL.md`:

```markdown
---
name: review-test
description: Review PRD from QA and testability perspective — acceptance criteria clarity, boundary conditions, regression risk.
triggers:
  - test review
  - QA review
  - testability
---

## Role Definition
You are a senior QA engineer reviewing product requirements for testability.

## Review Dimensions
1. **Acceptance Criteria**: Are they specific, measurable, testable?
2. **Boundary Conditions**: Edge cases and boundary values identified?
3. **Performance Requirements**: Quantifiable? (e.g. "page loads in <2s")
4. **Regression Risk**: What existing features might this break?
5. **Test Strategy**: What test types are needed? (unit/integration/e2e)
6. **Error Handling**: Are error scenarios and recovery paths defined?

## Output Format
| # | Dimension | Severity | Issue | Suggestion |
|---|-----------|----------|-------|------------|
```

Write `demo/04_skills/prd-review/review-ops/SKILL.md`:

```markdown
---
name: review-ops
description: Review PRD from business and operations perspective — value alignment, launch strategy, risk assessment, competitive analysis.
triggers:
  - operations review
  - business review
  - launch review
---

## Role Definition
You are a product operations manager reviewing requirements for business viability.

## Review Dimensions
1. **Business Value**: Does this align with product goals and strategy?
2. **Launch Strategy**: Feature flags, gradual rollout, A/B testing plan?
3. **Risk Assessment**: What could go wrong post-launch? Mitigation?
4. **Metrics**: How will success be measured? KPIs defined?
5. **Competitive Analysis**: How does this compare to competitors?
6. **Stakeholder Impact**: Who needs to be informed? Training needed?

## Output Format
| # | Dimension | Severity | Issue | Suggestion |
|---|-----------|----------|-------|------------|
```

- [ ] **Step 5: Write skill verification script**

Write `demo/04_skills/test_skills.py`:

```python
"""
Verify all 12 SKILL.md files are well-formed with required frontmatter fields.
"""
import os
import yaml
from pathlib import Path


def check_skill(filepath: str) -> dict:
    """Parse a SKILL.md file and validate its structure."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Must start with --- (YAML frontmatter)
    assert content.startswith("---"), f"{filepath}: missing YAML frontmatter"

    # Extract frontmatter between first and second ---
    parts = content.split("---", 2)
    assert len(parts) >= 3, f"{filepath}: malformed frontmatter"

    meta = yaml.safe_load(parts[1])
    body = parts[2].strip()

    # Required fields
    assert "name" in meta, f"{filepath}: missing 'name' in frontmatter"
    assert "description" in meta, f"{filepath}: missing 'description' in frontmatter"
    assert "triggers" in meta, f"{filepath}: missing 'triggers' in frontmatter"
    assert isinstance(meta["triggers"], list), f"{filepath}: 'triggers' must be a list"
    assert len(body) > 0, f"{filepath}: empty body"

    return {"name": meta["name"], "triggers": meta["triggers"], "size": len(body)}


def main():
    skills_root = Path("demo/04_skills")
    skill_files = sorted(skills_root.rglob("SKILL.md"))

    print(f"Found {len(skill_files)} SKILL.md files\n")

    results = []
    for sf in skill_files:
        try:
            info = check_skill(str(sf))
            results.append({"path": str(sf.relative_to(skills_root)), **info})
            print(f"  ✅ {sf.relative_to(skills_root)} — {info['name']}")
        except Exception as e:
            print(f"  ❌ {sf.relative_to(skills_root)} — {e}")
            results.append({"path": str(sf.relative_to(skills_root)), "error": str(e)})

    errors = [r for r in results if "error" in r]
    print(f"\n{'='*40}")
    print(f"Total: {len(results)} skills, {len(results) - len(errors)} passed, {len(errors)} failed")

    if errors:
        print("\nFailed skills:")
        for e in errors:
            print(f"  ❌ {e['path']}: {e['error']}")
        exit(1)
    else:
        print("\n✅ All skills valid!")
        print(f"Skill names: {[r['name'] for r in results]}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 6: Run verification**

```bash
pip install pyyaml
python demo/04_skills/test_skills.py
```

Expected: `✅ All skills valid!` with 12 skill names listed.

- [ ] **Step 7: Commit**

```bash
git add demo/04_skills/
git commit -m "feat: demo 04 — complete skill system (12 SKILL.md + validator)"
```

---

### Task 5: Context Engineering — Write / Select / Compress / Isolate

**Goal:** Demonstrate all four context engineering pillars in a single demo that runs the code health pipeline with explicit Write/Select/Compress/Isolate hooks.

**Dependencies:** Tasks 2, 4 (sub-agents + skills ready)

**Files:**
- Create: `demo/05_context_engineering.py`

**Interfaces:**
- Consumes: Sub-agent patterns from Task 2, skills from Task 4
- Produces: Verified context engineering dashboard — output files showing each pillar in action

---

- [ ] **Step 1: Write `demo/05_context_engineering.py`**

```python
"""
Demo 05: Context Engineering — Write / Select / Compress / Isolate.
Demonstrates all four pillars using the code health pipeline.

Pillars:
  Write    → Sub-agents write intermediate results to filesystem
  Select   → Summarizer selectively reads only needed files
  Compress → Each sub-agent compresses output to structured dict
  Isolate  → Each sub-agent has isolated context (own skill, no cross-contamination)
"""
import json
import os
from datetime import datetime
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from langchain.chat_models import init_chat_model


def main():
    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    backend = CompositeBackend(
        default=FilesystemBackend(root_dir="demo/workspace_05"),
    )

    # Load skills from file system (progressive disclosure)
    def load_skill(name):
        path = f"demo/04_skills/{name}/SKILL.md"
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        return ""

    architect_skill = load_skill("code-health/code-architect")
    security_skill = load_skill("code-health/code-security")
    report_skill = load_skill("general/report-writing")

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=f"""{report_skill}

You are a context engineering demo orchestrator. Your job demonstrates four pillars:

1. WRITE: Each sub-agent writes its findings to /workspace/drafts/<name>.json
2. SELECT: The summarizer uses ls to discover drafts, then selectively reads them
3. COMPRESS: Each sub-agent outputs compact JSON (not full conversation history)
4. ISOLATE: Each sub-agent only has its own skill, not others'

IMPORTANT: At each step, explain which pillar you're demonstrating.""",
        subagents=[
            {"name": "architect", "description": "Architecture analysis (isolated context)",
             "system_prompt": architect_skill},
            {"name": "security-analyst", "description": "Security scanning (isolated context)",
             "system_prompt": security_skill},
        ],
    )

    print("=" * 60)
    print("Demo 05: Context Engineering — Write / Select / Compress / Isolate")
    print("=" * 60)

    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "Run a context engineering demo on full-stack-fastapi-template-master/backend/app/:\\n\\n"
                    "Step 1 (WRITE): Spawn architect and security-analyst in parallel. "
                    "Each writes findings to /workspace/drafts/<name>.json\\n\\n"
                    "Step 2 (SELECT): Use ls to discover available drafts, "
                    "then read_file only the ones needed for merging.\\n\\n"
                    "Step 3 (COMPRESS): Each sub-agent outputs compact JSON with fields: "
                    "{{'agent': name, 'files_scanned': N, 'issues': [...], 'summary': '...'}}\\n\\n"
                    "Step 4 (ISOLATE): Confirm architect did NOT see security content and vice versa.\\n\\n"
                    "Write a summary to /workspace/context_engineering_report.md explaining "
                    "how each pillar was demonstrated."
                ),
            }
        ]
    })

    print(f"\n✅ Demo 05 complete!")
    print("Check demo/workspace_05/ for:")
    print("  - drafts/architect.json, drafts/security-analyst.json (Write + Compress)")
    print("  - context_engineering_report.md (Select + Isolate)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/05_context_engineering.py
```

- [ ] **Step 3: Verify all output files**

```bash
ls demo/workspace_05/drafts/ demo/workspace_05/context_engineering_report.md
```

- [ ] **Step 4: Commit**

```bash
git add demo/05_context_engineering.py
git commit -m "feat: demo 05 — context engineering (Write/Select/Compress/Isolate)"
```

---

### Task 6: Docker Sandbox + HITL Approval

**Goal:** Integrate Docker sandbox for safe code execution, implement `CompositeBackend` path routing, and demonstrate Human-in-the-Loop approval for sensitive operations.

**Dependencies:** Tasks 2, 5 (code health pipeline + context engineering ready)

**Files:**
- Create: `demo/06_sandbox_hitl.py`

**Interfaces:**
- Consumes: Code health pipeline from Task 2, CompositeBackend from Task 5
- Produces: Verified sandbox + HITL pattern reusable in Phase 2

---

- [ ] **Step 1: Write `demo/06_sandbox_hitl.py`**

```python
"""
Demo 06: Docker Sandbox + HITL (Human-in-the-Loop) Approval.
Demonstrates:
  - DockerSandbox: execute analysis tools in isolated container
  - CompositeBackend: route /workspace/*/code/ → sandbox, /workspace/*/drafts/ → local FS
  - HITL: pause before sensitive operations, require user approval
"""
import json
import os
import subprocess
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend, StateBackend
from deepagents.backends.sandbox import DockerSandbox
from deepagents.backends.protocol import SandboxBackendProtocol
from langchain.chat_models import init_chat_model


def check_docker():
    """Verify Docker is available."""
    try:
        result = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception as e:
        return None
    return None


def main():
    docker_version = check_docker()
    if not docker_version:
        print("⚠️  Docker not available. Running in simulation mode.")
        print("   Install Docker Desktop: https://docs.docker.com/desktop/setup/install/windows-install/")
        print()
        use_sandbox = False
    else:
        print(f"✅ Docker {docker_version} detected")
        use_sandbox = True

    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    # Build CompositeBackend with sandbox routing
    if use_sandbox:
        sandbox = DockerSandbox(
            image="python:3.10-slim",
            default_kwargs={
                "network_mode": "none",  # No network access inside sandbox
                "mem_limit": "512m",
            },
        )
        backend = CompositeBackend(
            default=FilesystemBackend(root_dir="demo/workspace_06"),
            routes={
                r"/workspace/.*/code/": sandbox,
                r"/temp/": StateBackend(),
            },
        )
    else:
        # Fallback: use local FS (for development without Docker)
        backend = FilesystemBackend(root_dir="demo/workspace_06")

    security_skill_path = "demo/04_skills/code-health/code-security/SKILL.md"
    if os.path.exists(security_skill_path):
        with open(security_skill_path, "r", encoding="utf-8") as f:
            security_skill = f.read()
    else:
        security_skill = "You are a security analyst."

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=f"""You are a security analysis agent operating within a sandbox.

IMPORTANT SECURITY RULES:
1. All code execution happens in the Docker sandbox (path: /workspace/*/code/)
2. You MUST ask for Human-in-the-Loop (HITL) approval before ANY write operation
3. The code directory is READ-ONLY — you cannot modify source files
4. If you want to suggest a fix, write it to /workspace/drafts/suggested_fix.patch
5. Sensitive files (.env, .git/) are not accessible

Sandbox status: {'ACTIVE' if use_sandbox else 'SIMULATED (Docker not available)'}

When you need to execute a command, use the sandbox. When you want to modify anything,
first explain what you want to do and wait for approval — this is the HITL pattern.""",
        system_prompt_scope="always",
    )

    print("=" * 60)
    print("Demo 06: Docker Sandbox + HITL")
    print(f"Sandbox: {'ACTIVE' if use_sandbox else 'SIMULATED'}")
    print("=" * 60)

    # Create a small test code file to scan
    test_code_dir = "demo/workspace_06/code"
    os.makedirs(test_code_dir, exist_ok=True)
    with open(f"{test_code_dir}/test_app.py", "w", encoding="utf-8") as f:
        f.write("""\
# Test application with intentional security issues
import os
import pickle

DB_PASSWORD = "admin123"  # Hardcoded secret

def get_user(user_id):
    query = "SELECT * FROM users WHERE id = " + user_id  # SQL injection
    return query

def load_data(filename):
    with open(filename, 'rb') as f:
        return pickle.load(f)  # Insecure deserialization
""")

    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Analyze the code in /workspace/code/test_app.py for security vulnerabilities.\\n\\n"
                    f"1. If Docker sandbox is active, run bandit or a manual review\\n"
                    f"2. Identify all security issues with severity ratings\\n"
                    f"3. For each issue you want to fix, follow HITL: "
                    f"describe the fix and save it to /workspace/drafts/suggested_fixes.md "
                    f"(DO NOT modify the original code directly)\\n"
                    f"4. Explain how the sandbox protects the host system"
                ),
            }
        ]
    })

    print(f"\n✅ Demo 06 complete!")
    print(f"   Sandbox mode: {'Docker' if use_sandbox else 'simulated'}")
    print(f"   Draft fixes: demo/workspace_06/drafts/suggested_fixes.md")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/06_sandbox_hitl.py
```

If Docker is available: agent runs security scan in isolated container.
If Docker is not available: runs in simulation mode with local FS fallback.

- [ ] **Step 3: Commit**

```bash
git add demo/06_sandbox_hitl.py
git commit -m "feat: demo 06 — Docker sandbox + HITL approval pattern"
```

---

### Task 7: Middleware & LangSmith Observability

**Goal:** Implement `PerformanceMiddleware` and `AuditMiddleware`, integrate LangSmith tracing, verify all three layers of observability.

**Dependencies:** Tasks 2, 4 (code health pipeline + skills)

**Files:**
- Create: `demo/07_middleware_langsmith.py`

**Interfaces:**
- Consumes: Code health pipeline from Task 2
- Produces: Reusable middleware classes, LangSmith project configuration

---

- [ ] **Step 1: Write `demo/07_middleware_langsmith.py`**

```python
"""
Demo 07: Middleware & LangSmith Observability.
Implements:
  1. PerformanceMiddleware — tracks model calls, tool calls, token usage, timing
  2. AuditMiddleware — logs sensitive operations (file writes, sandbox commands)
  3. LangSmith tracing — full trace of agent execution
"""
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model


# ---- Custom Middleware ----

@dataclass
class PerformanceMetrics:
    """Accumulates performance data across the run."""
    model_calls: int = 0
    model_total_time_ms: float = 0.0
    tool_calls: int = 0
    tool_total_time_ms: float = 0.0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    errors: int = 0
    events: list = field(default_factory=list)

    def record_model_call(self, duration_ms: float, tokens_in: int, tokens_out: int):
        self.model_calls += 1
        self.model_total_time_ms += duration_ms
        self.total_tokens_in += tokens_in
        self.total_tokens_out += tokens_out
        self.events.append({
            "type": "model_call",
            "duration_ms": round(duration_ms, 1),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
        })

    def record_tool_call(self, tool_name: str, duration_ms: float):
        self.tool_calls += 1
        self.tool_total_time_ms += duration_ms
        self.events.append({
            "type": "tool_call",
            "tool": tool_name,
            "duration_ms": round(duration_ms, 1),
        })

    def record_error(self, error: str):
        self.errors += 1
        self.events.append({"type": "error", "error": error})

    def summary(self) -> dict:
        return {
            "model_calls": self.model_calls,
            "model_avg_time_ms": round(self.model_total_time_ms / max(1, self.model_calls), 1),
            "tool_calls": self.tool_calls,
            "tool_avg_time_ms": round(self.tool_total_time_ms / max(1, self.tool_calls), 1),
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "total_tokens": self.total_tokens_in + self.total_tokens_out,
            "errors": self.errors,
            "events": self.events,
        }


class PerformanceMiddleware:
    """Middleware that wraps model and tool calls with timing and token tracking."""

    def __init__(self, metrics: PerformanceMetrics):
        self.metrics = metrics

    def wrap_model_call(self, call, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            # Extract token counts from result (implementation depends on model)
            tokens_in = getattr(result, "usage_metadata", {}).get("input_tokens", 0) if hasattr(result, "usage_metadata") else 0
            tokens_out = getattr(result, "usage_metadata", {}).get("output_tokens", 0) if hasattr(result, "usage_metadata") else 0
            self.metrics.record_model_call(duration_ms, tokens_in, tokens_out)
            return result
        except Exception as e:
            self.metrics.record_error(str(e))
            raise

    def wrap_tool_call(self, call, tool_name, *args, **kwargs):
        start = time.perf_counter()
        try:
            result = call(*args, **kwargs)
            duration_ms = (time.perf_counter() - start) * 1000
            self.metrics.record_tool_call(tool_name, duration_ms)
            return result
        except Exception as e:
            self.metrics.record_error(str(e))
            raise


class AuditMiddleware:
    """Logs sensitive operations for security audit trail."""

    def __init__(self, log_path: str):
        self.log_path = log_path
        self.entries = []

    def log(self, action: str, detail: str, approved: bool = None):
        entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "action": action,
            "detail": detail,
            "approved": approved,
        }
        self.entries.append(entry)

    def save(self):
        with open(self.log_path, "w", encoding="utf-8") as f:
            json.dump(self.entries, f, indent=2, ensure_ascii=False)
        print(f"  Audit log saved: {self.log_path} ({len(self.entries)} entries)")


# ---- Main Demo ----

def main():
    # LangSmith setup
    langsmith_available = bool(os.environ.get("LANGCHAIN_API_KEY"))
    if langsmith_available:
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", "aperio")
        print("✅ LangSmith tracing enabled (project: aperio)")
        # Note: actual tracing is automatic when LANGCHAIN_TRACING_V2=true
    else:
        print("ℹ️  LangSmith not configured (set LANGCHAIN_API_KEY to enable)")

    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    backend = FilesystemBackend(root_dir="demo/workspace_07")

    # Initialize custom middleware
    metrics = PerformanceMetrics()
    perf_middleware = PerformanceMiddleware(metrics)
    audit = AuditMiddleware("demo/workspace_07/audit_log.json")

    agent = create_deep_agent(
        model=model,
        backend=backend,
        middleware=[perf_middleware],
        system_prompt="You are a helpful assistant. Complete tasks step by step.",
    )

    print("=" * 60)
    print("Demo 07: Middleware & LangSmith Observability")
    print(f"LangSmith: {'ENABLED' if langsmith_available else 'NOT CONFIGURED'}")
    print("=" * 60)

    # Run a task that exercises model calls and tool calls
    result = agent.invoke({
        "messages": [
            {
                "role": "user",
                "content": (
                    "Do the following steps:\n"
                    "1. Write a short poem about code quality (3 lines) to /workspace/poem.md\n"
                    "2. Read the poem back\n"
                    "3. Summarize what you wrote\n"
                ),
            }
        ]
    })

    # --- Output Performance Report ---
    summary = metrics.summary()
    print(f"\n{'='*40}")
    print("📊 Performance Report")
    print(f"{'='*40}")
    print(f"  Model Calls:      {summary['model_calls']}")
    print(f"  Avg Model Time:   {summary['model_avg_time_ms']}ms")
    print(f"  Tool Calls:       {summary['tool_calls']}")
    print(f"  Avg Tool Time:    {summary['tool_avg_time_ms']}ms")
    print(f"  Total Tokens:     {summary['total_tokens']}")
    print(f"  Errors:           {summary['errors']}")

    # Save performance report
    report_path = "demo/workspace_07/performance_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\n  Report saved: {report_path}")

    # Save audit log
    audit.log("demo_run", f"Demo 07 completed: {summary['model_calls']} model calls, {summary['tool_calls']} tool calls")
    audit.save()

    print(f"\n✅ Demo 07 complete!")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/07_middleware_langsmith.py
```

- [ ] **Step 3: Verify output files**

```bash
ls demo/workspace_07/performance_report.json demo/workspace_07/audit_log.json demo/workspace_07/poem.md
```

- [ ] **Step 4: Commit**

```bash
git add demo/07_middleware_langsmith.py
git commit -m "feat: demo 07 — PerformanceMiddleware + AuditMiddleware + LangSmith"
```

---

### Task 8: Long-Term Memory — StoreBackend + Cross-Thread

**Goal:** Implement `StoreBackend` with `/memories/` path for persistent storage, demonstrate cross-thread memory sharing (different threads read/write same memory), and trend tracking across multiple "sessions."

**Dependencies:** Tasks 2, 5 (code health pipeline + context engineering)

**Files:**
- Create: `demo/08_longterm_memory.py`

**Interfaces:**
- Consumes: Report format from Task 2, CompositeBackend from Task 5
- Produces: Verified StoreBackend pattern for Phase 2

---

- [ ] **Step 1: Write `demo/08_longterm_memory.py`**

```python
"""
Demo 08: Long-Term Memory — StoreBackend + Cross-Thread.
Demonstrates:
  - /memories/ path for persistent user preferences and history
  - Cross-thread memory: thread A writes, thread B reads
  - Trend tracking: compare current results with historical data
"""
import json
import os
import uuid
from datetime import datetime
from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend
from deepagents.backends.store import StoreBackend
from langgraph.store.memory import InMemoryStore
from langchain.chat_models import init_chat_model


def main():
    model = init_chat_model(
        model="deepseek-v4-flash",
        api_key=os.environ.get("DEEPSEEK_API_KEY", "sk-xxx"),
        api_base="https://api.deepseek.com",
    )

    # Memory store (InMemoryStore for dev, replace with Redis/Postgres for prod)
    store = InMemoryStore()

    # CompositeBackend with /memories/ → StoreBackend
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir="demo/workspace_08"),
        routes={
            r"/memories/": StoreBackend(store=store, namespace="aperio"),
        },
    )

    system_prompt = """You are an agent with long-term memory.
- Write user preferences to /memories/preferences/<user_id>_<key>.json
- Write task history to /memories/history/<user_id>_<type>.json
- Read /memories/ to recall past interactions and preferences
- When generating reports, compare with historical data if available"""

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=system_prompt,
    )

    print("=" * 60)
    print("Demo 08: Long-Term Memory — StoreBackend + Cross-Thread")
    print("=" * 60)

    user_id = "demo_user"

    # ---- Thread 1: Write Preferences ----
    print("\n--- Thread 1: Writing user preferences ---")
    result1 = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"My user ID is '{user_id}'. Please save my preferences to memory:\\n"
                        f"1. Tech stack: Python, FastAPI, React\\n"
                        f"2. Risk threshold: High (I only care about Critical and High severity issues)\\n"
                        f"3. PRD template: Standard with Acceptance Criteria focus\\n\\n"
                        f"Write each to /memories/preferences/{user_id}_<key>.json"
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "thread_a"}},
    )

    # ---- Thread 2: Simulate Code Health Scan (stores history) ----
    print("\n--- Thread 2: Running code health scan (stores history) ---")
    result2 = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Simulate a code health scan for user '{user_id}'.\\n"
                        f"Results to store in history:\\n"
                        f"- Scan date: {datetime.now().isoformat()}\\n"
                        f"- Files scanned: 47\\n"
                        f"- Issues found: 9 (2 Critical, 3 High, 3 Medium, 1 Low)\\n"
                        f"- Health score: 72/100\\n\\n"
                        f"Write this to /memories/history/{user_id}_scans.json\\n"
                        f"Also write project context: 'This is an e-commerce backend' "
                        f"to /memories/context/proj_001_context.json"
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "thread_b"}},
    )

    # ---- Thread 3: New session — Read preferences and compare ----
    print("\n--- Thread 3: New session — Reading preferences + trend comparison ---")
    result3 = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"I'm back! My user ID is '{user_id}'.\\n"
                        f"1. Read my preferences from /memories/preferences/\\n"
                        f"2. Read my previous scan history from /memories/history/\\n"
                        f"3. Now simulate a NEW scan with these results:\\n"
                        f"   - 5 issues (0 Critical, 1 High, 3 Medium, 1 Low)\\n"
                        f"   - Health score: 85/100\\n"
                        f"4. Compare with previous scan and write a trend report to /workspace/trend_report.md\\n"
                        f"   showing: '5 issues resolved since last scan, 1 new issue found'\\n"
                        f"5. Apply my risk threshold preference (only show Critical and High severity)"
                    ),
                }
            ]
        },
        config={"configurable": {"thread_id": "thread_c"}},
    )

    print(f"\n✅ Demo 08 complete!")
    print(f"   Check demo/workspace_08/trend_report.md for cross-thread comparison")
    print(f"   Threads used: A (write prefs), B (write history), C (read + compare)")
    print(f"   This demonstrates: persistence across threads, trend tracking, preference application")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the demo**

```bash
python demo/08_longterm_memory.py
```

Expected: Three threads (A, B, C) share persistent memory. Thread C reads preferences and history written by A and B, then generates a trend report.

- [ ] **Step 3: Verify outputs**

```bash
ls demo/workspace_08/trend_report.md
```

- [ ] **Step 4: Commit**

```bash
git add demo/08_longterm_memory.py
git commit -m "feat: demo 08 — long-term memory with StoreBackend + cross-thread sharing"
```

---

## Phase 1 Complete — Self-Check

Before moving to Phase 2, verify:
- [ ] All 8 demos run successfully
- [ ] `git log --oneline` shows clean commit history for all 8 demos
- [ ] All SKILL.md files pass `python demo/04_skills/test_skills.py`

---

## Phase 2 & 3: FastAPI Integration + Polish

> **Note:** Phases 2 and 3 are summarized as task outlines. Full step-by-step code will be written after Phase 1 is complete and verified, since the exact integration patterns depend on how the demo implementations evolve.

### Phase 2 Tasks (FastAPI Template Integration)

**Task 9: Backend — New Data Models + Alembic Migration**
- Modify: `full-stack-fastapi-template-master/backend/app/models.py` — add `ScanTask` and `ReviewTask` models (see design.md §9)
- Create: `full-stack-fastapi-template-master/backend/app/alembic/versions/xxxx_add_scan_review_tasks.py` — auto-generated migration
- Run: `alembic revision --autogenerate -m "add scan and review tasks"` then `alembic upgrade head`
- Commit

**Task 10: Backend — Agent Service Layer**
- Create: `full-stack-fastapi-template-master/backend/app/services/__init__.py`
- Create: `full-stack-fastapi-template-master/backend/app/services/agent_service.py` — wraps DeepAgents calls, uses demo-verified patterns
- Functions: `run_code_health_scan(task_id, repo_url)` and `run_prd_review(task_id, requirement)`
- Create: `full-stack-fastapi-template-master/backend/app/services/skill_loader.py` — loads skills from `skills/` directory
- Commit

**Task 11: Backend — New API Routes + WebSocket**
- Create: `full-stack-fastapi-template-master/backend/app/api/routes/code_health.py` — CRUD endpoints for scan tasks
- Create: `full-stack-fastapi-template-master/backend/app/api/routes/prd_review.py` — CRUD endpoints for review tasks
- Create: `full-stack-fastapi-template-master/backend/app/api/routes/ws.py` — WebSocket for real-time agent output
- Modify: `full-stack-fastapi-template-master/backend/app/api/main.py` — include new routers
- Commit

**Task 12: Frontend — New Pages**
- Create: `full-stack-fastapi-template-master/frontend/src/routes/_layout/code-health.tsx`
- Create: `full-stack-fastapi-template-master/frontend/src/routes/_layout/prd-review.tsx`
- Create: `full-stack-fastapi-template-master/frontend/src/components/CodeHealth/` — input form, progress bar, report view, radar chart
- Create: `full-stack-fastapi-template-master/frontend/src/components/PRDReview/` — input form, progress bar, split-pane report view
- Modify: `full-stack-fastapi-template-master/frontend/src/routes/_layout/index.tsx` — Dashboard with metrics cards
- Commit

**Task 13: Integration — Copy Skills + Wire Everything**
- Copy: `demo/04_skills/` → `full-stack-fastapi-template-master/skills/`
- Configure: env vars for API keys, model selection, LangSmith
- Test: full flow — login → submit task → see real-time progress → view report
- Commit

**Task 14: Docker Compose — One-Click Deploy**
- Modify: `compose.yml` — add sandbox service
- Create: `Dockerfile.sandbox` — custom image with bandit, semgrep pre-installed
- Test: `docker compose up` launches everything
- Commit

### Phase 3 Tasks (Polish & Delivery)

**Task 15: Tests**
- Add: `tests/api/routes/test_code_health.py` — integration tests for scan endpoints
- Add: `tests/api/routes/test_prd_review.py` — integration tests for review endpoints
- Add: `tests/services/test_agent_service.py` — unit tests with mocked agents
- Run: `coverage run -m pytest && coverage report`

**Task 16: UI Polish & Report Export**
- Loading skeletons, error states, empty states for all pages
- Markdown report download button
- CSV export for review matrix

**Task 17: Final Report & Demo Materials**
- Write 最终报告 (Word/PDF) covering all sections per assignment requirements
- Record ≤5 min demo video
- Capture ≥5 key screenshots
- Final git push

---

> 📅 Created 2025-06-25 | Status: Phase 1 ready to execute | Next: Begin Task 1
