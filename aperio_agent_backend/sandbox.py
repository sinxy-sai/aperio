from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from importlib.resources import files

from .config import get_sandbox_image
from .resources import packaged_skills_root


def run_code_health_scan_in_docker(
    project_root: Path,
    target_rel: str,
    out_path: Path,
    *,
    timeout_seconds: int = 300,
    install_project_deps: bool = False,
) -> dict[str, Any]:
    """Run code-health-toolkit inside the packaged Docker sandbox.

    The host project is mounted read-only. This prevents the scanner from
    mutating source files, but also means dependency installation is deliberately
    disabled in Docker mode.
    """
    if install_project_deps:
        install_note = "ignored in Docker sandbox because the project mount is read-only"
    else:
        install_note = ""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    image = get_sandbox_image()
    _ensure_image(image)

    outputs_dir = out_path.parent.resolve()
    docker_out = "/outputs/tool_results.json"
    command = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{project_root.resolve()}:/workspace/project:ro",
        "-v",
        f"{packaged_skills_root().resolve()}:/skills:ro",
        "-v",
        f"{outputs_dir}:/outputs",
        image,
        "python",
        "/skills/code-health/code-health-toolkit/scripts/run_checks.py",
        "--project-root",
        "/workspace/project",
        "--target",
        target_rel or ".",
        "--out",
        docker_out,
        "--summary",
    ]
    completed = subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=max(30, timeout_seconds),
        check=False,
    )
    if not out_path.exists():
        raise RuntimeError(f"Docker scanner did not produce {out_path}: {(completed.stdout or '')[-2000:]}")

    data = json.loads(out_path.read_text(encoding="utf-8"))
    data.setdefault("backend_invocation", {})
    data["backend_invocation"].update(
        {
            "scanner_mode": "docker",
            "docker_image": image,
            "exit_code": completed.returncode,
            "summary_output": (completed.stdout or "")[-12000:],
            "install_project_deps": False,
            "install_project_deps_note": install_note,
            "invoked_by": "aperio_agent_backend.sandbox",
        }
    )
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def _ensure_image(image: str) -> None:
    inspect = subprocess.run(
        ["docker", "image", "inspect", image],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if inspect.returncode == 0:
        return

    dockerfile = Path(str(files("aperio_agent_backend") / "sandbox-assets" / "Dockerfile"))
    if not dockerfile.exists():
        raise RuntimeError(f"packaged Dockerfile not found: {dockerfile}")
    build = subprocess.run(
        ["docker", "build", "-t", image, "-f", str(dockerfile), str(dockerfile.parent)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if build.returncode != 0:
        raise RuntimeError(f"failed to build Docker image {image}: {(build.stdout or '')[-4000:]}")
