import sys
import pytest
from mcp_client import MCPClient

@pytest.fixture(scope="module")
def client():
    c = MCPClient(sys.executable, ["mcp_servers.py"])
    c.initialize()
    yield c
    c.close()

def test_list_tools(client):
    tools = client.list_tools()
    assert [t["name"] for t in tools] == ["query_contact"]

def test_query_found(client):
    result = client.call_tool("query_contact", {"name": "张三"})
    assert "zhang@corp.com" in result

def test_query_not_found(client):
    result = client.call_tool("query_contact", {"name": "王五"})
    assert "未找到" in result