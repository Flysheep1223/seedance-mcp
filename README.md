# Seedance 2.5 MCP

基于 Python MCP 2.x 开发的火山方舟 Seedance 2.5 MCP Server，提供视频生成任务和私域素材库管理能力，使用 SSE 对外提供 MCP 服务。

## 功能

### 视频生成

- 文生视频、首帧图生视频、首尾帧图生视频
- 图片、视频、音频全模态参考
- 视频编辑与视频延长
- 创建、查询、等待、列出、取消或删除视频任务
- 支持 480p、720p、1080p
- 支持 4 至 30 秒视频及自动时长
- 支持有声视频、MP4/MOV、尾帧返回和联网搜索

### 私域素材库

- 创建、查询、更新和删除素材组
- 上传图片、视频和音频素材
- 查询素材处理状态并等待素材变为 `Active`
- 使用 `asset://<AssetId>` 将已入库素材传给 Seedance
- 删除工具要求显式传入 `confirm=true`

## 环境要求

- Python 3.12.3 或更高版本
- [uv](https://docs.astral.sh/uv/)
- 已开通火山方舟 Seedance 2.5
- 使用素材库时，需具备对应 IAM 权限并完成素材库授权

## 环境变量

复制环境变量模板：

```bash
cp .env-example .env
```

配置内容：

```env
ARK_API_KEY=
VOLCENGINE_ACCESS_KEY=
VOLCENGINE_SECRET_KEY=
VOLCENGINE_SESSION_TOKEN=
```

变量说明：

| 变量                         | 用途                           | 是否必需          |
| ---------------------------- | ------------------------------ | ----------------- |
| `ARK_API_KEY`              | 调用 Seedance 2.5 视频生成 API | 视频生成必需      |
| `VOLCENGINE_ACCESS_KEY`    | 素材库 Access Key              | 素材库必需        |
| `VOLCENGINE_SECRET_KEY`    | 素材库 Secret Key              | 素材库必需        |
| `VOLCENGINE_SESSION_TOKEN` | STS 临时凭证配套 Token         | 仅临时 AK/SK 需要 |

长期 AK/SK 不需要 Session Token，可将该变量留空。不要将 `.env` 提交到 Git 或发送给他人。

## 安装与启动

```bash
cd /Users/bytedance/seedance-mcp
uv sync
uv run seedance-mcp
```

启动成功后，SSE 地址为：

```text
http://127.0.0.1:8000/sse
```

停止服务可在运行终端按 `Ctrl+C`。

修改代码或 `.env` 后，可执行以下命令重启：

```bash
pid=$(lsof -tiTCP:8000 -sTCP:LISTEN)
[ -n "$pid" ] && kill "$pid"
uv run seedance-mcp
```

## 配置

编辑 `mcp_settings.json`相关文档：

```json
{
  "mcpServers": {
    "seedance-2.5": {
      "url": "http://127.0.0.1:8000/sse",
      "type": "sse",
      "disabled": false,
      "autoApprove": []
    }
  }
}
```

服务启动后，重启或重新加载窗口即可。

## MCP 工具

### 视频任务

| 工具                                     | 作用                           |
| ---------------------------------------- | ------------------------------ |
| `seedance_create_video`                | 创建 Seedance 2.5 视频生成任务 |
| `seedance_get_video_task`              | 查询单个任务状态和结果         |
| `seedance_list_video_tasks`            | 查询最近 7 天的视频任务        |
| `seedance_wait_video_task`             | 轮询等待任务完成               |
| `seedance_cancel_or_delete_video_task` | 取消排队任务或删除任务记录     |
| `seedance_25_capabilities`             | 查看模型能力和参数限制         |

### 素材组

| 工具                            | 作用                     |
| ------------------------------- | ------------------------ |
| `seedance_create_asset_group` | 创建虚拟人像素材组       |
| `seedance_list_asset_groups`  | 查询素材组列表           |
| `seedance_get_asset_group`    | 查询单个素材组           |
| `seedance_update_asset_group` | 更新素材组名称或描述     |
| `seedance_delete_asset_group` | 永久删除素材组及组内素材 |

### 素材

| 工具                      | 作用                   |
| ------------------------- | ---------------------- |
| `seedance_create_asset` | 通过公网 URL 上传素材  |
| `seedance_list_assets`  | 查询素材列表           |
| `seedance_get_asset`    | 查询素材详情和处理状态 |
| `seedance_wait_asset`   | 等待素材处理完成       |
| `seedance_update_asset` | 更新素材名称           |
| `seedance_delete_asset` | 永久删除素材           |

素材库上传仅支持公网可访问的 HTTP/HTTPS URL，不支持本地文件路径或 Base64。素材状态为 `Active` 后才能用于视频生成。

## 项目结构

```text
seedance-mcp/
├── .env-example
├── main.py
├── pyproject.toml
├── src/seedance_mcp/
│   ├── client.py
│   ├── config.py
│   └── server.py
└── uv.lock
```

- `server.py`：MCP Server、视频工具、素材库签名和素材工具
- `client.py`：方舟视频生成 API 客户端
- `config.py`：环境变量配置
- `main.py`：兼容启动入口

## 注意事项

- 视频生成会产生方舟调用费用。
- 视频结果 URL 通常仅保留 24 小时，请及时下载或转存。
- 素材查询返回的临时 URL 有效期为 12 小时。
- 删除素材和素材组不可恢复。
- 临时 AK/SK 和 Session Token 会过期，过期后需要重新获取。

## 官方文档

- [创建视频生成任务](https://www.volcengine.com/docs/82379/1520757)
- [私域虚拟人像素材库](https://www.volcengine.com/docs/82379/2333565)
