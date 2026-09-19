# tools_adapter.py
import os
import sys
import json
from langchain_core.tools import StructuredTool
from mcp_client import MCPClient
from pydantic import create_model, Field
from typing import Union

_ADAPTER_DIR = os.path.dirname(os.path.abspath(__file__))   # tools_adapter.py 在项目根

MCP_SERVERS = [
    ("officecli", ["mcp"]),                          # 文档操作（原有）
    (sys.executable, [os.path.join(_ADAPTER_DIR, "mcp_servers.py")]),            # ← 新增：通讯录查询
]
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
    """连接所有 MCP Server，把全部工具合并成 StructuredTool 列表。"""
    clients, tools = [], []

    for command, args in (servers or MCP_SERVERS):
        client = MCPClient(command, args)
        client.initialize()
        clients.append(client)

        for t in client.list_tools():
            args_schema = json_schema_to_pydantic(t["name"], t["inputSchema"])

            def make_func(c, tool_name):        # 工厂绑定 client 和工具名
                def func(**kwargs):
                    return c.call_tool(tool_name, kwargs)
                return func

            tools.append(StructuredTool.from_function(
                make_func(client, t["name"]),
                name=t["name"],
                description=t["description"],
                args_schema=args_schema,
            ))


    return client, tools