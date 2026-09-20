# 基于 MCP 的 AI 办公助手 —— 项目全记录

> 一个从零搭建的 Agent 项目：手写 ReAct 循环 → 手写 MCP 客户端 → LangGraph 状态机 → 完整评测体系。
> 本文档记录所有核心概念、架构演进、调试案例和面试要点。

---

## 一、项目一句话介绍

"我做了一个基于 MCP 协议的 AI 办公自动化 Agent：通过自实现的 MCP 客户端接入两个工具服务（Office 文档操作 + 通讯录查询），用 LangGraph 构建 planner→executor→reviewer→repairer 状态机，支持断点续跑和人工审批，最后搭了一套 20 任务的自动化评测体系做消融实验，量化各模块对成功率的贡献。"

**技术栈**：Python、MCP 协议（JSON-RPC 2.0 over stdio）、LangChain / LangGraph、FastAPI、通义千问 qwen-plus（DashScope OpenAI 兼容接口）、pytest。

---

## 二、核心概念速查

### 2.1 MCP（Model Context Protocol）

- **是什么**：Anthropic 提出的开放协议，标准化"大模型 ↔ 外部工具/数据"的连接方式。类比：USB-C 接口——以前每个设备一根专用线，现在一个接口走天下。
- **架构**：Client-Server。Agent 侧是 Client，工具提供方是 Server。
- **三种原语**：
  - **Tools**：可执行的函数（最常用，如"创建文档"）
  - **Resources**：可读的数据（如文件内容、数据库记录）
  - **Prompts**：预置的提示词模板
- **两种传输**：stdio（本地子进程，本项目用）、HTTP/SSE（远程服务）
- **握手流程**（必须能默写）：
  ```
  Client                          Server
    │ ── initialize ──────────────> │  （协议版本、能力协商）
    │ <── server_info + capabilities │
    │ ── notifications/initialized → │  （纯通知，无 id，不需要回复）
    │ ── tools/list ──────────────> │
    │ <── 工具列表（name/description/inputSchema）│
    │ ── tools/call ──────────────> │
    │ <── 执行结果                  │
  ```
- **报文格式**：JSON-RPC 2.0，每条消息带 `jsonrpc/id/method/params`，响应带相同 `id` 配对。

### 2.2 ReAct 模式

- **是什么**：Reasoning + Acting，让模型在"思考→行动→观察"循环中完成任务。
- **循环结构**：Thought（模型想）→ Action（调工具）→ Observation（工具返回）→ 再 Thought……直到模型认为完成。
- **本质**：`messages` 列表就是轨迹（trajectory），每轮把工具结果以 `role: "tool"` 的消息追加回去，模型基于完整历史决策下一步。
- **关键细节**：`tool_call_id` 必须一一配对，否则 API 报 400。

### 2.3 LangGraph

- **是什么**：把 Agent 建模为**状态机/图**，节点是函数，边是转移规则。
- **核心概念**：
  - `StateGraph` + `TypedDict`：定义全局状态
  - **Reducer**：`Annotated[list, operator.add]`——节点返回的 list 是"追加"而非"覆盖"
  - **条件边**：根据状态动态决定下一节点（如 reviewer 判 NO → repairer）
  - **Checkpointer**：每步自动存档（MemorySaver 内存 / SqliteSaver 落盘），支持断点续跑
  - **Human-in-the-loop**：`interrupt_before=["executor"]` 在关键节点前暂停，人工审批后 `app.invoke(None, config)` 从断点继续

### 2.4 三种 Agent 架构对比（本项目亲自实现了前两种）

| 架构 | 控制流 | 优点 | 缺点 |
|---|---|---|---|
| 手写 ReAct 循环 | 模型自由发挥，一个 while 循环 | 简单、灵活、透明 | 无规划、无质检、错了没人纠 |
| LangGraph 状态机 | 显式节点 + 条件边 | 职责分离、可质检、可修复、可中断 | 延迟和 token 开销大 |
| 多 Agent（如 CrewAI/AutoGen） | 多个角色对话协作 | 复杂任务分工 | 调试难、成本高、行为不可控 |

