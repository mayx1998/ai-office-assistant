import sqlite3, time, functools, threading
from config import cfg

_local = threading.local()   # 每个线程各用各的数据库连接，避免并发冲突

def _conn():
    c = getattr(_local, "conn", None)
    if c is None:
        c = sqlite3.connect(cfg()["trace"]["db"])
        c.execute("PRAGMA journal_mode=WAL")
        _local.conn = c
    return c

def init_trace_db():
    _conn().execute("""CREATE TABLE IF NOT EXISTS traces(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        thread_id TEXT, node TEXT,
        duration_ms INTEGER, detail TEXT,
        created_at TEXT)""")
    _conn().commit()

def trace_node(node_name: str):
    """装饰器工厂：trace_node('planner') 返回一个装饰器，
    套在哪个节点函数上，就自动记录它的耗时。"""
    def deco(fn):
        @functools.wraps(fn)
        def wrapper(state, *a, **kw):
            t0 = time.time()
            detail = ""
            try:
                return fn(state, *a, **kw)
            except Exception as e:
                detail = f"ERROR: {type(e).__name__}: {e}"
                raise
            finally:                       # 不管成功还是抛异常，都记账
                ms = int((time.time() - t0) * 1000)
                _conn().execute(
                    "INSERT INTO traces(thread_id,node,duration_ms,detail,created_at)"
                    " VALUES(?,?,?,?,datetime('now'))",
                    (state.get("thread_id", "?"), node_name, ms, detail))
                _conn().commit()
        return wrapper
    return deco