from typing import TypedDict, Annotated
import operator

class OfficeState(TypedDict):
    task: str                                    # 用户原始任务
    plan: list[str]                              # planner 拆解出的步骤
    messages: Annotated[list, operator.add]      # 对话+执行历史（累加语义）
    last_tool_error: str                         # 最近一次工具错误（供 repairer 分析）
    review_pass: bool                            # reviewer 产物校验结果
    retry_count: int                             # 重试计数（防死循环核心）
    human_approved: bool                         # HITL 审批结果
    report: str                                  # 最终交付说明
    review_feedback: str                         # reviewer 未通过时的诊断，喂给下一轮 executor
    thread_id: str
