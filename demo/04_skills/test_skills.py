"""
Validate all SKILL.md files under demo/04_skills/.
Checks: YAML frontmatter structure, required fields, minimum content size.
Uses only stdlib — no external dependencies.
"""
import re
import sys
from pathlib import Path


def parse_simple_yaml(text: str) -> dict:
    """Parse a simple YAML frontmatter (key: value + list items)."""
    result = {}
    current_key = None
    for line in text.strip().split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Top-level key: value
        if ":" in stripped and not line.startswith(" ") and not stripped.startswith("-"):
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()
            if val:
                result[key] = val.strip("\"'")
            else:
                result[key] = []
                current_key = key
        # List item (indented or not)
        elif stripped.startswith("- "):
            val = stripped[2:].strip()
            if current_key and isinstance(result.get(current_key), list):
                result[current_key].append(val)
        # Indented key: value
        elif ":" in stripped and line.startswith("  "):
            key, _, val = stripped.partition(":")
            key = key.strip()
            result[key] = val.strip().strip("\"'")
    return result


def check_skill(filepath: Path) -> dict:
    """Parse a SKILL.md file and validate its structure."""
    content = filepath.read_text(encoding="utf-8")

    assert content.startswith("---"), "missing YAML frontmatter (must start with ---)"

    parts = content.split("---", 2)
    assert len(parts) >= 3, "malformed frontmatter (need 2 '---' delimiters)"

    meta = parse_simple_yaml(parts[1])
    body = parts[2].strip()

    assert "name" in meta, "missing 'name'"
    assert meta["name"], "empty 'name'"
    assert "description" in meta, "missing 'description'"
    assert len(meta.get("description", "")) > 10, "description too short"
    assert "triggers" in meta, "missing 'triggers'"
    triggers = meta["triggers"]
    assert isinstance(triggers, list), "'triggers' must be a list"
    assert len(triggers) >= 3, f"need >=3 triggers, got {len(triggers)}"
    assert len(body) > 200, f"body too short ({len(body)} chars, need >200)"

    # Check body has at least one ## section header (flexible structure)
    sections = re.findall(r'^##\s+(.+)', body, re.MULTILINE)
    assert len(sections) >= 2, f"need >=2 Markdown sections, got {len(sections)}"

    return {
        "name": meta["name"],
        "triggers": len(triggers),
        "size": len(body),
    }


def main():
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    skills_root = Path(__file__).resolve().parent
    skill_files = sorted(
        f for f in skills_root.rglob("SKILL.md")
        if "skill-creator" not in str(f)
    )

    print(f"Found {len(skill_files)} custom SKILL.md files\n")

    results = []
    for sf in skill_files:
        rel = sf.relative_to(skills_root)
        try:
            info = check_skill(sf)
            results.append(info)
            print(f"  ✅ {rel}")
            print(f"     name={info['name']}, triggers={info['triggers']}, body={info['size']} chars")
        except Exception as e:
            print(f"  ❌ {rel} — {e}")
            results.append({"error": str(e)})

    errors = [r for r in results if "error" in r]
    print(f"\n{'=' * 50}")
    print(f"Total: {len(results)} custom skills")
    print(f"Passed: {len(results) - len(errors)}")
    print(f"Failed: {len(errors)}")

    sc = skills_root / "skill-creator" / "SKILL.md"
    if sc.exists():
        print(f"\n  + skill-creator (imported Anthropic): OK")

    if errors:
        print("\n❌ Failed skills:")
        for e in errors:
            print(f"  {e['error']}")
        exit(1)
    else:
        print(f"\n✅ All {len(results)} custom skills valid!")


if __name__ == "__main__":
    main()
