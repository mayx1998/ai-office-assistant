"""第 5 步：批量生成 HTTP 服务。
POST /batch  收一个 employees.xlsx + 任务模板，立刻返回 task_id（取餐号），后台并发生成
GET  /tasks/{task_id}   查进度（done/total/errors）
GET  /traces/{thread_id} 查第 2 步落库的节点级耗时时间线

并发分级限流：信号量 sem 限制整体并发（无状态工具场景），office_sem 把真实 Office
操作压成串行（officecli 驱动单实例 Office，同时写会抢文件锁）。
启动：mcp/Scripts/python.exe -m uvicorn api:app --port 8000
"""
import asyncio, os, sys, uuid
from contextlib import asynccontextmanager

# Windows 控制台默认 GBK，LLM/工具输出里的特殊符号（如 ¥）会直接 UnicodeEncodeError
# 把进程标准流钉死成 UTF-8，与启动终端的代码页无关
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from openpyxl import load_workbook
from config import cfg
from graph import app as app_graph      # 编译好的 LangGraph 图（无中断版）


@asynccontextmanager
async def lifespan(_app):
    os.makedirs("outputs", exist_ok=True)   # 产物目录必须先存在，officecli 不会自动建目录
    yield


app = FastAPI(title="AI 办公助手 - 批量生成服务", lifespan=lifespan)


@app.get("/", include_in_schema=False)
def index():
    """托管单文件前端控制台。"""
    return FileResponse(os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html"))

sem = asyncio.Semaphore(cfg()["limits"]["concurrency"])            # 整体并发上限
office_sem = asyncio.Semaphore(cfg()["limits"]["office_concurrency"])  # Office 必须串行
tasks = {}   # task_id -> {"status", "done", "total", "errors"}，内存版进度表


class BatchReq(BaseModel):
    xlsx_path: str
    task_template: str    # 用 {name} 占位，如 "为{name}生成劳动合同，保存到 outputs/contract_{name}.docx"


def _initial_state(task_text: str, tid: str) -> dict:
    """OfficeState 的全部字段都要给初值，缺一个 LangGraph 都会报 KeyError。"""
    return {"task": task_text, "plan": [], "messages": [], "last_tool_error": "",
            "review_pass": False, "retry_count": 0, "human_approved": False,
            "report": "", "review_feedback": "", "thread_id": tid}


async def _run_one(task_id, name, task_text):
    async with sem:                       # 过总闸门
        tid = f"{task_id}-{name}"
        try:
            async with office_sem:        # Office 操作串行（真实 Office 是单实例）
                await asyncio.to_thread(
                    app_graph.invoke,
                    _initial_state(task_text, tid),
                    {"configurable": {"thread_id": tid}})
        except Exception as e:
            tasks[task_id]["errors"].append(f"{name}: {e}")
        finally:
            tasks[task_id]["done"] += 1   # 不管成败，进度 +1


async def _run_batch(task_id, req: BatchReq):
    wb = load_workbook(req.xlsx_path)
    names = [row[0].value for row in wb.active.iter_rows(min_row=2) if row[0].value]
    tasks[task_id].update(status="running", total=len(names), done=0, errors=[])
    await asyncio.gather(*[
        _run_one(task_id, n, req.task_template.format(name=n)) for n in names
    ])
    tasks[task_id]["status"] = "done"


@app.post("/batch")
async def start_batch(req: BatchReq, bg: BackgroundTasks):
    if not os.path.exists(req.xlsx_path):
        raise HTTPException(404, f"找不到文件：{req.xlsx_path}")
    task_id = uuid.uuid4().hex[:8]
    tasks[task_id] = {"status": "queued"}
    bg.add_task(_run_batch, task_id, req)   # 立刻返回 task_id，活扔给后台
    return {"task_id": task_id}


@app.get("/tasks/{task_id}")
def get_status(task_id: str):
    return tasks.get(task_id, {"status": "unknown"})


@app.get("/traces/{thread_id}")
def get_trace(thread_id: str):              # 第 2 步的 trace 查询也挂成接口
    import sqlite3
    db = os.path.join(os.path.dirname(os.path.abspath(__file__)), cfg()["trace"]["db"])
    rows = sqlite3.connect(db).execute(
        "SELECT node, duration_ms, detail, created_at FROM traces"
        " WHERE thread_id=? ORDER BY id", (thread_id,)).fetchall()
    return [{"node": r[0], "ms": r[1], "detail": r[2], "at": r[3]} for r in rows]
