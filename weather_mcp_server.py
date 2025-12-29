import os
import secrets
import httpx
import asyncio
import getpass
from typing import Annotated
from fastapi import FastAPI, Depends, HTTPException, status, Request
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import mcp.types as types
from contextlib import asynccontextmanager

# --- 配置初始化 (改为启动时手动输入) ---
print("=== MCP 服务器安全初始化 ===")
DEFAULT_USER = "admin"
# 获取用户名
API_USERNAME = input(f"请输入认证用户名 (默认: {DEFAULT_USER}): ") or DEFAULT_USER
# 安全获取密码 (输入时不会显示字符)
API_PASSWORD = getpass.getpass("请输入认证密码: ")

if not API_PASSWORD:
    print("错误: 必须设置密码才能启动服务器。")
    exit(1)

# 心知天气 API 配置
SENIVERSE_API_KEY = "SEfimV0EAFJ9r7Iro"

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
    async def _run_server():
        async with mcp_server.run(
            sse_transport.connect_scope(),
            mcp_server.options,
            raise_on_error=False
        ):
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
    return {"status": "ok", "message": "Weather MCP Server is running"}

@app.get("/sse")
async def sse_endpoint(request: Request, username: Annotated[str, Depends(authenticate)]):
    return await sse_transport.handle_sse_request(request)

@app.post("/messages")
async def messages_endpoint(request: Request, username: Annotated[str, Depends(authenticate)]):
    return await sse_transport.handle_post_request(request)

if __name__ == "__main__":
    import uvicorn
    print(f"\n服务即将启动，认证模式: Basic Auth")
    print(f"提示: 请在访问 http://127.0.0.1:8000/sse 时输入刚才设置的凭据。")
    uvicorn.run(app, host="127.0.0.1", port=8000)