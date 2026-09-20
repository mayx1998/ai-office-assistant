import subprocess, json, threading, queue
from config import cfg


class MCPClient:
    """最小可用的 MCP stdio client，自己实现 JSON-RPC 2.0。
    v2：超时可配置、任何路径都注销 id、修复 initialize 死代码、并发安全、崩溃自动重启一次。"""

    def __init__(self, command: str, args: list[str]):
        self.command = command
        self.args = args
        self._id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()   # 保护 id 分配和 stdin 写入
        self._start_proc()

    def _start_proc(self):
        self.proc = subprocess.Popen(
            self.command.split() + self.args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _restart(self):
        """server 进程崩溃后重启一次并重新握手。"""
        print("[mcp] server 进程异常，尝试重启…")
        try:
            self.proc.terminate()
        except Exception:
            pass
        self._start_proc()
        self.initialize()

    def _read_loop(self):
        """持续读 stdout：按 id 分发响应，跳过通知；迟到/无主响应丢弃。"""
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                      # 非 JSON 行直接丢弃（健壮性）
            if "id" not in msg:
                continue                      # server 主动推送的通知（如 initialized）
            q = self._pending.get(msg["id"])
            if q is not None:
                q.put(msg)
            else:
                print(f"[mcp] 丢弃迟到/无主响应 id={msg['id']}")

    def _send(self, payload: dict):
        """写一行 JSON-RPC 到子进程 stdin。加锁防并发写入交错。"""
        with self._lock:
            self.proc.stdin.write(json.dumps(payload) + "\n")
            self.proc.stdin.flush()

    def _rpc(self, method: str, params: dict | None = None, _retried: bool = False) -> dict:
        timeout = cfg()["limits"]["mcp_timeout_s"]
        q = queue.Queue()
        with self._lock:                      # id 分配 + 登记要原子化，防并发串号
            self._id += 1
            rid = self._id
            self._pending[rid] = q
        try:
            self._send({"jsonrpc": "2.0", "id": rid,
                        "method": method, "params": params or {}})
        except (BrokenPipeError, OSError):
            # 管道断了 = 子进程已死。重启一次并重发；第二次还死就向上抛
            self._pending.pop(rid, None)
            if _retried:
                raise
            self._restart()
            return self._rpc(method, params, _retried=True)
        try:
            resp = q.get(timeout=timeout)
        except queue.Empty:
            # 超时分两种：子进程死了 vs 活着但不应答
            if self.proc.poll() is not None:
                err = self.proc.stderr.read()
                raise RuntimeError(
                    f"MCP Server 子进程已退出(码 {self.proc.returncode}):\n{err}")
            raise TimeoutError(f"{method} {timeout}秒无响应（进程还活着但不应答）")
        finally:
            self._pending.pop(rid, None)      # 关键：任何路径都注销 id
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]

    # ---- MCP 协议方法 ----
    def initialize(self) -> dict:
        result = self._rpc("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "ai-office-assistant", "version": "0.1.0"}})
        # 握手完成通知（原来写在 return 后面，是永远不执行的死代码）
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list").get("tools", [])

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call",
                           {"name": name, "arguments": arguments})
        # content 是 [{type: "text", text: "..."}] 结构，拼接为字符串
        return "\n".join(c.get("text", "") for c in result.get("content", []))

    def close(self):
        self.proc.terminate()