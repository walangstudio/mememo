"""manage_skill tool - CRUD operations for skill prompt templates.

Skills live in two places, kept in sync here: the ``SkillStore`` (YAML, injected
by intent) and the memory store (a ``skill``-typed GLOBAL memory, surfaced by
semantic/hybrid recall). The mirror makes a distilled skill recallable by
*relevance*, not just by its coarse intent bucket. Memory mirroring is
best-effort — a memory-store failure never blocks the primary SkillStore op.
"""

import logging
from typing import TYPE_CHECKING

from .schemas import ManageSkillParams, ManageSkillResponse

if TYPE_CHECKING:
    from ..context.skill_store import Skill, SkillStore
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)

# Reserved tag that links a skill-mirror memory back to its SkillStore name (for
# upsert + delete sync). Namespaced + paired with a content_type='skill' / GLOBAL
# scope so it can never match a user-applied tag. The mirror also carries the
# intent tag for filtering.
SKILL_TAG_PREFIX = "mememo-skill:"


def _skill_mirror_ids(memory_manager: "MemoryManager", safe_name: str) -> list[str]:
    """Ids of the GLOBAL ``skill``-typed mirror memories for a (sanitized) name."""
    from ..core.identity import GLOBAL_REPO_ID

    return memory_manager.storage_manager.get_memory_ids_by_tag(
        f"{SKILL_TAG_PREFIX}{safe_name}",
        repo_id=GLOBAL_REPO_ID,
        branch="main",
        content_type="skill",
    )


async def _delete_skill_memories(
    memory_manager: "MemoryManager", safe_name: str, *, exclude: str | None = None
) -> None:
    """Delete the skill-mirror memories for a (sanitized) name, keeping ``exclude``.

    ``safe_name`` must be the canonical SkillStore name (``SkillStore.sanitize_name``)
    so the tag matches what the mirror was stored under.
    """
    from ..core.identity import GLOBAL_REPO_ID

    for mid in _skill_mirror_ids(memory_manager, safe_name):
        if mid == exclude:
            continue
        await memory_manager.delete_memory(mid, repo_id=GLOBAL_REPO_ID, branch="main")


async def _mirror_skill_memory(
    memory_manager: "MemoryManager",
    safe_name: str,
    intent: str,
    prompt: str,
    tags: list[str] | None,
) -> None:
    """Upsert a skill as a GLOBAL ``skill``-typed memory so recall can find it.

    Create-then-prune (not delete-then-create): the new mirror is written first,
    then older mirrors for the same name are removed. If create_memory raises
    (e.g. the secret scanner rejects the content), nothing was deleted — the
    previous mirror stays, so a failed upsert never orphans the mirror. A prune
    failure (mirror already created) is logged but not raised.
    """
    from ..types.memory import CreateMemoryParams, MemoryRelationships

    mirror_tags = [f"{SKILL_TAG_PREFIX}{safe_name}", intent, *(tags or [])]
    memory = await memory_manager.create_memory(
        CreateMemoryParams(
            content=f"{safe_name}: {prompt}",
            type="skill",
            tags=mirror_tags,
            relationships=MemoryRelationships(),
        ),
        force_global=True,
    )
    try:
        await _delete_skill_memories(memory_manager, safe_name, exclude=memory.id)
    except Exception as e:  # mirror is created; a stale duplicate is harmless
        logger.warning("Skill mirror prune failed for '%s': %s", safe_name, e)


# Above this many skills, skip the create-time dedup nudge — re-embedding the whole
# library on every create stops being cheap. The periodic curate_skills pass still
# catches duplicates in a large library.
_NUDGE_MAX_SKILLS = 500


