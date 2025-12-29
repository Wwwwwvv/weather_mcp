import os
import secrets
import httpx
import asyncio
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import mcp.types as types
from contextlib import asynccontextmanager

# --- 配置区 ---
SENIVERSE_API_KEY = "SEfimV0EAFJ9r7Iro"
API_USERNAME = "admin"
API_PASSWORD = "admin123"

# --- MCP 服务器核心逻辑 ---
mcp_server = Server("weather-service")
sse_transport = SseServerTransport("/messages")

@mcp_server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="get_weather",
            description="查询指定城市的天气预报信息 (实时数据)",
            inputSchema={
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "城市名称，例如：beijing, shanghai"},
                    "unit": {"type": "string", "enum": ["c", "f"], "default": "c"}
                },
                "required": ["city"],
            },
        )
    ]

@mcp_server.call_tool()
async def handle_call_tool(name: str, arguments: dict | None) -> list[types.TextContent]:
    if name == "get_weather":
        city = arguments.get("city", "beijing")
        unit = arguments.get("unit", "c")
        url = "https://api.seniverse.com/v3/weather/now.json"
        params = {"key": SENIVERSE_API_KEY, "location": city, "language": "zh-Hans", "unit": unit}

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, params=params, timeout=10.0)
                response.raise_for_status()
                data = response.json()
                result = data["results"][0]
                formatted_msg = (
                    f"【实时天气】城市：{result['location']['name']}\n"
                    f"天气：{result['now']['text']}，温度：{result['now']['temperature']}°{unit.upper()}"
                )
                return [types.TextContent(type="text", text=formatted_msg)]
        except Exception as e:
            return [types.TextContent(type="text", text=f"错误: {str(e)}")]
    raise ValueError(f"Unknown tool: {name}")

# --- FastAPI 及其生命周期管理 ---

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 在后台启动 MCP 服务器运行循环
    async def _run_server():
        async with mcp_server.run(
            sse_transport.connect_scope(),
            mcp_server.options,
            raise_on_error=False
        ):
            # 保持循环直到应用关闭
            await asyncio.Event().wait()
    
    task = asyncio.create_task(_run_server())
    yield
    task.cancel()

app = FastAPI(title="Weather MCP Server", lifespan=lifespan)
security = HTTPBasic()

def authenticate(credentials: Annotated[HTTPBasicCredentials, Depends(security)]):
    is_user_ok = secrets.compare_digest(credentials.username, API_USERNAME)
    is_pass_ok = secrets.compare_digest(credentials.password, API_PASSWORD)
    if not (is_user_ok and is_pass_ok):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

@app.get("/")
async def root():
    """健康检查接口"""
    return {"status": "ok", "message": "Weather MCP Server is running"}

@app.get("/sse")
async def sse_endpoint(request: Request, username: Annotated[str, Depends(authenticate)]):
    """处理 SSE 连接"""
    return await sse_transport.handle_sse_request(request)

@app.post("/messages")
async def messages_endpoint(request: Request, username: Annotated[str, Depends(authenticate)]):
    """处理 MCP 消息"""
    return await sse_transport.handle_post_request(request)

if __name__ == "__main__":
    import uvicorn
    # 强制监听 127.0.0.1 避免 IPv6 导致的 404 或连接问题
    uvicorn.run(app, host="127.0.0.1", port=8000)