---

## 三、系统架构（四天演进）

### Day 1-2：手写 ReAct + 手写 MCP 客户端

```
用户 → agent_loop.py（ReAct 循环）
         ↓ tool_map
      tools_adapter.py（MCP 桥接层）
         ↓ 动态加载
      mcp_client.py（手写 JSON-RPC stdio 客户端）
         ↓ 子进程
      ┌── officecli（Office 文档 CLI，1 个工具 + command 参数）
      └── mcp_servers.py（通讯录服务，query_contact）
```

**亮点设计**：
- `json_schema_to_pydantic`：把 MCP 工具的 JSON Schema 动态转成 Pydantic 模型（`create_model`），让 LangChain 能 bind_tools
- `make_func(c, tool_name)` 工厂函数：把 client 和工具名绑定进闭包
- 工具描述工程：officecli 只有一个 `officecli` 工具，description 里写满用法和 SOP（validate → issues → 截图审计），**description 质量 = 工具选择准确率**

### Day 3：LangGraph 状态机

```
planner（拆任务为步骤清单）
   ↓
executor（ReAct 循环执行全部步骤，最多 10 轮）
   ↓
reviewer（三层质检）
   ├─ 通过 → END
   ├─ 通过但有警告 → END
   └─ 不通过 → repairer（带着错误上下文重试，retry_count >= 3 强制结束）
```

**reviewer 三层质检**（能程序化判定的绝不交给 LLM）：
1. `last_tool_error` 非空 → 直接不通过
2. 没有任何工具执行记录 / 只有查询没有写操作 → 直接不通过
3. LLM-as-Judge：给任务描述 + 最近 6 条工具记录 + 明确 rubric，只答 YES/NO

### Day 4：评测体系

```
eval_dataset.json（20 任务：basic 8 / advanced 6 / complex 4 / 跨服务 2）
evaluator.py（程序化验收：文件存在性、内容包含/排除、最小长度、额外表）
prepare.py（用 officecli 造测试数据 data.xlsx）
run_ablation.py（三组消融：baseline ReAct / 状态机 / 状态机+HITL）
```

**方法论要点**：
- 每个任务跑 3 次（repeat=3）抗随机性
- 每次运行用带时间戳的独立工作目录，互不污染
- 任务注入绝对路径（`所有文件操作都在目录 {workdir} 中进行`）
- 结果增量写 results.json，中断不丢
- 导入全部放模块顶层（fail fast）

---

## 四、调试案例库（面试核心弹药）

### 案例 1：模型输出残缺 JSON —— 闭合补全兜底

- **现象**：qwen-plus 生成工具参数时系统性丢结尾的 `}`，如 `{"command": ["create", "D:\\...\\out.docx"]`。LangChain 将其归入 `invalid_tool_calls`，若把残缺消息回传 API 会 400；让模型重试，**重试一百次还是丢**。
- **根因**：模型在"长字符串数组"参数上的生成缺陷，不是 prompt 能根治的。
- **修法**：确定性兜底——候选补全 `raw / raw+"}" / raw+"]}" / raw+'"]}'` 逐个 `json.loads`，修好后直接执行，不依赖模型重试。
- **面试讲法**："对 LLM 输出做防御性解析。模型的系统性缺陷不要用重试对抗，要用确定性代码兜底。这也是 LLM 工程化的通用原则：概率问题用确定性手段解。"

### 案例 2：reviewer 误杀与放水 —— LLM-as-Judge 必须有 rubric

- **现象 A（误杀）**：任务全成功（validate 通过、0 issues），reviewer 判 NO，白白触发两轮修复，耗时翻 4 倍。
- **现象 B（放水）**：只建了个空文档 / 只查了邮箱没写文件，reviewer 判 YES。
- **根因**：judge 没拿到任务描述、没有明确判定标准，只能"凭感觉打分"。
- **修法**：① prompt 里给任务原文 + 工具记录 + 三条明确 rubric；② 加程序化前置层——记录里没有"Created/Added/Updated"等写操作痕迹直接判 NO，**根本不问 LLM**。
- **面试讲法**："LLM-as-Judge 不给 rubric，判定结果就是随机的。而且能程序化判定的绝不交给 LLM——程序化层在前，LLM 只处理程序化判不了的模糊地带。"

