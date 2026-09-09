# Seedance 2.5 MCP

**English** | [简体中文](README.zh-CN.md)

A Python MCP 2.x server for Volcano Ark Seedance 2.5. It exposes video generation and private asset library operations over SSE.

## Features

### Video Generation

- Text-to-video, first-frame, and first/last-frame generation
- Multimodal references using images, videos, and audio
- Video editing and extension
- Create, inspect, wait for, list, cancel, and delete generation tasks
- 480p, 720p, and 1080p output
- Fixed durations from 4 to 30 seconds or automatic duration
- Synchronized audio, MP4/MOV output, last-frame return, and web search

### Private Asset Library

- Create, inspect, update, and delete asset groups
- Upload image, video, and audio assets
- Poll asset processing until the asset becomes `Active`
- Reference approved assets as `asset://<AssetId>` in Seedance requests
- Explicit `confirm=true` protection for destructive tools

## Requirements

- Python 3.12.3 or later
- [uv](https://docs.astral.sh/uv/)
- Seedance 2.5 enabled in Volcano Ark
- IAM permissions and asset library authorization when using private assets

## Environment Variables

Create the local environment file:

```bash
cp .env-example .env
```

Configure the credentials:

```env
ARK_API_KEY=
VOLCENGINE_ACCESS_KEY=
VOLCENGINE_SECRET_KEY=
VOLCENGINE_SESSION_TOKEN=
```

| Variable                     | Purpose                                     | Required                 |
| ---------------------------- | ------------------------------------------- | ------------------------ |
| `ARK_API_KEY`              | Seedance 2.5 video generation API           | For video generation     |
| `VOLCENGINE_ACCESS_KEY`    | Asset library Access Key                    | For asset operations     |
| `VOLCENGINE_SECRET_KEY`    | Asset library Secret Key                    | For asset operations     |
| `VOLCENGINE_SESSION_TOKEN` | Token issued with temporary STS credentials | Only for temporary AK/SK |

A long-lived AK/SK pair does not require a session token. Never commit `.env` or share its contents.

### Custom Model or Inference Endpoint

The default model identifier is defined near the top of `src/seedance_mcp/server.py`:

```python
MODEL_ID = "doubao-seedance-2-5-260628"
```

You may replace it with your own Seedance 2.5 inference endpoint ID:

```python
MODEL_ID = "ep-your-seedance-2-5-endpoint-id"
```

The recommended approach is to leave the source unchanged and add the optional `ARK_MODEL` variable to `.env`:

```env
ARK_MODEL=ep-your-seedance-2-5-endpoint-id
```

`ARK_MODEL` takes precedence over `MODEL_ID`. If it is absent or empty, the server uses `MODEL_ID`. Restart the MCP server after changing either value.

## Install and Run

```bash
cd /path/to/seedance-mcp
uv sync
uv run seedance-mcp
```

The SSE endpoint is available at:

```text
http://127.0.0.1:8000/sse
```

### Custom Host and Port

The listening address and port are configurable. The defaults are `127.0.0.1` and `8000`. Add these optional variables to `.env` to override them:

```env
MCP_HOST=0.0.0.0
MCP_PORT=9000
```

After changing them, restart the server. With the example above:

- Local clients connect to `http://127.0.0.1:9000/sse`.
- Remote clients connect to `http://<server-ip-or-domain>:9000/sse`.
- `0.0.0.0` is a bind address and must not be used as the client URL.

Listening on `0.0.0.0` exposes the MCP service to the network. Restrict access with a firewall or security group and use an authenticated HTTPS reverse proxy for public deployments.

Press `Ctrl+C` in the server terminal to stop it. After changing code or `.env`, restart it with:

```bash
pid=$(lsof -tiTCP:8000 -sTCP:LISTEN)
[ -n "$pid" ] && kill "$pid"
uv run seedance-mcp
```

## MCP Client Configuration

Add the server to your MCP client's `mcp_settings.json`:

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

Restart the MCP server or reload the editor window after changing this configuration.

## MCP Tools

### Video Tasks

| Tool                                     | Description                                         |
| ---------------------------------------- | --------------------------------------------------- |
| `seedance_create_video`                | Create a Seedance 2.5 generation task               |
| `seedance_get_video_task`              | Retrieve one task and its result                    |
| `seedance_list_video_tasks`            | List video tasks created within the last seven days |
| `seedance_wait_video_task`             | Poll a task until it reaches a terminal state       |
| `seedance_cancel_or_delete_video_task` | Cancel a queued task or delete a task record        |
| `seedance_25_capabilities`             | Show supported capabilities and parameter limits    |

### Asset Groups

| Tool                            | Description                                     |
| ------------------------------- | ----------------------------------------------- |
| `seedance_create_asset_group` | Create a private virtual-character asset group  |
| `seedance_list_asset_groups`  | List and filter asset groups                    |
| `seedance_get_asset_group`    | Retrieve one asset group                        |
| `seedance_update_asset_group` | Update an asset group's name or description     |
| `seedance_delete_asset_group` | Permanently delete a group and all assets in it |

### Assets

| Tool                      | Description                                  |
| ------------------------- | -------------------------------------------- |
| `seedance_create_asset` | Upload an asset from a public URL            |
| `seedance_list_assets`  | List and filter assets                       |
| `seedance_get_asset`    | Retrieve asset details and processing status |
| `seedance_wait_asset`   | Wait for asset processing to complete        |
| `seedance_update_asset` | Update an asset name                         |
| `seedance_delete_asset` | Permanently delete an asset                  |

Asset uploads accept public HTTP/HTTPS URLs only. Local file paths and Base64 payloads are not supported. An asset must be `Active` before it can be used for video generation.

## Project Structure

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

- `server.py`: MCP server, video tools, asset signing, and asset tools
- `client.py`: Volcano Ark video generation API client
- `config.py`: environment-based configuration
- `main.py`: compatibility entry point

## Important Notes

- Video generation requests incur Volcano Ark usage charges.
- Generated video URLs are typically retained for 24 hours. Download or transfer results promptly.
- Asset query URLs are temporary and remain valid for 12 hours.
- Asset and asset-group deletion is irreversible.
- Temporary AK/SK credentials and session tokens expire and must be refreshed.

## Official Documentation

- [Create a video generation task](https://www.volcengine.com/docs/82379/1520757)
- [Private virtual-character asset library](https://www.volcengine.com/docs/82379/2333565)
