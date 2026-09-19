import subprocess, json
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

def run_officecli(args: list) -> str:
    """执行 officecli 命令，强制 JSON 输出，返回结构化结果字符串。"""
    try:
        r = subprocess.run(["officecli"] + args + ["--json"],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",   # ← 修 GBK 崩溃
                           timeout=30)
    except subprocess.TimeoutExpired:
        return json.dumps({"error": {"code": "TIMEOUT",
                                     "message": "命令执行超过30秒"}},
                          ensure_ascii=False)
    if r.returncode != 0:
        return json.dumps({"error": {"code": "CLI_FAILED",
                                     "message": r.stderr.strip()[:500]}},
                          ensure_ascii=False)   # ← 让中文错误信息原样输出
    return r.stdout

class WordCreateInput(BaseModel):
    file: str = Field(description="要创建的 docx 文件路径，如 demo.docx")

def word_create(file: str) -> str:                    # ← 参数名与 schema 一致
    """创建一个新的空白 Word 文档（.docx 格式）。"""
    return run_officecli(["create", file])

class WordAddInput(BaseModel):
    file: str = Field(description="目标 docx 文件路径")
    content: str = Field(description="要写入的正文文本")

def word_add(file: str, content: str) -> str:
    """向指定的 Word 文档正文末尾追加一个段落。"""
    return run_officecli(["add", file, "/body",
                          "--type", "paragraph",
                          "--prop", f"text={content}"])

class WordViewInput(BaseModel):
    file: str = Field(description="要查看的 docx 文件路径")

def word_view(file: str) -> str:
    """读取并返回 Word 文档中的全部文本内容。"""
    return run_officecli(["view", file, "text"])

TOOLS = [
    StructuredTool.from_function(word_create, args_schema=WordCreateInput),
    StructuredTool.from_function(word_add, args_schema=WordAddInput),
    StructuredTool.from_function(word_view, args_schema=WordViewInput),
]