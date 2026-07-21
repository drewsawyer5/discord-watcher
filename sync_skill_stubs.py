"""Regenerate ~/.claude/commands/ stubs from the vault skills folder.

The vault (Obsidian/7 - MD-AI/03 - Skills/) is the single source of truth for
skills; stubs are build artifacts and must never be hand-edited (see AI Brain
CLAUDE.md, Skills section). Run at SessionStart. Idempotent: a second run is a
no-op.

A skill is either a flat ``<name>.md`` file or a ``<name>/`` folder containing
``SKILL.md``. Names in SKIP are programmatic prompts (invoked by code, not by
the Skill tool) and get no stub. Orphan stubs are deleted only if they contain
the generated marker, so a hand-written command file is left alone (with a
warning) rather than destroyed.
"""

import sys
from pathlib import Path

SKILLS_DIR = Path(r"C:\Users\drews\Life Org\Obsidian\7 - MD-AI\03 - Skills")
COMMANDS_DIR = Path.home() / ".claude" / "commands"
SKIP = {"ingest-auto"}
MARKER = "Execute the /"

STUB_TEMPLATE = """Execute the /{name} skill. Full instructions are in:
`{target}`

Read that file first, then follow the skill steps.
"""


def discover_skills() -> dict:
    skills = {}
    for entry in sorted(SKILLS_DIR.iterdir()):
        if entry.name.startswith((".", "_")):
            continue
        if entry.is_file() and entry.suffix == ".md":
            name, target = entry.stem, entry
        elif entry.is_dir() and (entry / "SKILL.md").is_file():
            name, target = entry.name, entry / "SKILL.md"
        else:
            print(f"  warn: ignoring {entry.name} (not a .md skill or SKILL.md folder)")
            continue
        if " " in name:
            print(f"  warn: skipping '{name}' — skill names must not contain spaces")
            continue
        if name in SKIP:
            continue
        skills[name] = target
    return skills


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"error: skills dir not found: {SKILLS_DIR}")
        return 1
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)

    skills = discover_skills()
    created, updated, deleted, unchanged = [], [], [], []

    for name, target in skills.items():
        stub_path = COMMANDS_DIR / f"{name}.md"
        content = STUB_TEMPLATE.format(name=name, target=target)
        if not stub_path.exists():
            stub_path.write_text(content, encoding="utf-8")
            created.append(name)
        elif stub_path.read_text(encoding="utf-8") != content:
            stub_path.write_text(content, encoding="utf-8")
            updated.append(name)
        else:
            unchanged.append(name)

    for stub_path in sorted(COMMANDS_DIR.glob("*.md")):
        if stub_path.stem in skills:
            continue
        if MARKER in stub_path.read_text(encoding="utf-8"):
            stub_path.unlink()
            deleted.append(stub_path.stem)
        else:
            print(f"  warn: orphan {stub_path.name} is not a generated stub — left in place")

    print(
        f"sync_skill_stubs: {len(skills)} skills | "
        f"created {len(created)}{': ' + ', '.join(created) if created else ''} | "
        f"updated {len(updated)}{': ' + ', '.join(updated) if updated else ''} | "
        f"deleted {len(deleted)}{': ' + ', '.join(deleted) if deleted else ''} | "
        f"unchanged {len(unchanged)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
