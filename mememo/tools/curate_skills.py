"""curate_skills tool — consolidate a distilled-skill library that has drifted.

Phase C of the self-learning loop. Autonomous distillation (Phase A) keeps adding
skills, so near-duplicates accumulate. This pass:

  1. (apply=True) deterministically deletes EXACT-duplicate skills — identical prompt,
     safe to merge without a model — keeping the highest-priority one.
  2. clusters the remaining near-duplicates by embedding similarity and returns a
     passthrough_prompt asking the host model to merge each cluster into one skill
     (then apply via manage_skill). Near-dupes are never auto-deleted — only the host
     (or a configured LLM) can decide which wording to keep, so this is non-destructive
     by default, mirroring cleanup_memory's dry-run convention.

Passthrough-first: no API key needed — the host model does the merge.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from .schemas import CurateSkillsParams, CurateSkillsResponse

if TYPE_CHECKING:
    from ..context.skill_store import Skill, SkillStore
    from ..core.memory_manager import MemoryManager

logger = logging.getLogger(__name__)


def _skill_text(skill: Skill) -> str:
    """Text embedded for similarity — mirrors the skill-memory mirror's content."""
    return f"{skill.name}: {skill.prompt}"


async def _prune_exact_dupes(
    skills: list[Skill], skill_store: SkillStore, memory_manager: MemoryManager
) -> list[str]:
    """Delete exact-prompt-duplicate skills, keeping the best one per prompt.

    Groups skills by their (stripped) prompt; for any group with >1 member, keeps the
    highest-priority (tie → shortest name) and deletes the rest through manage_skill so
    the skill-memory mirror is reaped in lockstep. Returns the deleted names.
    """
    from .manage_skill import manage_skill
    from .schemas import ManageSkillParams

    by_prompt: dict[str, list[Skill]] = {}
    for s in skills:
        by_prompt.setdefault(s.prompt.strip(), []).append(s)

    removed: list[str] = []
    for group in by_prompt.values():
        if len(group) < 2:
            continue
        # Keep the highest-priority skill; tie → shortest then lexically-first name
        # so the kept/deleted choice is fully deterministic regardless of FS order.
        group.sort(key=lambda s: (-s.priority, len(s.name), s.name))
        for dup in group[1:]:
            resp = await manage_skill(
                ManageSkillParams(action="delete", name=dup.name), skill_store, memory_manager
            )
            if resp.success:  # only report what this pass actually deleted (a race may have)
                removed.append(dup.name)
    return removed


def _build_merge_prompt(skills: list[Skill], clusters: list[list[int]]) -> str:
    """Host-model instruction to merge each near-duplicate cluster into one skill."""
    lines = [
        "You are consolidating a reusable-skill library that has accumulated near-duplicate "
        "skills. For EACH cluster below, the skills overlap heavily. Merge each cluster into "
        "ONE skill that preserves every distinct, useful instruction (steps, commands, edge "
        "cases), then apply it with the manage_skill tool:",
        "  1. manage_skill(action='create', name=<clearest name>, intent=<most specific intent>, "
        "prompt=<merged prompt>) — reusing one of the cluster's names overwrites it.",
        "  2. manage_skill(action='delete', name=<every OTHER name in the cluster>).",
        "Keep the clearest name and the most specific intent. Do not drop any edge case or command.",
        "",
    ]
    for n, cluster in enumerate(clusters, 1):
        lines.append(f"## Cluster {n}")
        for i in cluster:
            s = skills[i]
            lines.append(f"- name: {s.name} | intent: {s.intent} | priority: {s.priority}")
            lines.append(f"  prompt: {s.prompt}")
        lines.append("")
    return "\n".join(lines).rstrip()


async def curate_skills(
    params: CurateSkillsParams,
    skill_store: SkillStore,
    memory_manager: MemoryManager | None = None,
) -> CurateSkillsResponse:
    skills = skill_store.list_skills()
    if len(skills) < 2:
        return CurateSkillsResponse(
            success=True, message=f"{len(skills)} skill(s) — nothing to curate."
        )

    if memory_manager is None:
        # Near-duplicate detection needs the embedder; exact-dupe deletion needs the
        # mirror sync. Both live on the memory manager.
        return CurateSkillsResponse(
            success=False,
            message="curate_skills requires the memory manager (embedder + mirror sync).",
        )

    removed_exact: list[str] = []
    if params.apply:
        removed_exact = await _prune_exact_dupes(skills, skill_store, memory_manager)
        if removed_exact:
            skills = skill_store.list_skills()  # reload after deletions
            if len(skills) < 2:
                return CurateSkillsResponse(
                    success=True,
                    removed_exact=removed_exact,
                    message=f"Removed {len(removed_exact)} exact-duplicate skill(s); "
                    f"{len(skills)} left — no near-duplicates to merge.",
                )

    from ..context.skill_curator import cluster_duplicates

    vectors = memory_manager.embedder.embed([_skill_text(s) for s in skills])
    clusters = cluster_duplicates(vectors, params.threshold)
    if not clusters:
        return CurateSkillsResponse(
            success=True,
            removed_exact=removed_exact,
            message=(
                f"No near-duplicate skills above similarity {params.threshold:.2f}"
                + (f" (removed {len(removed_exact)} exact dupe(s))." if removed_exact else ".")
            ),
        )

    cluster_dicts = [
        [
            {"name": skills[i].name, "intent": skills[i].intent, "priority": skills[i].priority}
            for i in cluster
        ]
        for cluster in clusters
    ]
    n_dupes = sum(len(c) for c in clusters)
    return CurateSkillsResponse(
        success=True,
        clusters=cluster_dicts,
        removed_exact=removed_exact,
        passthrough=True,
        passthrough_prompt=_build_merge_prompt(skills, clusters),
        message=(
            f"Found {len(clusters)} near-duplicate cluster(s) covering {n_dupes} skills. "
            "Merge each via the host model using passthrough_prompt, then manage_skill."
            + (f" Removed {len(removed_exact)} exact dupe(s)." if removed_exact else "")
        ),
    )
