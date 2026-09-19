import os
import pytest
from mcp_client import MCPClient

@pytest.fixture(scope="module")
def client():
    """整个测试模块共用一个连接：Server 只启动一次，避免反复握手。"""
    c = MCPClient("officecli", ["mcp"])
    c.initialize()                     # 握手在 fixture 里做一次
    yield c
    c.close()                          # 测完关闭子进程

# ── 测试 1：initialize 握手 ──────────────────────
def test_initialize():
    """握手成功：Server 应返回自己的信息和支持的能力。"""
    c = MCPClient("officecli", ["mcp"])
    result = c.initialize()
    assert "serverInfo" in result, "响应里应有 serverInfo"
    assert "capabilities" in result, "响应里应有 capabilities"
    print("\nServer 信息:", result["serverInfo"])
    c.close()

# ── 测试 2：tools/list 非空且结构完整 ─────────────
def test_list_tools(client):
    tools = client.list_tools()
    assert len(tools) > 0, "工具列表不能为空"

    # 每个工具必须有面试要求讲清楚的三个字段
    for t in tools:
        assert "name" in t
        assert "description" in t
        assert "inputSchema" in t

    print("\n发现的工具:", [t["name"] for t in tools])

# ── 测试 3：call_tool 执行真实文档操作 ────────────
def test_call_tool_create_document(client, tmp_path):
    """真实创建一个 docx，并验证文件真的出现在磁盘上。"""
    target = tmp_path / "mcp_test.docx"

    result = client.call_tool("officecli", {
        "command": ["create", str(target)]   # 数组形式：避免路径被按空格错误切分
    })

    assert target.exists(), "工具说成功了，但文件没创建出来"
    print("\n创建结果:", result)