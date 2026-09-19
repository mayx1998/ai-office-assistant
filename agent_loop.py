from openai import OpenAI
from tools_adapter import load_mcp_tools
import json
import os


API_KEY = os.getenv("DASHSCOPE_API_KEY")
API_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"
client = OpenAI(api_key=API_KEY, base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
mcp_clients, TOOLS = load_mcp_tools()

def run_agent(user_task:str,max_steps:int=15)-> str:
    messages=[
        {"role":"system","content":
         "你是文档助手，用工具完成任务，每步执行后简要说明进展,"
         "遇到错误先分析原因再修正重试."},
        {"role":"user","content":user_task},
    ]
    tool_map={t.name:t for t in TOOLS}
    openai_tools=[
        {"type": "function",
         "function": {"name": t.name,
                      "description": t.description,
                      "parameters": t.args_schema.model_json_schema()}}
        for t in TOOLS
    ]

    for step in range(max_steps):
        resp=client.chat.completions.create(
            model="qwen-plus",messages=messages,tools=openai_tools
        )
        msg=resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))


        if not msg.tool_calls:
            return msg.content

        for call in msg.tool_calls:
            args= json.loads(call.function.arguments)
            tool=tool_map[call.function.name]
            observation=tool.invoke(args)
            messages.append(
                {
                    "role": "tool",                    # ✅ 角色是 tool
                    "tool_call_id": call.id,           # ✅ 和调用对应
                    "content": str(observation)
                }
            )
    return "任务未完成：达到最大步数限制。"
if __name__ == "__main__":
    print(run_agent("帮我创建 demo.docx，写一段 100 字的人工智能简介"))
    print(run_agent("查一下张三的邮箱，把联系方式追加到 demo.docx 末尾"))





