# probe_schema.py
import json
from mcp_client import MCPClient

c = MCPClient("officecli", ["mcp"])
c.initialize()
tools = c.list_tools()
print(json.dumps(tools[0], indent=2, ensure_ascii=False))
c.close()