# debug_graph.py（项目根目录）
import time
from graph import app

result = app.invoke(
    {"task": "创建 out.docx，写一句 Hello",
     "plan": [], "messages": [], "last_tool_error": "",
     "review_pass": False, "retry_count": 0,
     "human_approved": True, "report": ""},
    config={"configurable": {"thread_id": f"debug-{time.time()}"}}
)
print("retry_count:", result["retry_count"])
print("last_tool_error:", result["last_tool_error"])
print("最后消息:", result["messages"][-1] if result["messages"] else "无")