### 案例 3：planner 虚构工具能力

- **现象**：任务是查邮箱写文档，planner 规划成"查询员工的姓名、部门、职位"，但 query_contact 只有张三李四两个人、只返回邮箱。executor 查了 10 个人、8 个"未找到"，死循环。
- **修法**：planner prompt 显式约束"计划不能超出工具实际能力"；评测任务措辞与工具能力对齐。
- **面试讲法**："planner 的幻觉比 executor 更危险——计划错了，执行得再好也是错的方向。要在 planner 的上下文里注入工具能力边界。"

### 案例 4：闭包 late-binding 陷阱

- **现象**：循环里 `lambda: client.call_tool(name, args)` 注册多个工具，运行时所有工具都调用最后一个。
- **修法**：工厂函数 `make_func(c, tool_name)` 把循环变量绑定为参数默认值。
- **面试讲法**：Python 经典坑，闭包捕获的是变量引用不是值。

### 案例 5：Windows 环境三连坑

- **GBK 编码**：subprocess 读中文输出 UnicodeDecodeError → 所有 Popen 加 `encoding="utf-8", errors="replace"`。
- **os.chdir 无效**：`os.chdir` 不影响已 spawn 的子进程 CWD → 改为任务注入绝对路径。
- **相对路径陷阱**：CWD 切到 eval/ 后 `"mcp_servers.py"` 找不到 → 全部改绝对路径常量 `PROJECT_ROOT`。
- **面试讲法**："工程上我的原则是 fail fast + 路径绝对化，环境依赖全部显式化。"

### 案例 6：超时错误要"会说话"

- **现象**：评测每个任务卡 30 秒报 `_queue.Empty`，看不出是子进程死了还是慢。
- **修法**：超时分支里先 `proc.poll()` 检查子进程是否已退出，退了就把 stderr 读出来一起抛出：
  ```python
  except queue.Empty:
      if self.proc.poll() is not None:
          raise RuntimeError(f"子进程已退出(码 {self.proc.returncode}):\n{self.proc.stderr.read()}")
      raise TimeoutError("30秒无响应（进程还活着但不应答）")
  ```
- **面试讲法**："错误信息的设计也是工程能力——一个好错误信息能省半小时排查。"

### 案例 7：import 时执行副作用

- **现象**：`import graph` 就触发了 HITL 演示；`from graph import app` 报 ImportError（`app = build_app()` 缩进进了 `__main__` 块）。
- **修法**：演示代码包 `if __name__ == "__main__":`；模块级对象定义放顶层。
- **面试讲法**："模块 import 必须零副作用，这是可测试性的前提。"

### 案例 8：模型的自我纠正能力（正面案例）

- **现象**：executor 连续失败后，模型主动调用 `load_skill word` 加载 officecli 的技能文档，加载完立刻会用正确参数了，一轮通过。
- **启示**：工具提供"按需加载的使用手册"是有效的纠错机制；可以在 executor 里工程化为"连续 2 次工具报错自动注入 load_skill"。

---

## 五、工具链速查

### officecli 真实语法（教程里的假语法害过人）

```bash
officecli create <file>                      # 创建（已存在报错，--force 覆盖）
officecli add <file> /body --type paragraph --prop text=内容
officecli set <file> /Sheet1/A1 --prop value=123
officecli view <file> text                   # 提取文本（评测验收用）
officecli view <file> issues                 # 格式问题检查
officecli validate <file>                    # 结构校验
officecli load_skill word                    # 加载 docx 技能文档
```

### 环境激活（PowerShell）

