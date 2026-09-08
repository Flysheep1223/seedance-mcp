from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


load_dotenv(override=True)


@dataclass(frozen=True)
class Settings:
    api_key: str = field(repr=False)
    base_url: str
    model: str
    timeout_seconds: float
    max_retries: int
    host: str
    port: int
    access_key: str = field(repr=False)
    secret_key: str = field(repr=False)
    session_token: str = field(repr=False)
    asset_base_url: str
    asset_region: str
    asset_project_name: str


def get_settings() -> Settings:
    return Settings(
        api_key=os.getenv("ARK_API_KEY", "").strip(),
        base_url=os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/"),
        model=os.getenv("ARK_MODEL", "doubao-seedance-2-5-260628").strip(),
        timeout_seconds=float(os.getenv("ARK_TIMEOUT_SECONDS", "60")),
        max_retries=int(os.getenv("ARK_MAX_RETRIES", "3")),
        host=os.getenv("MCP_HOST", "127.0.0.1"),
        port=int(os.getenv("MCP_PORT", "8000")),
        access_key=(
            os.getenv("VOLCENGINE_ACCESS_KEY", "")
            or os.getenv("VOLCENGINE_ACCESS_KEY_ID", "")
        ).strip(),
        secret_key=(
            os.getenv("VOLCENGINE_SECRET_KEY", "")
            or os.getenv("VOLCENGINE_SECRET_ACCESS_KEY", "")
        ).strip(),
        session_token=os.getenv("VOLCENGINE_SESSION_TOKEN", "").strip(),
        asset_base_url=os.getenv(
            "VOLCENGINE_ASSET_BASE_URL",
            "https://ark.cn-beijing.volcengineapi.com",
        ).rstrip("/"),
        asset_region=os.getenv("VOLCENGINE_REGION", "cn-beijing").strip(),
        asset_project_name=os.getenv("VOLCENGINE_PROJECT_NAME", "default").strip(),
    )
