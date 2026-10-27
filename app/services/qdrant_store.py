"""Small async Qdrant adapter used by versioned knowledge indexes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import httpx

from app.core.config import get_settings


def collection_name(index_id: object) -> str:
    settings = get_settings()
    suffix = str(index_id).replace("-", "")
    return f"{settings.qdrant_collection}_{suffix}"


async def create_collection(name: str, dimension: int) -> None:
    settings = get_settings()
    url = f"{str(settings.qdrant_url).rstrip('/')}/collections/{name}"
    async with httpx.AsyncClient(timeout=settings.qdrant_timeout_seconds) as client:
        response = await client.put(
            url,
            json={"vectors": {"size": dimension, "distance": "Cosine"}},
        )
        response.raise_for_status()


async def delete_collection(name: str) -> None:
    """Delete a Qdrant index; a missing collection is already clean."""

    settings = get_settings()
    url = f"{str(settings.qdrant_url).rstrip('/')}/collections/{name}"
    async with httpx.AsyncClient(timeout=settings.qdrant_timeout_seconds) as client:
        response = await client.delete(url)
        if response.status_code == 404:
            return
        response.raise_for_status()


async def upsert_points(name: str, rows: Iterable[dict[str, Any]]) -> None:
    settings = get_settings()
    url = f"{str(settings.qdrant_url).rstrip('/')}/collections/{name}/points"
    points = list(rows)
    if not points:
        return
    async with httpx.AsyncClient(timeout=settings.qdrant_timeout_seconds) as client:
        for start in range(0, len(points), 128):
            response = await client.put(
                url,
                params={"wait": "true"},
                json={"points": points[start : start + 128]},
            )
            if response.is_error:
                raise RuntimeError(
                    f"Qdrant upsert failed ({response.status_code}): {response.text[:500]}"
                )


async def search(name: str, vector: list[float], limit: int) -> list[dict[str, Any]]:
    settings = get_settings()
    url = f"{str(settings.qdrant_url).rstrip('/')}/collections/{name}/points/search"
    async with httpx.AsyncClient(timeout=settings.qdrant_timeout_seconds) as client:
        response = await client.post(
            url,
            json={"vector": vector, "limit": limit, "with_payload": True},
        )
        response.raise_for_status()
        payload = response.json()
    result = payload.get("result", []) if isinstance(payload, dict) else []
    return result if isinstance(result, list) else []
