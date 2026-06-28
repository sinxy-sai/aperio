"""
Validate SKILL.md files under demo/04_skills/.

This follows the portable skill contract: frontmatter only needs `name` and
`description`; scripts, references, assets, and agents metadata are optional
bundled resources.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


NAME_RE = re.compile(r"^[a-z0-9-]+$")


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Parse simple top-level YAML frontmatter from a SKILL.md file."""
    assert text.startswith("---"), "missing YAML frontmatter"
    parts = text.split("---", 2)
    assert len(parts) >= 3, "malformed YAML frontmatter"

    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        if line.startswith((" ", "\t", "-")):
            continue
        key, _, value = stripped.partition(":")
        meta[key.strip()] = value.strip().strip("\"'")

    return meta, parts[2].strip()


def check_skill(filepath: Path) -> dict[str, object]:
    """Validate one skill package."""
    content = filepath.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(content)
    folder = filepath.parent.name

    name = meta.get("name", "")
    description = meta.get("description", "")

    assert name, "missing or empty `name`"
    assert NAME_RE.fullmatch(name), "`name` must use lowercase letters, digits, and hyphens"
    assert folder == name, f"folder name `{folder}` must match skill name `{name}`"
    assert description, "missing or empty `description`"
    assert len(description) > 20, "`description` is too short to trigger reliably"
    assert body, "missing skill body"
    assert len(body) > 100, "skill body is too short"

    return {
        "name": name,
        "description_len": len(description),
        "body_len": len(body),
    }


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    skills_root = Path(__file__).resolve().parent
    skill_files = sorted(skills_root.rglob("SKILL.md"))

    print(f"Found {len(skill_files)} SKILL.md files\n")

    failures: list[tuple[Path, str]] = []
    for skill_file in skill_files:
        rel = skill_file.relative_to(skills_root)
        try:
            info = check_skill(skill_file)
            print(
                f"  OK {rel} "
                f"name={info['name']} "
                f"description={info['description_len']} chars "
                f"body={info['body_len']} chars"
            )
        except Exception as exc:
            print(f"  FAIL {rel} - {exc}")
            failures.append((rel, str(exc)))

    print(f"\n{'=' * 50}")
    print(f"Total: {len(skill_files)} skills")
    print(f"Passed: {len(skill_files) - len(failures)}")
    print(f"Failed: {len(failures)}")

    if failures:
        print("\nFailed skills:")
        for rel, error in failures:
            print(f"  {rel}: {error}")
        raise SystemExit(1)

    print("\nAll skill packages are valid.")


if __name__ == "__main__":
    main()