async def _dedup_nudge(
    skill: "Skill", skill_store: "SkillStore", memory_manager: "MemoryManager"
) -> str:
    """A one-line hint if the just-created skill near-duplicates an existing one.

    Non-destructive: it only names the closest match so the host can consolidate
    (via curate_skills). Best-effort — embedding failure yields no nudge. Uses the
    same threshold + doc-to-doc similarity the curator clusters on, so a skill
    flagged here is one curate_skills would later group.
    """
    from ..context.skill_curator import DEFAULT_DUP_THRESHOLD, nearest

    others = [s for s in skill_store.list_skills() if s.name != skill.name]
    if not others or len(others) >= _NUDGE_MAX_SKILLS:
        return ""
    texts = [f"{skill.name}: {skill.prompt}"] + [f"{s.name}: {s.prompt}" for s in others]
    vectors = memory_manager.embedder.embed(texts)
    hit = nearest(vectors[0], vectors[1:], DEFAULT_DUP_THRESHOLD)
    if hit is None:
        return ""
    idx, score = hit
    return (
        f" Note: {score:.0%} similar to existing skill '{others[idx].name}' — "
        "consider consolidating with curate_skills."
    )


async def manage_skill(
    params: ManageSkillParams,
    skill_store: "SkillStore",
    memory_manager: "MemoryManager | None" = None,
) -> ManageSkillResponse:
    if params.action == "list":
        skills = skill_store.list_skills()
        return ManageSkillResponse(
            success=True,
            message=f"Found {len(skills)} skills",
            skills=[
                {
                    "name": s.name,
                    "intent": s.intent,
                    "priority": s.priority,
                    "token_count": s.token_count,
                    "tags": s.tags,
                }
                for s in skills
            ],
        )

    if params.action == "get":
        if not params.name:
            return ManageSkillResponse(success=False, message="name is required for get")
        skill = skill_store.get_skill(params.name)
        if not skill:
            return ManageSkillResponse(success=False, message=f"Skill '{params.name}' not found")
        return ManageSkillResponse(
            success=True,
            message=f"Found skill '{skill.name}'",
            skills=[
                {
                    "name": skill.name,
                    "intent": skill.intent,
                    "priority": skill.priority,
                    "prompt": skill.prompt,
                    "token_count": skill.token_count,
                    "tags": skill.tags,
                }
            ],
        )

    if params.action == "create":
        if not params.name or not params.prompt or not params.intent:
            return ManageSkillResponse(
                success=False, message="name, intent, and prompt are required for create"
            )
        skill = skill_store.create_skill(
            name=params.name,
            intent=params.intent,
            prompt=params.prompt,
            priority=params.priority if params.priority is not None else 0,
            tags=params.tags,
        )
        nudge = ""
        if memory_manager is not None:
            try:
                await _mirror_skill_memory(
                    memory_manager, skill.name, skill.intent, skill.prompt, skill.tags
                )
            except Exception as e:  # mirror is best-effort; SkillStore op already succeeded
                logger.warning("Skill mirror to memory store failed for '%s': %s", skill.name, e)
            try:
                nudge = await _dedup_nudge(skill, skill_store, memory_manager)
            except Exception as e:  # nudge is advisory only — never fail the create
                logger.debug("Skill dedup nudge failed for '%s': %s", skill.name, e)
        return ManageSkillResponse(
            success=True,
            message=f"Created skill '{skill.name}' ({skill.token_count} tokens){nudge}",
            skills=[
                {
                    "name": skill.name,
                    "intent": skill.intent,
                    "priority": skill.priority,
                    "token_count": skill.token_count,
                    "tags": skill.tags,
                }
            ],
        )

    if params.action == "delete":
        if not params.name:
            return ManageSkillResponse(success=False, message="name is required for delete")
        deleted = skill_store.delete_skill(params.name)
        if memory_manager is not None:
            # Sanitize to the canonical name the mirror was tagged under (the YAML
            # store sanitizes too) — without this, a name like "git ops" would
            # query "mememo-skill:git ops" and never match "mememo-skill:git-ops".
            # Runs even when the YAML was already gone, so it also reaps orphans.
            try:
                from ..context.skill_store import SkillStore

                await _delete_skill_memories(memory_manager, SkillStore.sanitize_name(params.name))
            except Exception as e:  # best-effort — keep the SkillStore delete result authoritative
                logger.warning("Skill mirror delete failed for '%s': %s", params.name, e)
        if deleted:
            return ManageSkillResponse(success=True, message=f"Deleted skill '{params.name}'")
        return ManageSkillResponse(success=False, message=f"Skill '{params.name}' not found")

    return ManageSkillResponse(success=False, message=f"Unknown action: {params.action}")
