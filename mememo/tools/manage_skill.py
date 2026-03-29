"""manage_skill tool - CRUD operations for skill prompt templates."""

import logging
from typing import TYPE_CHECKING

from .schemas import ManageSkillParams, ManageSkillResponse

if TYPE_CHECKING:
    from ..context.skill_store import SkillStore

logger = logging.getLogger(__name__)


async def manage_skill(params: ManageSkillParams, skill_store: "SkillStore") -> ManageSkillResponse:
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
        return ManageSkillResponse(
            success=True,
            message=f"Created skill '{skill.name}' ({skill.token_count} tokens)",
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
        if deleted:
            return ManageSkillResponse(success=True, message=f"Deleted skill '{params.name}'")
        return ManageSkillResponse(success=False, message=f"Skill '{params.name}' not found")

    return ManageSkillResponse(success=False, message=f"Unknown action: {params.action}")
