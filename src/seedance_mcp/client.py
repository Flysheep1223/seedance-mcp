from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .config import get_settings


class ArkAPIError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _require_api_key() -> str:
    api_key = get_settings().api_key
    if not api_key:
        raise ArkAPIError(
            "未配置 ARK_API_KEY，请在项目根目录的 .env 中填写火山方舟 API Key",
            code="missing_api_key",
        )
    return api_key


async def ark_request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    headers = {
        "Authorization": f"Bearer {_require_api_key()}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
        for attempt in range(settings.max_retries + 1):
            try:
                response = await client.request(
                    method,
                    f"{settings.base_url}{path}",
                    headers=headers,
                    json=body,
                    params=params,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt < settings.max_retries:
                    await asyncio.sleep(attempt + 1)
                    continue
                raise ArkAPIError(f"连接火山方舟失败：{exc}", code="transport_error") from exc

            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {}

            if (response.status_code == 429 or response.status_code >= 500) and attempt < settings.max_retries:
                await asyncio.sleep((1, 3, 8)[min(attempt, 2)])
                continue

            if response.is_error:
                error = payload.get("error") if isinstance(payload, dict) else {}
                error = error if isinstance(error, dict) else {}
                raise ArkAPIError(
                    error.get("message") or payload.get("message") or f"HTTP {response.status_code}",
                    code=error.get("code") or payload.get("code") or "ark_api_error",
                    status_code=response.status_code,
                )

            if not isinstance(payload, dict):
                raise ArkAPIError("火山方舟返回了非对象 JSON", code="invalid_response")
            return payload

    raise ArkAPIError("火山方舟请求失败", code="request_failed")
