"""
Demo 04: Generate all Aperio skills using skill-creator.
Reads the Anthropic skill-creator methodology, then generates all 11 SKILL.md files
in a single agent session for consistent quality.
"""
import os
import sys
from pathlib import Path

_DEMO_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _DEMO_DIR.parent

from dotenv import load_dotenv
load_dotenv(_DEMO_DIR / ".env")

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend
from langchain.chat_models import init_chat_model

SKILLS_DIR = _DEMO_DIR / "04_skills"

# Create all directories before agent runs
for category, subdirs in [
    ("general", ["report-writing", "review-matrix", "tool-usage"]),
    ("code-health", ["code-architect", "code-security", "code-dependency", "code-documentation"]),
    ("prd-review", ["review-tech", "review-ux", "review-test", "review-ops"]),
]:
    for sub in subdirs:
        (SKILLS_DIR / category / sub).mkdir(parents=True, exist_ok=True)

SKILL_LIST = """
| 路径 | 角色 | 用途摘要 |
|------|------|---------|
| general/report-writing | 技术报告专家 | 统一报告格式：健康报告(评分+风险表+详情+趋势+建议)、PRD(背景+用户故事+范围+流程+验收+非功能) |
| general/review-matrix | 评审矩阵专家 | 整合多角色反馈为标准矩阵：维度/严重度(Critical/High/Medium/Low)/描述/建议/状态(Accepted/Rejected) |
| general/tool-usage | 工具规范专家 | 文件I/O安全规则(敏感文件不可读)、沙盒命令(30s超时)、重试策略(指数退避3次)、输出清理 |
| code-health/code-architect | 软件架构师 | 分析目录结构、模块耦合、分层清晰度、命名规范、反模式(God Class/循环依赖) |
| code-health/code-security | 应用安全工程师 | 审计SQL注入/XSS/硬编码密钥/不安全反序列化/路径遍历，沙盒中执行bandit+semgrep并去误报 |
| code-health/code-dependency | 依赖管理专家 | 检查版本落后、已知CVE、许可证兼容性、未声明传递依赖、未使用依赖 |
| code-health/code-documentation | 技术文档专家 | 评估README完整性、API docstring覆盖、注释质量、配置文档、贡献指南 |
| prd-review/review-tech | 技术Lead/架构师 | 评审技术可行性、架构影响、API设计、安全隐患、性能预期、新依赖 |
| prd-review/review-ux | UX设计师 | 评审操作路径、异常状态覆盖、交互一致性、无障碍、信息架构 |
| prd-review/review-test | QA工程师 | 评审可测试性、边界条件、性能指标、回归风险、测试策略 |
| prd-review/review-ops | 产品运营经理 | 评审商业价值、上线策略、风险评估、成功指标、竞品对比、干系人影响 |
"""


def main():
    print("=" * 60)
    print("Demo 04: Generate Skills using skill-creator")
    print("=" * 60)

    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

    api_key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        print("ERROR: DEEPSEEK_API_KEY not found in demo/.env")
        return

    model = init_chat_model(
        model="openai:deepseek-v4-flash",
        api_key=api_key,
        base_url="https://api.deepseek.com",
    )

    agent = create_deep_agent(
        model=model,
        backend=FilesystemBackend(root_dir=str(_PROJECT_ROOT), virtual_mode=False),
        system_prompt="""You are a skill creation expert. You have access to the skill-creator
at demo/04_skills/skill-creator/SKILL.md — use its methodology to guide your work.

Your task is to generate ALL 11 SKILL.md files listed below. Write each file to its
exact path using write_file. Each file must be a complete, production-quality skill
following this exact format:

```markdown
---
name: <kebab-case-name>
description: <one-line Chinese description of what this skill does>
triggers:
  - <Chinese trigger keyword 1>
  - <Chinese trigger keyword 2>
  - <Chinese trigger keyword 3>
---

## 角色定义
<2-3 sentences defining the role, in Chinese>

## 工作流程
1. <step 1 in Chinese>
2. <step 2 in Chinese>
...

## 检查清单
- [ ] <checklist item in Chinese>
- [ ] ...

## 输出格式
| 序号 | <column> | <column> | <column> | <column> |
|------|---------|---------|---------|---------|
```

Rules:
- name must be kebab-case English
- description must be one line in Chinese
- triggers: at least 3, in Chinese
- 角色定义, 工作流程, 检查清单 (5+ items), 输出格式 (Markdown table) — all in Chinese
- Each skill must be self-contained and useful to its target agent role""",
    )

    task = f"""Read demo/04_skills/skill-creator/SKILL.md to understand the skill creation methodology.

Then create ALL 11 skill files listed below. Write each one using write_file.

{SKILL_LIST}

For each skill, create a full SKILL.md at demo/04_skills/<path>/SKILL.md.

The directories already exist. Write all 11 files now."""

    print("Generating 11 skills in one session (this may take 5-10 minutes)...\n")

    try:
        result = agent.invoke({
            "messages": [{"role": "user", "content": task}],
        })
        final = result["messages"][-1].content if result.get("messages") else "(no response)"
        print(f"\nAgent response:\n{final[:500]}...")
    except Exception as exc:
        print(f"ERROR: {exc}")
        return

    # Verify
    print(f"\n{'=' * 60}")
    count = 0
    for sf in sorted(SKILLS_DIR.rglob("SKILL.md")):
        if "skill-creator" not in str(sf):
            size = sf.stat().st_size
            print(f"  {'✅' if size > 200 else '⚠️'} {sf.relative_to(SKILLS_DIR)} ({size} bytes)")
            count += 1
    print(f"\n  Total custom skills: {count} (expected: 11)")
    print(f"  Plus skill-creator (imported) at demo/04_skills/skill-creator/")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
