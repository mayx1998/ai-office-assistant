from langgraph.graph import StateGraph, END
from graph_state import OfficeState
from nodes import make_planner, make_executor, make_reviewer, make_repairer, with_retry
from tools_adapter import load_mcp_tools
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from config import cfg
from tracing import init_trace_db, trace_node
import os

_API_KEY_ENV = cfg()["llm"]["api_key_env"]
if not os.environ.get(_API_KEY_ENV):
    raise RuntimeError(
        f"缺少环境变量 {_API_KEY_ENV}——请在系统环境变量或 .env 中设置后再启动"
        "（Windows 设完环境变量要重开终端才生效）")

llm = ChatOpenAI(
    model=cfg()["llm"]["model"],
    base_url=cfg()["llm"]["base_url"],
    api_key=os.environ[_API_KEY_ENV],
)

mcp_clients, TOOLS = load_mcp_tools()          # ←  Day 2 的成果原样复用

init_trace_db()
g = StateGraph(OfficeState)
g.add_node("planner", trace_node("planner")(make_planner(with_retry(llm), [t.name for t in TOOLS])))
g.add_node("executor", trace_node("executor")(make_executor(llm, TOOLS)))
g.add_node("reviewer", trace_node("reviewer")(make_reviewer(with_retry(llm))))
g.add_node("repairer", trace_node("repairer")(make_repairer(with_retry(llm))))

g.set_entry_point("planner")
g.add_edge("planner", "executor")
g.add_edge("executor", "reviewer")

def route_after_review(state: OfficeState) -> str:
    if state["review_pass"]:
        return "end"
    if state["retry_count"] >= 3:
        return "end_with_warning"
    return "repairer"

g.add_conditional_edges("reviewer", route_after_review,
                        {"end": END, "end_with_warning": END, "repairer": "repairer"})
g.add_edge("repairer", "executor")

def build_app(interrupt: bool = False):
    """同一张图，按需编译成'无中断'或'人工审批'两个版本。"""
    if interrupt:
        return g.compile(checkpointer=MemorySaver(),
                         interrupt_before=["executor"])
    return g.compile(checkpointer=MemorySaver())

app = build_app()                    # 无中断版：评测、FastAPI 用
app_hitl = build_app(interrupt=True) # 审批版：HITL demo 用
if __name__ == "__main__":
    config = {"configurable": {"thread_id": "task-001"}}   # 存档槽位 ID

    # ── 第一段：跑到 executor 前自动暂停 ──
    app_hitl.invoke({
        "task": "创建 report.docx，写一段 100 字的人工智能简介",
        "plan": [], "messages": [], "last_tool_error": "",
        "review_pass": False, "retry_count": 0,
        "human_approved": False, "report": "",
        "thread_id": "task-001",  # ← 加这行
    }, config)

    # ── 此刻图已暂停。查看模型打算干什么 ──
    snapshot = app_hitl.get_state(config)  # ← 这里也改
    print("计划步骤：", snapshot.values["plan"])
    print("暂停在节点：", snapshot.next)





    # ── 人工审批──
    answer = input("批准执行吗？(y/n): ")
    if answer.lower() == "y":
        result = app_hitl.invoke(None, config)   # ← 改
        print("完成：", result["messages"][-1])
    else:
        print("已拒绝，任务终止。")