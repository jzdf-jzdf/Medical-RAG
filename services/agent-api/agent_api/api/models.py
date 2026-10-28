import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_api.db.session import get_db
from agent_api.models import ModelConfig
from agent_api.schemas.models import ModelConfigCreate, ModelConfigRead, ModelConfigUpdate
from agent_api.services.crypto import encrypt_secret

router = APIRouter(prefix="/models", tags=["models"])


def _response(item: ModelConfig) -> ModelConfigRead:
    return ModelConfigRead(
        id=item.id,
        name=item.name,
        provider=item.provider,
        model=item.model,
        base_url=item.base_url,
        max_tokens=item.max_tokens,
        temperature=item.temperature,
        is_default=item.is_default,
        is_active=item.is_active,
        has_api_key=bool(item.api_key_encrypted),
    )


@router.get("", response_model=list[ModelConfigRead])
async def list_models(db: AsyncSession = Depends(get_db)) -> list[ModelConfigRead]:
    items = list(
        (
            await db.scalars(
                select(ModelConfig)
                .where(ModelConfig.is_active.is_(True))
                .order_by(ModelConfig.is_default.desc(), ModelConfig.created_at)
            )
        ).all()
    )
    return [_response(item) for item in items]


@router.post("", response_model=ModelConfigRead, status_code=status.HTTP_201_CREATED)
async def create_model(
    payload: ModelConfigCreate, db: AsyncSession = Depends(get_db)
) -> ModelConfigRead:
    has_models = await db.scalar(
        select(ModelConfig.id).where(ModelConfig.is_active.is_(True)).limit(1)
    )
    make_default = payload.is_default or has_models is None
    if make_default:
        await _clear_default(db)
    item = ModelConfig(
        **payload.model_dump(exclude={"api_key", "is_default"}),
        api_key_encrypted=encrypt_secret(payload.api_key),
        is_default=make_default,
    )
    db.add(item)
    await _commit_or_conflict(db)
    await db.refresh(item)
    return _response(item)


@router.patch("/{model_id}", response_model=ModelConfigRead)
async def update_model(
    model_id: uuid.UUID,
    payload: ModelConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> ModelConfigRead:
    item = await db.get(ModelConfig, model_id)
    if item is None or not item.is_active:
        raise HTTPException(status_code=404, detail="Model configuration not found")

    values = payload.model_dump(exclude_unset=True, exclude={"api_key", "is_default"})
    for key, value in values.items():
        setattr(item, key, value)
    if payload.api_key is not None:
        item.api_key_encrypted = encrypt_secret(payload.api_key)
    if payload.is_default:
        await _clear_default(db, except_id=item.id)
        item.is_default = True
        item.is_active = True
    await _commit_or_conflict(db)
    await db.refresh(item)
    return _response(item)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(model_id: uuid.UUID, db: AsyncSession = Depends(get_db)) -> Response:
    item = await db.get(ModelConfig, model_id)
    if item is None or not item.is_active:
        raise HTTPException(status_code=404, detail="Model configuration not found")
    was_default = item.is_default
    item.is_active = False
    item.is_default = False
    if was_default:
        replacement = await db.scalar(
            select(ModelConfig)
            .where(ModelConfig.id != item.id, ModelConfig.is_active.is_(True))
            .order_by(ModelConfig.created_at)
            .limit(1)
        )
        if replacement:
            replacement.is_default = True
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _clear_default(db: AsyncSession, except_id: uuid.UUID | None = None) -> None:
    query = update(ModelConfig).where(ModelConfig.is_default.is_(True))
    if except_id:
        query = query.where(ModelConfig.id != except_id)
    await db.execute(query.values(is_default=False))


async def _commit_or_conflict(db: AsyncSession) -> None:
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Model name already exists") from exc
