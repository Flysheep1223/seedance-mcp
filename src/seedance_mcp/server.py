from __future__ import annotations

import asyncio
import json
import os
import re
import time
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field
from volcenginesdkcore.signv4 import SignerV4

from .client import ArkAPIError, ark_request
from .config import get_settings


MODEL_ID = "doubao-seedance-2-5-260628"
RATIOS = ("16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive")
RESOLUTIONS = ("480p", "720p", "1080p")
TASK_TYPES = ("auto", "reference", "edit", "extend")

mcp = MCPServer(
    "seedance-2.5",
    instructions=(
        "Use seedance_create_video to submit a task, then call seedance_get_video_task "
        "or seedance_wait_video_task. Generated video URLs expire after 24 hours."
    ),
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _fail(code: str, message: str, suggestion: str = "") -> ToolError:
    return ToolError(
        _dump(
            {
                "error": code,
                "message": message,
                "fix_suggestion": suggestion or None,
            }
        )
    )


def _clean_urls(values: list[str] | None) -> list[str]:
    return [value.strip() for value in values or [] if value and value.strip()]


class AssetAPIError(RuntimeError):
    def __init__(self, message: str, *, code: str = "", status_code: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _require_asset_credentials() -> tuple[str, str, str | None]:
    settings = get_settings()
    if not settings.access_key or not settings.secret_key:
        raise AssetAPIError(
            "未配置素材库 AK/SK，请在 .env 中填写 VOLCENGINE_ACCESS_KEY 和 VOLCENGINE_SECRET_KEY",
            code="missing_access_key",
        )
    return settings.access_key, settings.secret_key, settings.session_token or None


def build_signed_asset_request(
    action: str,
    body: dict[str, Any],
) -> tuple[str, dict[str, str], str]:
    settings = get_settings()
    access_key, secret_key, session_token = _require_asset_credentials()
    endpoint = urlparse(settings.asset_base_url)
    if not endpoint.scheme or not endpoint.netloc:
        raise AssetAPIError("素材库 API 地址无效", code="invalid_asset_base_url")

    query = {"Action": action, "Version": "2024-01-01"}
    raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Host": endpoint.netloc,
    }
    SignerV4.sign(
        "/",
        "POST",
        headers,
        raw_body,
        [],
        query,
        access_key,
        secret_key,
        settings.asset_region,
        "ark",
        session_token,
    )
    return f"{settings.asset_base_url}/", headers, raw_body


async def asset_request(action: str, body: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    query = {"Action": action, "Version": "2024-01-01"}

    for attempt in range(settings.max_retries + 1):
        url, headers, raw_body = build_signed_asset_request(action, body)
        try:
            async with httpx.AsyncClient(timeout=settings.timeout_seconds, trust_env=False) as client:
                response = await client.post(url, params=query, headers=headers, content=raw_body)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            if attempt < settings.max_retries:
                await asyncio.sleep(attempt + 1)
                continue
            raise AssetAPIError(f"连接素材库 API 失败：{exc}", code="transport_error") from exc

        try:
            payload = response.json() if response.content else {}
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        if (response.status_code == 429 or response.status_code >= 500) and attempt < settings.max_retries:
            await asyncio.sleep((1, 3, 8)[min(attempt, 2)])
            continue

        metadata = payload.get("ResponseMetadata", {})
        api_error = metadata.get("Error", {}) if isinstance(metadata, dict) else {}
        if response.is_error or api_error:
            api_error = api_error if isinstance(api_error, dict) else {}
            raise AssetAPIError(
                api_error.get("Message") or payload.get("message") or f"HTTP {response.status_code}",
                code=api_error.get("Code") or payload.get("code") or "asset_api_error",
                status_code=response.status_code,
            )

        result = payload.get("Result", {})
        if not isinstance(result, dict):
            raise AssetAPIError("素材库 API 返回了非对象 Result", code="invalid_response")
        return result

    raise AssetAPIError("素材库 API 请求失败", code="request_failed")


def _asset_project_name(value: str | None) -> str:
    return (value or get_settings().asset_project_name or "default").strip()


def _validate_asset_name(name: str, *, label: str = "name") -> str:
    name = name.strip()
    if not name:
        raise ValueError(f"{label} 不能为空")
    if len(name) > 64:
        raise ValueError(f"{label} 不能超过 64 个字符")
    return name


async def _call_asset(action: str, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return await asset_request(action, body)
    except AssetAPIError as exc:
        raise _fail(
            exc.code or "asset_api_error",
            str(exc),
            "检查 AK/SK、可选 STS Token、IAM 方舟权限、ProjectName，以及是否已签署素材库授权函。",
        ) from exc


def build_create_body(
    *,
    prompt: str | None = None,
    first_frame_url: str | None = None,
    last_frame_url: str | None = None,
    reference_image_urls: list[str] | None = None,
    reference_video_urls: list[str] | None = None,
    reference_audio_urls: list[str] | None = None,
    task_type: str = "auto",
    resolution: str = "720p",
    ratio: str = "adaptive",
    duration: int = -1,
    generate_audio: bool = True,
    watermark: bool = False,
    output_format: str = "mp4",
    return_last_frame: bool = False,
    priority: int = 0,
    callback_url: str | None = None,
    execution_expires_after: int = 172800,
    safety_identifier: str | None = None,
    enable_web_search: bool = False,
) -> dict[str, Any]:
    prompt = (prompt or "").strip()
    first_frame_url = (first_frame_url or "").strip()
    last_frame_url = (last_frame_url or "").strip()
    images = _clean_urls(reference_image_urls)
    videos = _clean_urls(reference_video_urls)
    audios = _clean_urls(reference_audio_urls)

    if len(images) > 30:
        raise ValueError("reference_image_urls 最多 30 张")
    if len(videos) > 10:
        raise ValueError("reference_video_urls 最多 10 个")
    if len(audios) > 10:
        raise ValueError("reference_audio_urls 最多 10 段")
    if task_type not in TASK_TYPES:
        raise ValueError(f"task_type 必须是 {', '.join(TASK_TYPES)}")
    if resolution not in RESOLUTIONS:
        raise ValueError("Seedance 2.5 的 resolution 仅支持 480p、720p、1080p")
    if ratio not in RATIOS:
        raise ValueError(f"ratio 必须是 {', '.join(RATIOS)}")
    if duration != -1 and not 4 <= duration <= 30:
        raise ValueError("duration 必须为 -1，或 4 到 30 秒")
    if output_format not in ("mp4", "mov"):
        raise ValueError("output_format 必须是 mp4 或 mov")
    if not 0 <= priority <= 9:
        raise ValueError("priority 必须在 0 到 9 之间")
    if not 3600 <= execution_expires_after <= 259200:
        raise ValueError("execution_expires_after 必须在 3600 到 259200 秒之间")
    if safety_identifier and (
        len(safety_identifier) > 64 or not re.fullmatch(r"[A-Za-z0-9._:@-]+", safety_identifier)
    ):
        raise ValueError("safety_identifier 必须是不超过 64 字符的英文标识符")
    if callback_url and not callback_url.startswith(("http://", "https://")):
        raise ValueError("callback_url 必须是 HTTP 或 HTTPS URL")

    frame_mode = bool(first_frame_url or last_frame_url)
    reference_mode = bool(images or videos or audios)
    if last_frame_url and not first_frame_url:
        raise ValueError("使用尾帧时必须同时提供 first_frame_url")
    if frame_mode and reference_mode:
        raise ValueError("首帧/首尾帧模式不能与全模态参考素材混用")
    if frame_mode and ratio != "adaptive":
        raise ValueError("Seedance 2.5 首帧/首尾帧任务的 ratio 必须为 adaptive")
    if frame_mode and task_type != "auto":
        raise ValueError("首帧/首尾帧任务不要指定 reference/edit/extend")
    if task_type in ("edit", "extend") and not videos:
        raise ValueError(f"{task_type} 任务必须提供 reference_video_urls")
    if task_type != "auto" and not reference_mode:
        raise ValueError(f"{task_type} 任务必须提供至少一种参考素材")
    if task_type in ("edit", "extend") and ratio != "adaptive":
        raise ValueError(f"{task_type} 任务的 ratio 必须为 adaptive")
    if task_type == "edit" and duration != -1:
        raise ValueError("edit 任务的 duration 必须为 -1")
    if not (prompt or frame_mode or reference_mode):
        raise ValueError("prompt、图片、视频、音频至少提供一种")

    content: list[dict[str, Any]] = []
    if prompt:
        content.append({"type": "text", "text": prompt})
    if first_frame_url:
        content.append({"type": "image_url", "image_url": {"url": first_frame_url}, "role": "first_frame"})
    if last_frame_url:
        content.append({"type": "image_url", "image_url": {"url": last_frame_url}, "role": "last_frame"})
    content.extend(
        {"type": "image_url", "image_url": {"url": url}, "role": "reference_image"} for url in images
    )
    content.extend(
        {"type": "video_url", "video_url": {"url": url}, "role": "reference_video"} for url in videos
    )
    content.extend(
        {"type": "audio_url", "audio_url": {"url": url}, "role": "reference_audio"} for url in audios
    )

    body: dict[str, Any] = {
        "model": MODEL_ID,
        "content": content,
        "resolution": resolution,
        "ratio": ratio,
        "duration": duration,
        "generate_audio": generate_audio,
        "watermark": watermark,
        "output_format": output_format,
        "return_last_frame": return_last_frame,
        "priority": priority,
        "execution_expires_after": execution_expires_after,
    }
    if reference_mode:
        body["omni_reference_task_type"] = task_type
    if callback_url:
        body["callback_url"] = callback_url
    if safety_identifier:
        body["safety_identifier"] = safety_identifier
    if enable_web_search:
        body["tools"] = [{"type": "web_search"}]
    return body


async def _call_ark(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | list[tuple[str, Any]] | None = None,
) -> dict[str, Any]:
    try:
        return await ark_request(method, path, body=body, params=params)
    except ArkAPIError as exc:
        raise _fail(
            exc.code or "ark_api_error",
            str(exc),
            "检查 ARK_API_KEY、模型开通状态、余额以及输入参数。",
        ) from exc


@mcp.tool()
async def seedance_create_asset_group(
    name: Annotated[str, Field(description="素材组名称，不超过 64 字符")],
    description: Annotated[str, Field(description="素材组描述，不超过 300 字符")] = "",
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """创建一个 AIGC 私域虚拟人像素材组。首次使用前需在控制台签署授权函。"""
    try:
        name = _validate_asset_name(name)
        description = description.strip()
        if len(description) > 300:
            raise ValueError("description 不能超过 300 个字符")
    except ValueError as exc:
        raise _fail("invalid_parameters", str(exc)) from exc

    return _dump(
        await _call_asset(
            "CreateAssetGroup",
            {
                "Name": name,
                "Description": description,
                "GroupType": "AIGC",
                "ProjectName": _asset_project_name(project_name),
            },
        )
    )


@mcp.tool()
async def seedance_list_asset_groups(
    name: Annotated[str | None, Field(description="素材组名称模糊搜索")] = None,
    group_ids: Annotated[list[str] | None, Field(description="素材组 ID 精确筛选")] = None,
    group_type: Annotated[Literal["AIGC", "LivenessFace"], Field(description="素材组类型")] = "AIGC",
    max_results: Annotated[int, Field(ge=1, le=100, description="每页记录数")] = 20,
    next_token: Annotated[str | None, Field(description="上一页返回的 NextToken")] = None,
    sort_by: Annotated[Literal["CreateTime", "UpdateTime"], Field(description="排序字段")] = "CreateTime",
    sort_order: Annotated[Literal["Desc", "Asc"], Field(description="排序方向")] = "Desc",
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """查询私域素材组，使用 NextToken 分页。"""
    filters: dict[str, Any] = {"GroupType": group_type}
    if name and name.strip():
        filters["Name"] = name.strip()
    ids = _clean_urls(group_ids)
    if ids:
        filters["GroupIds"] = ids
    body: dict[str, Any] = {
        "Filter": filters,
        "MaxResults": max_results,
        "SortBy": sort_by,
        "SortOrder": sort_order,
        "ProjectName": _asset_project_name(project_name),
    }
    if next_token and next_token.strip():
        body["NextToken"] = next_token.strip()
    return _dump(await _call_asset("ListAssetGroups", body))


@mcp.tool()
async def seedance_get_asset_group(
    group_id: Annotated[str, Field(description="group- 开头的素材组 ID")],
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """查询单个素材组。"""
    return _dump(
        await _call_asset(
            "GetAssetGroup",
            {"Id": group_id.strip(), "ProjectName": _asset_project_name(project_name)},
        )
    )


@mcp.tool()
async def seedance_update_asset_group(
    group_id: Annotated[str, Field(description="group- 开头的素材组 ID")],
    name: Annotated[str | None, Field(description="新名称，不超过 64 字符")] = None,
    description: Annotated[str | None, Field(description="新描述，不超过 300 字符")] = None,
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """更新素材组名称或描述。"""
    body: dict[str, Any] = {
        "Id": group_id.strip(),
        "ProjectName": _asset_project_name(project_name),
    }
    try:
        if name is not None:
            body["Name"] = _validate_asset_name(name)
        if description is not None:
            description = description.strip()
            if len(description) > 300:
                raise ValueError("description 不能超过 300 个字符")
            body["Description"] = description
        if name is None and description is None:
            raise ValueError("name 和 description 至少提供一个")
    except ValueError as exc:
        raise _fail("invalid_parameters", str(exc)) from exc
    return _dump(await _call_asset("UpdateAssetGroup", body))


@mcp.tool()
async def seedance_delete_asset_group(
    group_id: Annotated[str, Field(description="group- 开头的素材组 ID")],
    confirm: Annotated[bool, Field(description="必须设为 true；将连同组内所有素材永久删除")] = False,
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """永久删除素材组及组内全部素材。该操作不可恢复。"""
    if not confirm:
        raise _fail("confirmation_required", "删除素材组会同时永久删除组内所有素材，请设置 confirm=true")
    await _call_asset(
        "DeleteAssetGroup",
        {"Id": group_id.strip(), "ProjectName": _asset_project_name(project_name)},
    )
    return _dump({"group_id": group_id.strip(), "deleted": True})


@mcp.tool()
async def seedance_create_asset(
    group_id: Annotated[str, Field(description="目标素材组 ID")],
    url: Annotated[str, Field(description="公网可访问的图片、视频或音频 URL；不支持 Base64")],
    asset_type: Annotated[Literal["Image", "Video", "Audio"], Field(description="素材类型")],
    name: Annotated[str | None, Field(description="素材名称，不超过 64 字符")] = None,
    project_name: Annotated[str | None, Field(description="必须与素材组所属项目一致")] = None,
) -> str:
    """提交一个私域素材进行异步预处理和审核。"""
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        raise _fail("invalid_parameters", "素材库仅接受 HTTP/HTTPS 公网 URL，不支持 Base64 或本地路径")
    body: dict[str, Any] = {
        "GroupId": group_id.strip(),
        "URL": url,
        "AssetType": asset_type,
        "ProjectName": _asset_project_name(project_name),
    }
    if name is not None:
        try:
            body["Name"] = _validate_asset_name(name)
        except ValueError as exc:
            raise _fail("invalid_parameters", str(exc)) from exc
    result = await _call_asset("CreateAsset", body)
    asset_id = result.get("Id")
    return _dump(
        {
            **result,
            "status": "Processing",
            "next_step": f"调用 seedance_wait_asset 等待处理完成；Active 后可用 asset://{asset_id} 生成视频。",
        }
    )


@mcp.tool()
async def seedance_list_assets(
    group_ids: Annotated[list[str] | None, Field(description="按素材组 ID 筛选")] = None,
    name: Annotated[str | None, Field(description="素材名称模糊搜索")] = None,
    statuses: Annotated[
        list[Literal["Active", "Processing", "Failed"]] | None,
        Field(description="素材状态筛选"),
    ] = None,
    group_type: Annotated[Literal["AIGC", "LivenessFace"], Field(description="素材组类型")] = "AIGC",
    max_results: Annotated[int, Field(ge=1, le=100, description="每页记录数")] = 20,
    next_token: Annotated[str | None, Field(description="上一页返回的 NextToken")] = None,
    sort_by: Annotated[
        Literal["CreateTime", "UpdateTime", "GroupId"],
        Field(description="排序字段"),
    ] = "CreateTime",
    sort_order: Annotated[Literal["Desc", "Asc"], Field(description="排序方向")] = "Desc",
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """查询私域素材，返回的素材 URL 有效期为 12 小时。"""
    filters: dict[str, Any] = {"GroupType": group_type}
    ids = _clean_urls(group_ids)
    if ids:
        filters["GroupIds"] = ids
    if name and name.strip():
        filters["Name"] = name.strip()
    if statuses:
        filters["Statuses"] = statuses
    body: dict[str, Any] = {
        "Filter": filters,
        "MaxResults": max_results,
        "SortBy": sort_by,
        "SortOrder": sort_order,
        "ProjectName": _asset_project_name(project_name),
    }
    if next_token and next_token.strip():
        body["NextToken"] = next_token.strip()
    result = await _call_asset("ListAssets", body)
    if result.get("Items"):
        result["url_notice"] = "返回的素材 URL 仅有效 12 小时；视频生成请优先使用 asset://<AssetId>。"
    return _dump(result)


@mcp.tool()
async def seedance_get_asset(
    asset_id: Annotated[str, Field(description="asset- 开头的素材 ID")],
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """查询素材处理状态；Active 后可用于 Seedance 视频生成。"""
    result = await _call_asset(
        "GetAsset",
        {"Id": asset_id.strip(), "ProjectName": _asset_project_name(project_name)},
    )
    if result.get("Status") == "Active":
        result["video_generation_reference"] = f"asset://{result.get('Id', asset_id.strip())}"
    if result.get("URL"):
        result["url_notice"] = "该访问 URL 仅有效 12 小时。"
    return _dump(result)


@mcp.tool()
async def seedance_wait_asset(
    ctx: Context,
    asset_id: Annotated[str, Field(description="asset- 开头的素材 ID")],
    timeout_seconds: Annotated[int, Field(ge=10, le=1800, description="最长等待时间")] = 300,
    poll_interval_seconds: Annotated[int, Field(ge=5, le=60, description="轮询间隔")] = 10,
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """轮询素材，直到变为 Active、Failed 或等待超时。"""
    started = time.monotonic()
    rounds = 0
    while time.monotonic() - started < timeout_seconds:
        rounds += 1
        result = await _call_asset(
            "GetAsset",
            {"Id": asset_id.strip(), "ProjectName": _asset_project_name(project_name)},
        )
        status = result.get("Status")
        if status == "Active":
            result["video_generation_reference"] = f"asset://{result.get('Id', asset_id.strip())}"
            return _dump(result)
        if status == "Failed":
            return _dump(result)
        try:
            await asyncio.wait_for(
                ctx.report_progress(
                    progress=min(0.95, (time.monotonic() - started) / timeout_seconds),
                    total=1.0,
                    message=f"第 {rounds} 次查询：{status or 'unknown'}",
                ),
                timeout=1.0,
            )
        except Exception:
            pass
        await asyncio.sleep(poll_interval_seconds)
    return _dump(
        {
            "asset_id": asset_id.strip(),
            "status": "still_processing",
            "next_step": "稍后调用 seedance_get_asset 继续查询。",
        }
    )


@mcp.tool()
async def seedance_update_asset(
    asset_id: Annotated[str, Field(description="asset- 开头的素材 ID")],
    name: Annotated[str, Field(description="新名称，不超过 64 字符")],
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """更新素材名称。"""
    try:
        name = _validate_asset_name(name)
    except ValueError as exc:
        raise _fail("invalid_parameters", str(exc)) from exc
    return _dump(
        await _call_asset(
            "UpdateAsset",
            {
                "Id": asset_id.strip(),
                "Name": name,
                "ProjectName": _asset_project_name(project_name),
            },
        )
    )


@mcp.tool()
async def seedance_delete_asset(
    asset_id: Annotated[str, Field(description="asset- 开头的素材 ID")],
    confirm: Annotated[bool, Field(description="必须设为 true；删除后不可恢复")] = False,
    project_name: Annotated[str | None, Field(description="项目名，默认 default")] = None,
) -> str:
    """永久删除单个素材。该操作不可恢复。"""
    if not confirm:
        raise _fail("confirmation_required", "删除素材不可恢复，请设置 confirm=true")
    await _call_asset(
        "DeleteAsset",
        {"Id": asset_id.strip(), "ProjectName": _asset_project_name(project_name)},
    )
    return _dump({"asset_id": asset_id.strip(), "deleted": True})


@mcp.tool()
async def seedance_create_video(
    prompt: Annotated[str | None, Field(description="视频提示词；纯素材任务可不填")] = None,
    first_frame_url: Annotated[str | None, Field(description="首帧图片 URL、Base64 或 asset:// ID")] = None,
    last_frame_url: Annotated[str | None, Field(description="尾帧图片；必须同时提供首帧")] = None,
    reference_image_urls: Annotated[list[str] | None, Field(description="参考图片，最多 30 张")] = None,
    reference_video_urls: Annotated[list[str] | None, Field(description="参考视频，最多 10 个，总时长不超过 30 秒")] = None,
    reference_audio_urls: Annotated[list[str] | None, Field(description="参考音频，最多 10 段，总时长不超过 30 秒")] = None,
    task_type: Annotated[
        Literal["auto", "reference", "edit", "extend"],
        Field(description="全模态任务类型；普通文生视频或首尾帧任务使用 auto"),
    ] = "auto",
    resolution: Annotated[Literal["480p", "720p", "1080p"], Field(description="输出分辨率")] = "720p",
    ratio: Annotated[
        Literal["16:9", "4:3", "1:1", "3:4", "9:16", "21:9", "adaptive"],
        Field(description="宽高比；编辑、延长、首尾帧任务必须 adaptive"),
    ] = "adaptive",
    duration: Annotated[int, Field(description="4-30 秒；-1 由模型选择，编辑任务必须 -1")] = -1,
    generate_audio: Annotated[bool, Field(description="是否生成同步音频")] = True,
    watermark: Annotated[bool, Field(description="是否添加 AI 生成水印")] = False,
    output_format: Annotated[Literal["mp4", "mov"], Field(description="输出格式")] = "mp4",
    return_last_frame: Annotated[bool, Field(description="是否返回尾帧 PNG")] = False,
    priority: Annotated[int, Field(ge=0, le=9, description="同一 Endpoint 内的排队优先级")] = 0,
    callback_url: Annotated[str | None, Field(description="任务状态变化回调 URL")] = None,
    execution_expires_after: Annotated[
        int, Field(ge=3600, le=259200, description="任务过期秒数")
    ] = 172800,
    safety_identifier: Annotated[str | None, Field(description="不超过 64 字符的匿名终端用户标识")] = None,
    enable_web_search: Annotated[bool, Field(description="允许模型按需联网搜索")] = False,
) -> str:
    """创建一个 Seedance 2.5 异步视频生成任务并返回 task_id。"""
    try:
        body = build_create_body(
            prompt=prompt,
            first_frame_url=first_frame_url,
            last_frame_url=last_frame_url,
            reference_image_urls=reference_image_urls,
            reference_video_urls=reference_video_urls,
            reference_audio_urls=reference_audio_urls,
            task_type=task_type,
            resolution=resolution,
            ratio=ratio,
            duration=duration,
            generate_audio=generate_audio,
            watermark=watermark,
            output_format=output_format,
            return_last_frame=return_last_frame,
            priority=priority,
            callback_url=callback_url,
            execution_expires_after=execution_expires_after,
            safety_identifier=safety_identifier,
            enable_web_search=enable_web_search,
        )
    except ValueError as exc:
        raise _fail("invalid_parameters", str(exc)) from exc

    result = await _call_ark("POST", "/contents/generations/tasks", body=body)
    return _dump(
        {
            "task_id": result.get("id"),
            "status": result.get("status", "queued"),
            "model": body["model"],
            "next_step": "调用 seedance_get_video_task 查询；生成完成前不要重复创建任务。",
        }
    )


@mcp.tool()
async def seedance_get_video_task(task_id: Annotated[str, Field(description="创建任务返回的 task_id")]) -> str:
    """查询视频任务状态；成功时返回 24 小时有效的视频 URL。"""
    result = await _call_ark("GET", f"/contents/generations/tasks/{task_id}")
    if result.get("status") == "succeeded":
        result["url_notice"] = "video_url 和 last_frame_url 仅保留 24 小时，请及时下载或转存。"
    return _dump(result)


@mcp.tool()
async def seedance_list_video_tasks(
    status: Annotated[
        Literal["queued", "running", "cancelled", "succeeded", "failed", "expired"] | None,
        Field(description="按任务状态筛选"),
    ] = None,
    task_ids: Annotated[list[str] | None, Field(description="按任务 ID 精确筛选，可传多个")] = None,
    endpoint_id: Annotated[str | None, Field(description="按 ep- 开头的推理接入点 ID 筛选")] = None,
    page_num: Annotated[int, Field(ge=1, le=500, description="页码")] = 1,
    page_size: Annotated[int, Field(ge=1, le=500, description="每页数量")] = 20,
) -> str:
    """查询最近 7 天的视频生成任务列表。"""
    params: list[tuple[str, Any]] = [
        ("page_num", page_num),
        ("page_size", page_size),
    ]
    if status:
        params.append(("filter.status", status))
    if endpoint_id:
        params.append(("filter.model", endpoint_id))
    params.extend(("filter.task_ids", task_id) for task_id in _clean_urls(task_ids))
    return _dump(await _call_ark("GET", "/contents/generations/tasks", params=params))


@mcp.tool()
async def seedance_wait_video_task(
    ctx: Context,
    task_id: Annotated[str, Field(description="创建任务返回的 task_id")],
    timeout_seconds: Annotated[int, Field(ge=10, le=600, description="最长等待时间")] = 120,
    poll_interval_seconds: Annotated[int, Field(ge=5, le=30, description="轮询间隔")] = 10,
) -> str:
    """轮询任务直到成功、失败或超时。"""
    started = time.monotonic()
    rounds = 0
    while time.monotonic() - started < timeout_seconds:
        rounds += 1
        result = await _call_ark("GET", f"/contents/generations/tasks/{task_id}")
        status = result.get("status")
        if status == "succeeded":
            result["url_notice"] = "video_url 和 last_frame_url 仅保留 24 小时，请及时下载或转存。"
            return _dump(result)
        if status in ("failed", "cancelled", "expired"):
            return _dump(result)
        try:
            await asyncio.wait_for(
                ctx.report_progress(
                    progress=min(0.95, (time.monotonic() - started) / timeout_seconds),
                    total=1.0,
                    message=f"第 {rounds} 次查询：{status or 'unknown'}",
                ),
                timeout=1.0,
            )
        except Exception:
            pass
        await asyncio.sleep(poll_interval_seconds)

    return _dump(
        {
            "task_id": task_id,
            "status": "still_running",
            "message": f"等待 {timeout_seconds} 秒后任务仍未完成。",
            "next_step": "稍后继续调用 seedance_get_video_task。",
        }
    )


@mcp.tool()
async def seedance_cancel_or_delete_video_task(
    task_id: Annotated[str, Field(description="要取消或删除的任务 ID")],
) -> str:
    """取消 queued 任务，或删除 succeeded/failed/expired 任务记录。"""
    await _call_ark("DELETE", f"/contents/generations/tasks/{task_id}")
    return _dump({"task_id": task_id, "ok": True})


@mcp.tool()
def seedance_25_capabilities() -> str:
    """返回本 MCP 已实现的 Seedance 2.5 官方能力边界。"""
    return _dump(
        {
            "model": MODEL_ID,
            "resolution": list(RESOLUTIONS),
            "ratio": list(RATIOS),
            "duration": {"default": -1, "seconds": [4, 30]},
            "reference_limits": {"images": 30, "videos": 10, "audio": 10},
            "task_types": list(TASK_TYPES),
            "audio_only_input": True,
            "generate_audio": True,
            "output_formats": ["mp4", "mov"],
            "web_search": True,
            "unsupported": ["4k", "frames", "seed", "camera_fixed", "service_tier=flex", "draft"],
        }
    )


def main() -> None:
    settings = get_settings()
    transport = os.getenv("MCP_TRANSPORT", "sse")
    mcp.run(transport=transport, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
