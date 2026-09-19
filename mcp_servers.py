from mcp.server import Server
from mcp.server.stdio import stdio_server
import mcp.types as types

app = Server("contact-server")
CONTACTS = {"张三": "zhang@corp.com", "李四": "li@corp.com"}

@app.list_tools()
async def list_tools():
    return [types.Tool(
        name="query_contact",
        description="按姓名查询公司通讯录邮箱",
        inputSchema={"type": "object",
                     "properties": {"name": {"type": "string", "description": "姓名"}},
                     "required": ["name"]})]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "query_contact":
        person = arguments.get("name", "")
        email = CONTACTS.get(person)
        if email:
            return [types.TextContent(type="text",
                                      text=json.dumps({"name": person, "email": email}, ensure_ascii=False))]
        return [types.TextContent(type="text",
                                  text=json.dumps({"error": f"未找到 {person}"}, ensure_ascii=False))]
    raise ValueError(f"未知工具: {name}")

if __name__ == "__main__":
    import asyncio, json
    async def main():
        async with stdio_server() as (r, w):
            await app.run(r, w, app.create_initialization_options())
    asyncio.run(main())