```powershell
# 教程的 source 是 Linux 命令，Windows 用：
venv\Scripts\Activate.ps1
# 如遇执行策略限制：
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 防电脑睡眠（长时间跑评测）

```powershell
powercfg /change standby-timeout-ac 0
powercfg /change monitor-timeout-ac 30
```

---

## 六、面试预判问答

**Q：为什么用 MCP 而不是直接 function calling？**
A：function calling 是模型 API 层的能力，工具定义散落在各应用里；MCP 把工具抽象成独立服务，一次开发处处接入，协议层标准化了握手、发现（tools/list）、调用（tools/call）。我的项目里同一套 MCP 客户端代码不加修改就接了两个完全不同的 server。

**Q：你的 MCP 客户端是自己写的？难点在哪？**
A：是，手写 JSON-RPC 2.0 over stdio。难点三个：① 异步读写——子进程 stdout 用独立线程读，按 id 分发到对应队列；② 通知和响应的区分（initialized 通知没有 id）；③ 错误诊断——超时时区分"进程死了"和"进程活着但不应答"。

**Q：LangGraph 比普通 ReAct 好在哪？有数据吗？**
A：有，而且数据先打了我的脸再给了我答案。我建了两套任务集（v1：20 个短任务；v2：15 个长链路任务），每组各跑 3 次：

| 组别 | v1 短任务 | v2 长链路 |
|---|---|---|
| baseline 裸 ReAct | 95.0% | 88.9% |
| 状态机（初版接口） | 83.3% | 60.0% |
| 状态机（v3 修复后） | — | **93.3%** |

初版状态机全面落败，差距在长任务上拉大到 29 个百分点。我对全部失败 run 做了归因，发现瓶颈不在节点本身而在**三个信息接口**：planner→executor 丢约束（路径、格式前缀被转述丢失）、reviewer→repairer 的 NO 不带修复方向（修复轮盲修）、验收看工具回执不看产物实际内容。v3 针对性修复后反超 baseline 4.4 个百分点。结论：**架构的可靠性取决于环节间的信息传递质量，每个 LLM 环节都必须用数据证明它值得存在**。状态机的真实价值还不止成功率——它内置了参数修复（吸收了 177 次模型残缺 JSON 输出）、HITL 审批和断点续跑，这些是裸循环结构上给不了的；代价是延迟约为 baseline 的 2~3 倍。

**Q：HITL 怎么实现的？**
A：LangGraph 的 Checkpointer 每步自动存档到 SqliteSaver，`interrupt_before=["executor"]` 让图在执行前暂停，人工批准后带同一个 thread_id 调 `invoke(None, config)` 从断点继续。本质是"可持久化的状态机 + 断点恢复"。

**Q：评测怎么保证可信度？**
A：四点：① 验收全部程序化（文件存在、内容匹配、officecli view 提取文本比对），不依赖 LLM 主观判断；② 每任务重复 3 次报成功率而非单次成败；③ 每次运行独立带时间戳的工作目录隔离副作用；④ 失败案例全部人工归因分类。

**Q：遇到的最难的问题？**
A：（讲案例 1，残缺 JSON）关键是定位方法：先打印原始 args 分类残缺形态（缺闭合/截断/引号风格），确认是模型系统性缺陷后，放弃"教模型写对"，改用确定性补全兜底。工程上要分清"概率性问题"和"确定性问题"。

---

## 七、经验原则（可以刻进脑子里）

1. **模型的系统性缺陷，用确定性代码兜底，不要用重试对抗。**
2. **能程序化判定的，绝不交给 LLM。**
3. **LLM-as-Judge 必须给 rubric，否则判定是随机的。**
4. **planner 的幻觉比 executor 更危险——方向错了，执行再好也白搭。**
5. **工具 description 是 prompt 工程的一部分，质量直接决定工具选择准确率。**
6. **fail fast：导入放顶层、路径绝对化、错误信息要带上下文。**
7. **模块 import 零副作用，演示代码包 `__main__`。**
8. **评测先行：没有量化数据，"改进了"只是感觉。**
9. **多节点系统的瓶颈常在节点间的信息接口而非节点本身；验收要看产物实际内容，不能只看工具回执。**
