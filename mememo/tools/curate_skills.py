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

    usage = skill_store.usage_map()

    def _uses(s: Skill) -> int:
        return int((usage.get(s.name) or {}).get("count", 0))

    by_prompt: dict[str, list[Skill]] = {}
    for s in skills:
        by_prompt.setdefault(s.prompt.strip(), []).append(s)

    removed: list[str] = []
    for group in by_prompt.values():
        if len(group) < 2:
            continue
        # Keep the MOST-USED copy first (then highest-priority, then shortest /
        # lexically-first name for determinism). Keeping the used copy matters when
        # stale-unused pruning runs in the same pass: a higher-priority but never-used
        # duplicate would otherwise win here and then get stale-pruned, deleting the
        # only copy of a skill that was actually being used.
        group.sort(key=lambda s: (-_uses(s), -s.priority, len(s.name), s.name))
        for dup in group[1:]:
            resp = await manage_skill(
                ManageSkillParams(action="delete", name=dup.name), skill_store, memory_manager
            )
            if resp.success:  # only report what this pass actually deleted (a race may have)
                removed.append(dup.name)
    return removed


async def _prune_stale_unused(
    skill_store: SkillStore, memory_manager: MemoryManager, stale_days: int
) -> list[str]:
    """Delete skills never injected and older than ``stale_days`` (reaps their mirrors)."""
    from .manage_skill import manage_skill
    from .schemas import ManageSkillParams

    removed: list[str] = []
    for name in skill_store.stale_unused_skills(stale_days=stale_days):
        resp = await manage_skill(
            ManageSkillParams(action="delete", name=name), skill_store, memory_manager
        )
        if resp.success:
            removed.append(name)
    return removed


def _prune_summary(
    removed_exact: list[str], removed_unused: list[str], candidates: list[str]
) -> str:
    bits = []
    if removed_exact:
        bits.append(f"removed {len(removed_exact)} exact dupe(s)")
    if removed_unused:
        bits.append(f"removed {len(removed_unused)} never-used skill(s)")
    if candidates:
        bits.append(f"{len(candidates)} never-used skill(s) prunable (preview)")
    return "; ".join(bits)


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
    removed_unused: list[str] = []
    unused_candidates: list[str] = []

    if params.stale_unused_days > 0 and not params.apply:
        # Dry preview only — apply path computes + deletes the live set below.
        unused_candidates = skill_store.stale_unused_skills(stale_days=params.stale_unused_days)

    if params.apply:
        removed_exact = await _prune_exact_dupes(skills, skill_store, memory_manager)
        if params.stale_unused_days > 0:
            removed_unused = await _prune_stale_unused(
                skill_store, memory_manager, params.stale_unused_days
            )
        if removed_exact or removed_unused:
            skills = skill_store.list_skills()  # reload after deletions

    prune_note = _prune_summary(removed_exact, removed_unused, unused_candidates)

    if len(skills) < 2:
        tail = f"{len(skills)} skill(s) left — no near-duplicates to merge."
        return CurateSkillsResponse(
            success=True,
            removed_exact=removed_exact,
            removed_unused=removed_unused,
            unused_candidates=unused_candidates,
            message=f"{prune_note}; {tail}" if prune_note else tail,
        )

    from ..context.skill_curator import cluster_duplicates

    vectors = memory_manager.embedder.embed([_skill_text(s) for s in skills])
    clusters = cluster_duplicates(vectors, params.threshold)
    if not clusters:
        head = f"No near-duplicate skills above similarity {params.threshold:.2f}"
        return CurateSkillsResponse(
            success=True,
            removed_exact=removed_exact,
            removed_unused=removed_unused,
            unused_candidates=unused_candidates,
            message=f"{head} ({prune_note})." if prune_note else f"{head}.",
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
        removed_unused=removed_unused,
        unused_candidates=unused_candidates,
        passthrough=True,
        passthrough_prompt=_build_merge_prompt(skills, clusters),
        message=(
            f"Found {len(clusters)} near-duplicate cluster(s) covering {n_dupes} skills. "
            "Merge each via the host model using passthrough_prompt, then manage_skill."
            + (f" ({prune_note})." if prune_note else "")
        ),
    )
