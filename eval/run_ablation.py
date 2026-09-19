import os
import sys
import time
from eval.evaluator import run_eval
from graph import app, app_hitl

os.makedirs(os.path.join(os.path.dirname(__file__), "logs"), exist_ok=True)

class Tee:
    def __init__(self, path):
        self.file = open(path, "w", encoding="utf-8")
        self.stdout = sys.stdout
    def write(self, s):
        self.stdout.write(s)
        self.file.write(s)
    def flush(self):
        self.stdout.flush()
        self.file.flush()

log_path = os.path.join(os.path.dirname(__file__), "logs", f"run_{int(time.time())}.txt")
sys.stdout = Tee(log_path)
print(f"本次日志保存到: {log_path}")

INITIAL_STATE = {"task": "", "plan": [], "messages": [],
                 "last_tool_error": "", "review_pass": False,
                 "retry_count": 0, "human_approved": True, "report": ""}

# Baseline: Day 1 裸循环 + 手写工具
def baseline(task):
    from agent_loop import run_agent
    return {"result": run_agent(task), "retry_count": 0}

# Exp 2: LangGraph 状态机（无中断版）
def exp2(task):
    state = {**INITIAL_STATE, "task": task}
    return app.invoke(state, config={"configurable": {"thread_id": f"eval-{time.time()}"}})

def exp3(task):
    config = {"configurable": {"thread_id": f"eval-{time.time()}"}}
    state = {**INITIAL_STATE, "task": task}
    app_hitl.invoke(state, config)
    return app_hitl.invoke(None, config)

rows = run_eval(exp2, dataset_path=r"D:\dev\基于MCP的AI办公助手\eval\eval_dataset_v2.json", repeat=3)
