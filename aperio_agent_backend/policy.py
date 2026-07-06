from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import get_install_project_deps, get_sandbox_image, get_scan_sandbox_mode


def write_local_policy(run_root: Path, input_bundle: dict[str, Any]) -> Path:
    """Write the per-run policy file consumed by Aperio agents."""

    local_resources = run_root / "local-resources"
    local_resources.mkdir(parents=True, exist_ok=True)

    resolved = (input_bundle.get("resolved_paths") or [{}])[0]
    target_rel = str(resolved.get("target_relative_path") or ".")
    source_path = "/workspace/project" if target_rel == "." else f"/workspace/project/{target_rel}"
    policy_path = local_resources / "aperio_policy.yaml"
    policy_path.write_text(
        f"""security:
  sandbox: {get_scan_sandbox_mode()}
  require_human_approval:
    - execute
    - write_file
  internet_search:
    mode: read_only_public_web
    approval_required: false
    evidence_rule: "web snippets are supplemental; local files and tool results remain authoritative"
storage:
  default: run_workspace
  inputs: read_only_filesystem
  outputs: filesystem
  local_resources: read_only_filesystem
output_contract:
  root_markdown_aliases: denied
  final_outputs:
    code_health:
      - /outputs/code_health/code_health_report.md
    prd_review:
      - /outputs/prd_review/prd_v2_final.md
      - /outputs/prd_review/review_matrix.md
code_health:
  project_root: /workspace/project
  source_path: {source_path}
  target_relative_path: {target_rel}
  draft_dir: /outputs/code_health/drafts
  final_report: /outputs/code_health/code_health_report.md
  toolchain:
    image: {get_sandbox_image()}
    schema: code-health-tools-v5
    install_project_deps_default: {str(get_install_project_deps()).lower()}
    raw_results: /outputs/code_health/raw/tool_results.json
    mypy_limitation: "project dependencies may be unavailable; mypy results can be partial and not CI-equivalent"
    pytest_coverage_limitation: "pytest and coverage depend on discovered tests and installed project dependencies"
prd_review:
  draft_dir: /outputs/prd_review/drafts
  prd_v1: /outputs/prd_review/prd_v1.md
  final_prd: /outputs/prd_review/prd_v2_final.md
  review_matrix: /outputs/prd_review/review_matrix.md
web_search:
  raw_evidence_dir:
    code_health: /outputs/code_health/raw/web_search
    prd_review: /outputs/prd_review/raw/web_search
  memory_policy: "do not store raw search results; only cite curated evidence in final outputs"
team: aperio-agent
""",
        encoding="utf-8",
    )
    return policy_path
