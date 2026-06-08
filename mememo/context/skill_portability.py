"""Export/import distilled skills as agentskills.io ``SKILL.md`` (Phase D — portability).

A mememo skill lives as YAML in the ``SkillStore``; ``SKILL.md`` is the open Agent Skills
format — YAML frontmatter (``name`` + ``description`` required, max 64 / 1024 chars) plus a
markdown body of instructions, one skill per directory (``<name>/SKILL.md``). Export maps
each skill to that layout, preserving mememo's ``intent``/``priority``/``tags`` under the
optional ``metadata`` key so the data survives the trip; import reverses it and tolerates a
foreign ``SKILL.md`` that has no ``metadata`` (defaults intent=general, priority=0). The
skill name normalizes to the SKILL.md form (lowercase, hyphens) on round-trip.

Pure functions — no I/O. The CLI (``export-skills`` / ``import-skills``) owns the files.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .skill_store import Skill

# SKILL.md name rule: <=64 chars, lowercase letters/digits/hyphens, no leading/trailing hyphen.
_NAME_BAD = re.compile(r"[^a-z0-9-]+")
_NAME_DASHES = re.compile(r"-+")


def skillmd_name(raw: str) -> str:
    """Coerce a skill name to a valid SKILL.md ``name`` (and parent-dir name)."""
    s = _NAME_BAD.sub("-", raw.strip().lower())
    s = _NAME_DASHES.sub("-", s).strip("-")[:64].strip("-")
    return s or "skill"


def _description(skill: Skill) -> str:
    """A non-empty SKILL.md ``description`` (required) — the prompt's first real line."""
    for line in skill.prompt.splitlines():
        line = line.strip()
        if line:
            return line[:1024]
    return f"{skill.intent} skill: {skill.name}"[:1024]


def skill_to_skillmd(skill: Skill) -> tuple[str, str]:
    """Return ``(dir_name, SKILL.md text)`` for a skill."""
    import yaml

    name = skillmd_name(skill.name)
    frontmatter = {
        "name": name,
        "description": _description(skill),
        "metadata": {
            "intent": skill.intent,
            "priority": skill.priority,
            "tags": list(skill.tags),
        },
    }
    front = yaml.safe_dump(frontmatter, default_flow_style=False, sort_keys=False).strip()
    body = skill.prompt.rstrip() + "\n"
    return name, f"---\n{front}\n---\n\n{body}"


def parse_skillmd(text: str) -> dict | None:
    """Parse a ``SKILL.md`` string into skill fields, or ``None`` if it has no name/body.

    Returns ``{name, intent, priority, prompt, tags}``. ``intent``/``priority``/``tags`` come
    from the optional ``metadata`` block; a foreign SKILL.md without it imports as a
    ``general`` skill. ``prompt`` is the markdown body (the description is only a summary).
    """
    from ..importers.markdown_memory import _parse_frontmatter

    meta, body = _parse_frontmatter(text)
    raw_name = meta.get("name")
    prompt = body.strip()
    # name must be a scalar (a YAML list/dict would str() into a garbled identifier).
    if not raw_name or isinstance(raw_name, (list, dict)) or not prompt:
        return None
    # Canonicalize to the SKILL.md name form so import->export->import is idempotent
    # (a foreign 'git_ops' and our 'git-ops' converge instead of multiplying).
    name = skillmd_name(str(raw_name))

    md = meta.get("metadata")
    md = md if isinstance(md, dict) else {}
    try:
        priority = int(md.get("priority", 0))
    except (TypeError, ValueError):
        priority = 0
    tags = md.get("tags")
    tags = [str(t) for t in tags] if isinstance(tags, list) else []
    return {
        "name": name,
        "intent": str(md.get("intent") or "general"),
        "priority": priority,
        "prompt": prompt,
        "tags": tags,
    }
