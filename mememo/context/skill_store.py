"""
Skill store for intent-based prompt injection.

Skills are reusable prompt templates stored as YAML files, selected
by intent classification and injected within a configurable token budget.
"""

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..utils.token_counter import count_tokens

logger = logging.getLogger(__name__)


@dataclass
class Skill:
    name: str
    intent: str
    priority: int
    prompt: str
    tags: list[str] = field(default_factory=list)
    token_count: int = 0

    def __post_init__(self):
        if not self.token_count:
            self.token_count = count_tokens(self.prompt)


class SkillStore:
    def __init__(self, base_dir: Path):
        self._skills_dir = base_dir / "skills"
        self._skills: list[Skill] | None = None
        self._last_mtime: float = 0.0

    def _needs_reload(self) -> bool:
        if self._skills is None:
            return True
        if not self._skills_dir.exists():
            return False
        try:
            current_mtime = max(
                (f.stat().st_mtime for f in self._skills_dir.glob("*.yaml")),
                default=0.0,
            )
            return current_mtime > self._last_mtime
        except OSError:
            return False

    def _load_skills(self) -> list[Skill]:
        if not self._needs_reload():
            return self._skills or []

        skills: list[Skill] = []
        if not self._skills_dir.exists():
            self._skills = []
            return skills

        max_mtime = 0.0
        for path in sorted(self._skills_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    continue
                skill = Skill(
                    name=data.get("name", path.stem),
                    intent=data.get("intent", "general"),
                    priority=int(data.get("priority", 0)),
                    prompt=str(data.get("prompt", "")),
                    tags=data.get("tags", []),
                )
                if skill.prompt.strip():
                    skills.append(skill)
                max_mtime = max(max_mtime, path.stat().st_mtime)
            except (yaml.YAMLError, OSError, ValueError) as e:
                logger.warning("Failed to load skill %s: %s", path, e)

        self._skills = skills
        self._last_mtime = max_mtime
        logger.debug("Loaded %d skills from %s", len(skills), self._skills_dir)
        return skills

    def get_skills_for_intent(self, intent: str, budget: int) -> list[Skill]:
        skills = self._load_skills()
        matching = [s for s in skills if s.intent == intent]
        matching.sort(key=lambda s: s.priority, reverse=True)

        selected: list[Skill] = []
        used = 0
        for skill in matching:
            if used + skill.token_count > budget:
                continue
            selected.append(skill)
            used += skill.token_count

        return selected

    def list_skills(self) -> list[Skill]:
        return self._load_skills()

    def get_skill(self, name: str) -> Skill | None:
        for skill in self._load_skills():
            if skill.name == name:
                return skill
        return None

    @staticmethod
    def _sanitize_name(name: str) -> str:
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError(f"Invalid skill name: {name!r}")
        return safe

    def create_skill(
        self, name: str, intent: str, prompt: str, priority: int = 0, tags: list[str] | None = None
    ) -> Skill:
        safe_name = self._sanitize_name(name)
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        skill = Skill(
            name=safe_name,
            intent=intent,
            priority=priority,
            prompt=prompt,
            tags=tags or [],
        )
        path = self._skills_dir / f"{safe_name}.yaml"
        data = {
            "name": skill.name,
            "intent": skill.intent,
            "priority": skill.priority,
            "prompt": skill.prompt,
            "tags": skill.tags,
        }
        path.write_text(yaml.dump(data, default_flow_style=False), encoding="utf-8")
        self._skills = None  # force reload
        logger.info("Created skill '%s' at %s", name, path)
        return skill

    def delete_skill(self, name: str) -> bool:
        safe_name = self._sanitize_name(name)
        path = self._skills_dir / f"{safe_name}.yaml"
        if path.exists():
            os.remove(path)
            self._skills = None  # force reload
            logger.info("Deleted skill '%s'", name)
            return True
        return False
