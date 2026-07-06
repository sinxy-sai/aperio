from __future__ import annotations

import shutil
from importlib.resources import files
from pathlib import Path


def packaged_skills_root() -> Path:
    """Return the package-local skills resource directory."""
    return Path(str(files("aperio_agent_backend") / "skill-assets"))


def copy_packaged_skills(run_root: Path) -> Path:
    """Expose packaged skills inside a DeepAgents virtual workspace.

    DeepAgents loads skills through its configured backend. The backend used by
    the web/CLI runner is rooted at the per-run workspace, so packaged resources
    are copied into that workspace before an agent starts.
    """
    source = packaged_skills_root()
    target = run_root / "skills"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target
