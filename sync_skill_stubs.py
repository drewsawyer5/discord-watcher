"""Regenerate ~/.claude/commands/ stubs at SessionStart (A6).

Two kinds of skill, never both for one name (life-org-projects #193, tester 2026-10-05):

* **vault-fed** — a ``<name>.md`` file (or ``<name>/SKILL.md`` folder) in the vault skills folder. The stub points at
  that file. Today's default; shrinks as skills flip.
* **SLATE-native** — no vault file; the body lives in SLATE's agent config store (kind ``skill``). The stub is the
  same one the ``nuc`` session uses ("fetch from SLATE, else the cached copy"), written through ``slate-agents``
  (``~/.claude/bin/slate-agents.cmd``), with the body cached under ``~/.claude/slate-cache/skills/``. The vault
  file's absence is the flag — nothing else to configure.

Stubs are build artifacts and must never be hand-edited. Idempotent: a second run is a no-op. Names in SKIP are
programmatic prompts (invoked by code, not by the Skill tool) and get no stub. Orphan stubs are deleted only if they
carry the generated marker, so a hand-written command file is left alone (with a warning) rather than destroyed.
SLATE unreachable: native stubs and caches are left exactly as they were (the cached copy is what the stub falls
back to), vault-fed stubs are regenerated as always.
"""

import json
import subprocess
import sys
from pathlib import Path

SKILLS_DIR = Path(r"C:\Users\drews\Life Org\Obsidian\7 - MD-AI\03 - Skills")
COMMANDS_DIR = Path.home() / ".claude" / "commands"
CACHE_DIR = Path.home() / ".claude" / "slate-cache" / "skills"
SLATE_AGENTS = Path.home() / ".claude" / "bin" / "slate-agents.cmd"
SKIP = {"ingest-auto"}
MARKER = "Execute the /"
NATIVE_MARKER = "Fetch the current instructions from SLATE first"

VAULT_STUB = """Execute the /{name} skill. Full instructions are in:
`{target}`

Read that file first, then follow the skill steps.
"""

# byte-identical to STUB_TEMPLATE / STUB_FRONTMATTER in SLATE/backend/app/agents/cli.py (render_stub), bar the wrapper path
NATIVE_STUB = """Execute the /{name} skill. Fetch the current instructions from SLATE first:
`"{cli}" get skill {name} --body`
If SLATE is unreachable, read the cached copy instead:
`{cache}`

Then follow the skill steps.
"""
# #271: the store doc's description rides on the stub as frontmatter, so the model reads *when* the skill fires.
NATIVE_FRONTMATTER = """---
description: {description}
---
"""


def render_native_stub(name: str, *, cli: Path, cache: Path, description: str | None) -> str:
    body = NATIVE_STUB.format(name=name, cli=cli, cache=cache.as_posix())
    if not description:
        return body
    return NATIVE_FRONTMATTER.format(description=json.dumps(description, ensure_ascii=False)) + body


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


def slate_agents(*args: str) -> str | None:
    """Run the wrapper; its stdout, or None when SLATE (or the wrapper) is not reachable."""
    if not SLATE_AGENTS.is_file():
        return None
    try:
        done = subprocess.run([str(SLATE_AGENTS), *args], capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return done.stdout if done.returncode == 0 else None


def discover_native(vault_names: set) -> dict | None:
    """Store skills with no vault file → {name: (body, description)}. None when SLATE is unreachable (keep what is cached)."""
    listing = slate_agents("list", "--kind", "skill")
    if listing is None:
        return None
    native = {}
    for line in listing.splitlines():
        if not line.startswith("skill/"):
            continue
        name = line.split(" · ", 1)[0].removeprefix("skill/").strip()
        if name in vault_names or name in SKIP:
            continue
        raw = slate_agents("get", "skill", name)
        if raw is None:
            return None
        doc = json.loads(raw)
        native[name] = (doc["body"], doc.get("description"))
    return native


def write_if_changed(path: Path, content: str, created: list, updated: list, unchanged: list, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(content, encoding="utf-8")
        created.append(name)
    elif path.read_text(encoding="utf-8") != content:
        path.write_text(content, encoding="utf-8")
        updated.append(name)
    else:
        unchanged.append(name)


def main() -> int:
    if not SKILLS_DIR.is_dir():
        print(f"error: skills dir not found: {SKILLS_DIR}")
        return 1
    COMMANDS_DIR.mkdir(parents=True, exist_ok=True)
    skills = discover_skills()
    created, updated, deleted, unchanged = [], [], [], []
    for name, target in skills.items():
        write_if_changed(COMMANDS_DIR / f"{name}.md", VAULT_STUB.format(name=name, target=target), created, updated, unchanged, name)

    native = discover_native(set(skills))
    if native is None:
        # SLATE unreachable: whatever native stubs exist stay (their cached copies are the fallback they name)
        native_names = {p.stem for p in COMMANDS_DIR.glob("*.md") if NATIVE_MARKER in p.read_text(encoding="utf-8")}
        note = f"native {len(native_names)} (SLATE unreachable — kept as cached)"
    else:
        native_names = set(native)
        for name, (body, description) in native.items():
            cache = CACHE_DIR / f"{name}.md"
            cache.parent.mkdir(parents=True, exist_ok=True)
            if not cache.exists() or cache.read_text(encoding="utf-8") != body:
                cache.write_text(body, encoding="utf-8")
            stub = render_native_stub(name, cli=SLATE_AGENTS, cache=cache, description=description)
            write_if_changed(COMMANDS_DIR / f"{name}.md", stub, created, updated, unchanged, f"{name} (SLATE)")
        for stale in CACHE_DIR.glob("*.md") if CACHE_DIR.is_dir() else []:
            if stale.stem not in native:
                stale.unlink()
        note = f"native {len(native)}{': ' + ', '.join(sorted(native)) if native else ''}"

    for stub_path in sorted(COMMANDS_DIR.glob("*.md")):
        if stub_path.stem in skills or stub_path.stem in native_names:
            continue
        if MARKER in stub_path.read_text(encoding="utf-8"):
            stub_path.unlink()
            deleted.append(stub_path.stem)
        else:
            print(f"  warn: orphan {stub_path.name} is not a generated stub — left in place")

    print(
        f"sync_skill_stubs: {len(skills)} vault skills | {note} | "
        f"created {len(created)}{': ' + ', '.join(created) if created else ''} | "
        f"updated {len(updated)}{': ' + ', '.join(updated) if updated else ''} | "
        f"deleted {len(deleted)}{': ' + ', '.join(deleted) if deleted else ''} | "
        f"unchanged {len(unchanged)}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
