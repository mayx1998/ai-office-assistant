"""路径白名单：Agent 自己生成的文件路径属于"不可信输入"，写盘前必须过这道闸。
相对路径按项目根目录解析——这样仓库 clone 到任何位置都能直接跑，不用改配置。"""
import os
from config import cfg

# security.py 在项目根目录，相对白名单条目都基于它解析
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# 常见文档扩展名（大小写不敏感）
_DOC_EXTS = (".docx", ".xlsx", ".pptx")


def _resolve(d: str) -> str:
    """白名单条目可以是绝对路径（如 ``D:\\...``），也可以是相对项目根的路径（如 ``outputs``）。"""
    if not os.path.isabs(d):
        d = os.path.join(PROJECT_ROOT, d)
    return os.path.realpath(d)


def safe_path(p: str) -> str:
    """把路径解析成绝对路径后，必须落在白名单目录内，否则抛 PermissionError。
    realpath 会把 ../.. 和符号链接这类花招解析掉，commonpath 判断是否在白名单内。
    相对路径按【进程当前工作目录】解析（与命令行 cd 到的位置一致）——所以服务要从项目根启动。"""
    rp = os.path.realpath(p)
    for d in cfg()["paths"]["allowed_dirs"]:
        rd = _resolve(d)
        try:
            if os.path.commonpath([rp, rd]) == rd:
                return rp
        except ValueError:
            continue                  # 盘符不同，必然不在该白名单目录内
    raise PermissionError(f"路径越界：{p}（不在允许的工作目录内）")


def check_tool_args(arguments: dict):
    """在发起工具调用前检查参数：任何指向文档文件的路径都必须过白名单。
    递归遍历参数里所有字符串值（officecli 的参数一般是 {"command": [...]} 结构）。"""
    def walk(v):
        if isinstance(v, str):
            if v.lower().endswith(_DOC_EXTS):
                safe_path(v)          # 越界直接抛异常，调用被拒绝
        elif isinstance(v, (list, tuple)):
            for item in v:
                walk(item)
        elif isinstance(v, dict):
            for item in v.values():
                walk(item)

    walk(arguments)
