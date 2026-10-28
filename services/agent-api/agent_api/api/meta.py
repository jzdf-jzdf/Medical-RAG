from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.core.config import get_settings
from agent_api.db.session import get_db
from agent_api.models import AgentSkill

router = APIRouter(tags=["metadata"])


@router.get("/model-profiles")
async def model_profiles() -> dict:
    settings = get_settings()
    profiles = [
        {"name": "default", "provider": settings.model_provider, "model": settings.model_name}
    ]
    profiles.extend(
        {"name": name, "provider": value.get("provider"), "model": value.get("model")}
        for name, value in settings.model_profiles_json.items()
    )
    return {"items": profiles, "default": settings.default_model_profile}


@router.get("/skills")
async def skills(db: AsyncSession = Depends(get_db)) -> dict:
    items = list((await db.scalars(select(AgentSkill).where(AgentSkill.enabled.is_(True)))).all())
    return {
        "items": [
            {"name": item.name, "version": item.version, "config": item.config} for item in items
        ]
    }
