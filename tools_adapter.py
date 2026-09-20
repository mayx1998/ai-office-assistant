# tools_adapter.py
import os
import sys
from langchain_core.tools import StructuredTool
from mcp_client import MCPClient
from pydantic import create_model, Field
from typing import Union
from config import cfg
from security import check_tool_args

_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))   # tools_adapter.py 在项目根


def _servers_from_config():
    """MCP server 清单从 config.yaml 读——config 里加三行即可接入新工具，Agent 代码零改动。
    command 为 'python' 时替换为当前解释器，保证无论在本机还是容器里都正确。"""
    servers = []
    for s in cfg()["mcp_servers"]:
        cmd = sys.executable if s["command"] == "python" else s["command"]
        args = [os.path.join(_ADAPTER_DIR, a) if a.endswith(".py") else a
                for a in s.get("args", [])]
        servers.append((s["name"], cmd, args))
    return servers
def json_schema_to_pydantic(name: str, schema: dict):
    """JSON Schema -> Pydantic model 的简易转换（args_schema 的自动化生成）"""
    fields = {}
    required = schema.get("required", [])

    for prop, spec in schema.get("properties", {}).items():
        raw_type = spec.get("type")
        type_map = {"string": str, "number": float, "integer": int,
                    "boolean": bool, "array": list}

        if isinstance(raw_type, list):
            # 联合类型：构造 Union[str, list] 这样的类型
            pytype = Union[tuple(type_map.get(t, str) for t in raw_type)]
        else:
            pytype = type_map.get(raw_type, str)
        description = spec.get("description", "")

        if prop in required:
            fields[prop] = (pytype, Field(description=description))
        else:
            fields[prop] = (pytype, Field(default=None, description=description))

    return create_model(name, **fields)


def load_mcp_tools(servers=None):
    """连接所有 MCP Server，把全部工具合并成 StructuredTool 列表。
    单个 server 启动失败（如容器里没有 officecli）只告警跳过，不影响其他 server——
    工具的运行时依赖决定部署拓扑。"""
    clients, tools = [], []

    for name, command, args in (servers or _servers_from_config()):
        try:
            client = MCPClient(command, args)
            client.initialize()
            server_tools = client.list_tools()
        except Exception as e:
            print(f"[tools_adapter] [WARN] MCP server '{name}' 启动失败，已跳过: {e}")
            continue
        clients.append(client)
        print(f"[tools_adapter] [OK] server '{name}' 就绪，{len(server_tools)} 个工具")

        for t in server_tools:
            args_schema = json_schema_to_pydantic(t["name"], t["inputSchema"])

            def make_func(c, tool_name):        # 工厂绑定 client 和工具名
                def func(**kwargs):
                    check_tool_args(kwargs)     # 路径白名单：越界直接抛 PermissionError
                    return c.call_tool(tool_name, kwargs)
                return func

            tools.append(StructuredTool.from_function(
                make_func(client, t["name"]),
                name=t["name"],
                description=t["description"],
                args_schema=args_schema,
            ))

    if not tools:
        raise RuntimeError("没有任何可用的 MCP 工具，请检查 config.yaml 的 mcp_servers 配置")
    return clients, tools