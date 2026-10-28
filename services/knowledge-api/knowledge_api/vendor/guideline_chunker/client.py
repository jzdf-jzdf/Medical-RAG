"""Async HTTP client intended for integration into ``my_rag_system``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx


class GuidelineChunkerClientError(RuntimeError):
    def __init__(self, status_code: int, detail: Any):
        super().__init__(f"guideline chunker returned HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class GuidelineChunkerClient:
    """Small async client for the upload-after-normalization integration point."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float = 1800,
        client: httpx.AsyncClient | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self._client = client

    async def process_markdown_file(
        self,
        path: Path,
        *,
        dry_run: bool = False,
        force: bool = False,
        no_cache: bool = False,
        workers: int | None = None,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        source = Path(path)
        return await self.process_markdown_bytes(
            filename=source.name,
            content=source.read_bytes(),
            dry_run=dry_run,
            force=force,
            no_cache=no_cache,
            workers=workers,
            include_audit=include_audit,
        )

    async def process_markdown_bytes(
        self,
        *,
        filename: str,
        content: bytes,
        dry_run: bool = False,
        force: bool = False,
        no_cache: bool = False,
        workers: int | None = None,
        include_audit: bool = False,
    ) -> dict[str, Any]:
        data = {
            "dry_run": str(dry_run).lower(),
            "force": str(force).lower(),
            "no_cache": str(no_cache).lower(),
            "include_audit": str(include_audit).lower(),
        }
        if workers is not None:
            data["workers"] = str(workers)

        if self._client is not None:
            response = await self._client.post(
                f"{self.base_url}/api/v1/process",
                files={"file": (filename, content, "text/markdown")},
                data=data,
                timeout=self.timeout,
            )
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.base_url}/api/v1/process",
                    files={"file": (filename, content, "text/markdown")},
                    data=data,
                )

        if response.is_error:
            _raise_for_api_error(response)
        return response.json()

    async def persist_run(self, request_id: str) -> dict[str, Any]:
        """Retry PostgreSQL persistence for a run already retained locally."""

        url = f"{self.base_url}/api/v1/runs/{request_id}/persist"
        if self._client is not None:
            response = await self._client.post(url, timeout=self.timeout)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url)
        if response.is_error:
            _raise_for_api_error(response)
        return response.json()


def _raise_for_api_error(response: httpx.Response) -> None:
    try:
        detail = response.json().get("detail", response.text)
    except ValueError:
        detail = response.text
    raise GuidelineChunkerClientError(response.status_code, detail)
