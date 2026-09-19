import subprocess, json, threading, queue

class MCPClient:
    """最小可用的 MCP stdio client，自己实现 JSON-RPC 2.0。"""

    def __init__(self, command: str, args: list[str]):
        self.proc = subprocess.Popen(
            command.split() + args,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True,encoding="utf-8", errors="replace", bufsize=1)
        self._id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self):
        """持续读 stdout：按 id 分发响应，跳过 server 主动推送的通知。"""
        for line in self.proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue                      # 非 JSON 行直接丢弃（健壮性）
            if "id" in msg and msg["id"] in self._pending:
                self._pending[msg["id"]].put(msg)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        q = queue.Queue()
        self._pending[self._id] = q
        self.proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": self._id,
             "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        # ↓↓↓ 从这里开始是新代码 ↓↓↓
        try:
            resp = q.get(timeout=30)
        except queue.Empty:
            # 超时分两种：子进程死了 vs 活着但不应答
            if self.proc.poll() is not None:
                err = self.proc.stderr.read()
                raise RuntimeError(
                    f"MCP Server 子进程已退出(码 {self.proc.returncode}):\n{err}")
            raise TimeoutError(f"{method} 30秒无响应（进程还活着但不应答）")
        # ↑↑↑ 到这里结束 ↑↑↑
        del self._pending[self._id]
        if "error" in resp:
            raise RuntimeError(resp["error"])
        return resp["result"]

    # ---- MCP 协议方法 ----
    def initialize(self) -> dict:
        return self._rpc("initialize", {
            "protocolVersion": "2025-06-18",   # 以你用的 SDK/文档版本为准
            "capabilities": {},
            "clientInfo": {"name": "ai-office-assistant", "version": "0.1.0"}})
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        self.proc.stdin.flush()
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