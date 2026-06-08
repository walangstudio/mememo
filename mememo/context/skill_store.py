"""
Skill store for intent-based prompt injection.

Skills are reusable prompt templates stored as YAML files, selected
by intent classification and injected within a configurable token budget.
"""

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from ..utils.token_counter import count_tokens

logger = logging.getLogger(__name__)

# Sidecar (NOT *.yaml, so the skill glob never loads it as a skill) holding per-skill
# usage: {sanitized_name: {"count": int, "last_used": iso8601}}. Kept out of the YAML
# so a usage bump on every injection doesn't churn the skill files' mtime (which would
# trigger a needless reload storm).
_USAGE_FILE = "usage.json"


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
    def sanitize_name(name: str) -> str:
        """Canonical on-disk skill name (alnum/-/_). Raises on an empty result.

        Public so callers that key off the skill name (e.g. the manage_skill memory
        mirror) derive the same canonical name the YAML file is stored under.
        """
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        if not safe:
            raise ValueError(f"Invalid skill name: {name!r}")
        return safe

    # Back-compat alias for the previously-private name.
    _sanitize_name = sanitize_name

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
            self._purge_usage(safe_name)
            self._skills = None  # force reload
            logger.info("Deleted skill '%s'", name)
            return True
        return False

    # ----- usage tracking -------------------------------------------------
    # A skill's value is whether it ever gets injected. Tracking that lets the
    # curator prune skills that were distilled but never matched anything.

    def _usage_path(self) -> Path:
        return self._skills_dir / _USAGE_FILE

    def _load_usage(self) -> dict:
        path = self._usage_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("usage load failed (%s); treating as empty", e)
            return {}

    def _save_usage(self, usage: dict) -> None:
        path = self._usage_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Unique temp per writer (pid+thread): the daemon serves inject hooks on a
        # ThreadingHTTPServer, so a shared temp name could let two writers corrupt
        # each other's temp before the atomic replace. A lost update (last replace
        # wins) is fine for a heuristic counter; a torn file is not.
        tmp = path.with_name(f"usage.{os.getpid()}.{threading.get_ident()}.json.tmp")
        tmp.write_text(json.dumps(usage), encoding="utf-8")
        tmp.replace(path)  # atomic within a volume on POSIX and Windows

    def record_use(self, names: list[str]) -> None:
        """Increment usage_count + set last_used for each injected skill.

        Best-effort: a usage write must never break injection, so all failures are
        swallowed. Keys are canonicalized via ``sanitize_name`` so record_use,
        ``get_usage``, and ``stale_unused_skills`` always agree on the key.
        """
        keys: list[str] = []
        for n in names:
            if not n:
                continue
            try:
                keys.append(self.sanitize_name(n))
            except ValueError:
                continue
        if not keys:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            usage = self._load_usage()
            for key in keys:
                entry = usage.get(key) or {"count": 0, "last_used": None}
                entry["count"] = int(entry.get("count", 0)) + 1
                entry["last_used"] = now
                usage[key] = entry
            self._save_usage(usage)
        except Exception as e:  # never let usage accounting break the inject hook
            logger.debug("record_use failed: %s", e)

    def get_usage(self, name: str) -> dict:
        """Return ``{'count': int, 'last_used': iso|None}`` for a skill (zeros if unseen)."""
        safe = self._sanitize_name(name)
        entry = self._load_usage().get(safe)
        if not entry:
            return {"count": 0, "last_used": None}
        return {"count": int(entry.get("count", 0)), "last_used": entry.get("last_used")}

    def usage_map(self) -> dict:
        """All recorded usage counts, keyed by sanitized name (one read)."""
        return self._load_usage()

    def stale_unused_skills(self, *, stale_days: float, _now: float | None = None) -> list[str]:
        """Names of skills never injected (count==0) AND not modified in ``stale_days`` days.

        A freshly distilled skill has count==0 until it first matches, so the age
        gate (skill-file mtime) keeps a brand-new skill from being pruned before it
        has had a chance to be used. ``_now`` overrides the clock for tests.
        """
        if stale_days <= 0:
            return []
        now = _now if _now is not None else time.time()
        usage = self._load_usage()
        out: list[str] = []
        for skill in self._load_skills():
            try:
                key = self.sanitize_name(skill.name)
            except ValueError:
                continue
            if int((usage.get(key) or {}).get("count", 0)) > 0:
                continue
            try:
                age_days = (now - (self._skills_dir / f"{key}.yaml").stat().st_mtime) / 86400
            except OSError:
                continue
            if age_days >= stale_days:
                out.append(skill.name)
        return out

    def _purge_usage(self, safe_name: str) -> None:
        try:
            usage = self._load_usage()
            if usage.pop(safe_name, None) is not None:
                self._save_usage(usage)
        except Exception as e:
            logger.debug("usage purge failed for '%s': %s", safe_name, e)
