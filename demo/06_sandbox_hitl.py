"""
Demo 06: Docker Sandbox + HITL (Human-in-the-Loop) Approval.
Based on Exercise 12 patterns: DockerSandbox for isolated code execution,
CompositeBackend for path routing, and LangGraph Command interrupt for HITL.
"""
import os
import sys
import subprocess
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend, CompositeBackend, StateBackend
from langchain.chat_models import init_chat_model

WORKSPACE = str((_PROJECT_ROOT / "demo/workspace_06").resolve())


def _check_docker() -> bool:
    """Check if Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


def _load_skill(name: str) -> str:
    path = _DEMO_DIR / "04_skills" / name / "SKILL.md"
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def main():
    print("=" * 60)
    print("Demo 06: Docker Sandbox + HITL Approval")
    print("=" * 60)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    docker_ok = _check_docker()
    print(f"Docker: {'✅ available' if docker_ok else '⚠️  not available (simulation mode)'}")

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found")
        return

    model = _build_model(api_key)

    # CompositeBackend with sandbox routing
    # When Docker is available, /workspace/*/code/ routes to sandbox
    # /workspace/*/drafts/ routes to local FS (safe for writing reports)
    # /temp/ routes to ephemeral StateBackend
    backend = CompositeBackend(
        default=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=True),
        routes={
            r"/temp/": StateBackend(),
        },
    )

    # Load security skill + tool-usage skill
    security_skill = _load_skill("code-health/code-security")
    tool_skill = _load_skill("general/tool-usage")

    # Create a test file with intentional security issues
    code_dir = Path(WORKSPACE) / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    (code_dir / "test_app.py").write_text(
        'import os\nimport pickle\n\n'
        'DB_PASSWORD = "admin123"  # Hardcoded secret\n\n'
        'def get_user(user_id):\n'
        '    query = "SELECT * FROM users WHERE id = " + user_id  # SQL injection\n'
        '    return query\n\n'
        'def load_data(filename):\n'
        '    with open(filename, "rb") as f:\n'
        '        return pickle.load(f)  # Insecure deserialization\n',
        encoding="utf-8",
    )

    system_prompt = f"""{security_skill}

{tool_skill}

SANDBOX STATUS: {'ACTIVE — all code execution runs in isolated Docker container' if docker_ok else 'SIMULATED — Docker not available, running with local FS'}

CRITICAL RULES:
1. All code execution for security scanning happens in the sandbox
2. The source code directory is READ-ONLY — you CANNOT modify user code
3. If you want to suggest a fix, write it to {WORKSPACE}/drafts/suggested_fixes.md
4. This is HITL (Human-in-the-Loop): any write to code directories requires approval
5. Sensitive files (.env, .git/) are inaccessible

When you find a vulnerability, explain it and suggest a fix — but DO NOT apply the fix to the original code. This is the HITL pattern: you propose, human approves."""

    agent = create_deep_agent(
        model=model,
        backend=backend,
        system_prompt=system_prompt,
    )

    result = agent.invoke({
        "messages": [{
            "role": "user",
            "content": (
                f"Analyze the code at {WORKSPACE}/code/test_app.py for security vulnerabilities.\n\n"
                f"1. Read the file\n"
                f"2. Identify ALL security issues with severity ratings (Critical/High/Medium/Low)\n"
                f"3. For each issue: explain the risk, show the vulnerable line, suggest a fix\n"
                f"4. Write findings to {WORKSPACE}/security_report.md\n"
                f"5. Write suggested fixes to {WORKSPACE}/drafts/suggested_fixes.md\n"
                f"   (DO NOT modify the original test_app.py — this is the HITL pattern)\n"
                f"6. Explain how sandbox isolation protects the host system"
            ),
        }],
    })

    final = result["messages"][-1].content if result.get("messages") else "(empty)"
    print(f"\nFinal: {final[:300]}...")
    print(f"\n✅ Demo 06 complete!")
    print(f"   Sandbox: {'Docker' if docker_ok else 'simulated'}")
    print(f"   Report: {WORKSPACE}/security_report.md")
    print(f"   Fixes: {WORKSPACE}/drafts/suggested_fixes.md")


def _build_model(api_key: str):
    return init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )


if __name__ == "__main__":
    main()
