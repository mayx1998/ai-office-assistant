# Day5 工程化 —— 学习总结（概念 + 调试案例 + 面试要点）

> Day1-4 把 Agent 从"能跑"做到"跑得对"：手写 ReAct → 手写 MCP 客户端 → LangGraph 状态机 → 评测体系。
> Day5 解决的是另一个问题：**"能上线"**——把实验室里的脚本变成配置化、可观测、有容错、有安全防线、能并发服务化、能打包部署的系统。
> 本文档记录 Day5 涉及的全部核心概念、真实踩坑案例和面试话术。

---

## 一、Day5 一句话介绍

"我把一个命令行 Agent 工程化成了可部署服务：所有旋钮抽到 config.yaml 实现工具即插即用；给每个图节点装'行车记录仪'（trace 落 SQLite）；按'三类错误三条路'原则实现容错；给 Agent 自生成的路径加白名单、给工具调用加上限额；用 FastAPI + 分级信号量把单任务程序变成并发批量服务；最后用 Docker 打包消灭'在我机器上是好的'。"

**面试价值定位**：Day1-4 证明"Agent 能干活"，Day5 回答的是工程面试的真正问题——"用户量上来了怎么办？线上抖一下怎么办？出事了怎么排查？"

---

## 二、核心概念速查

### 2.1 配置化 —— 把"拆机器调电路板"变成"按遥控器"

- **是什么**：所有运行时旋钮（模型名、步数上限、超时、并发度、MCP server 清单）集中到 `config.yaml`，代码启动时读取，改行为不改代码。
- **怎么读**：`from config import cfg` → `cfg()["limits"]["max_steps"]`。用全局缓存 `_CFG` 保证只读一次。
- **两条纪律**：
  - **API key 永不进配置文件**，只写环境变量名（`api_key_env: DASHSCOPE_API_KEY`），配合 .gitignore 和 `git log -p | findstr sk-` 排查历史泄露。
  - `pip freeze > requirements.txt` ——别人（或 Docker）拿到它就能复刻一模一样的环境，这是"能上线"的第一块基石。

### 2.2 Trace —— 给 Agent 装行车记录仪

- **是什么**：装饰器 `@trace_node("planner")` 给每个图节点"套壳"，运行前后自动计时并写入 SQLite，事后可按 thread_id 回放整条时间线。
- **三个前置概念**：
  - **SQLite**：一个文件就是一个数据库（traces.db），零安装，Python 自带。
  - **装饰器**：不改函数内部，在它运行前后自动加行为（这里是"计时 + 记账"）。
  - **WAL 模式**：SQLite 的并发写入模式，一行 `PRAGMA journal_mode=WAL` 照抄即可。
- **关键细节**：数据库连接用 `threading.local()` 存——**每个线程各用各的连接**，否则并发批量时会互相锁死。
- **thread_id 是 trace 的魂**：它既是 state 里的字段，也是 LangGraph checkpoint 的配置，同一个值把"哪次任务"串起来。漏写它，trace 查询就是空的。
- **进阶玩法**：`SELECT node, AVG(duration_ms) FROM traces GROUP BY node` 算出各节点耗时占比——"reviewer 占总耗时 X%"这种数据，是消融实验叙事的延续。

### 2.3 容错 —— 三类错误三条路（本节的世界观）

**最重要的认知：乱重试比不重试更糟。** qwen-plus 截断 JSON 是系统性缺陷，重试一万次还是截断。所以先给错误分类，再对症下药：

| 错误类型 | 例子 | 对策 |
|---|---|---|
| 临时性 | 429 限流、5xx、超时、断连 | 自动重试（指数退避 + 随机抖动） |
| 系统性 | JSON 截断（模型生成缺陷） | 确定性修复（闭合补全兜底） |
| 逻辑性 | 参数不对、内容缺漏 | reviewer/repairer 回路 |

- **指数退避**：第 1 次等 1s、第 2 次等 2s、第 3 次等 4s，再加随机抖动防止"retry storm"冲垮服务器。行业标准做法。
- **⚠️ 400 类错误绝不重试**：参数错了重试一万次还是错，纯烧额度。重试白名单只含：RateLimitError、APITimeoutError、APIConnectionError、InternalServerError。
- **超时后要"注销自己"**（MCP 客户端的灵魂细节）：客户端是 reader 线程按 id 分发响应。调用超时后如果不从 `_pending` 里删掉自己的 id，server 迟到的响应会留在队列里——下次调用恰好复用该 id 时就会**读到上一个请求的响应**，数据张冠李戴。`finally: self._pending.pop(rid, None)` 这行就是干这个的；reader 分发处还要配一个 else 分支丢弃"无主响应"。
- **CLI/子进程也要超时**：`subprocess.run(..., timeout=N)`，超时被强杀而不是永久挂起。
- **超时错误要"会说话"**：抛错前先 `proc.poll()` 区分"进程死了"（附 stderr）还是"活着但不应答"——一个好错误信息省半小时排查。

### 2.4 安全 —— Agent 的输出就是不可信输入

- **世界观**：Agent 会**自己生成**文件路径和命令参数，所以它的输出和用户输入一样要设防。防线既是防攻击者，也是**防 Agent 自己犯蠢**（实测：executor 曾用相对路径把文件写错目录）。
- **路径白名单**：`realpath` 把 `..\..` 和符号链接这类花招解析掉，`commonpath` 判断目标是否落在 `allowed_dirs` 内，越界抛 `PermissionError`。
- **检查位置**：在工具调用的"最后一公里"检查（make_func 包装函数里、真正发起调用之前），而不是在 LLM 侧指望 prompt 约束——**防御要落在代码上，不能落在模型的自觉上**。
- **工具调用限额**：`tool_counts` 对每个工具计数，超过 `per_tool_max_calls` 就向模型返回"已达上限，用已有信息收尾"——防模型对某个工具无限死调烧额度的最后一道闸。
- **四条防线全景**（能逐条讲）：

| 威胁 | 防线 |
|---|---|
| 路径越界 | 白名单（本节新建） |
| 命令注入 | 全项目无 `shell=True`，列表传参，shell 元字符不被解读 |
| 危险操作 | HITL：interrupt_before + 人工审批 |
| API key 泄露 | 环境变量 + .gitignore + git 历史排查 |
| 无限循环烧额度 | 工具调用限额 + max_steps |
| prompt 注入（概念题） | 工具返回内容是不可信输入不能当指令执行；高危操作靠 HITL 人工兜底 |

### 2.5 并发服务化 —— 从"一次一件事"到"取餐号模式"

- **四个前置概念**：
  - **FastAPI**：几行代码把函数变成 HTTP 接口；`/docs` 自动生成调试页面。
  - **后台任务 + 轮询**：生成 100 份合同要几分钟，HTTP 不能让人干等——立即返回 task_id（"取餐号"），调用方拿号定时来问。
  - **信号量（Semaphore）**：并发闸门，`Semaphore(4)` = 最多同时放行 4 个，其余排队，防打爆 LLM 限流。
  - **asyncio.to_thread**：LangGraph 的 `.invoke()` 是同步阻塞调用，直接放进 async 服务会堵死所有请求；`to_thread` 把它扔进线程池各跑各的。
- **分级限流（面试加分点）**：并发度不是越大越好，要按工具的资源特性分级——
  - 无状态工具（通讯录查询）→ 并发 4，压测演示用它
  - Office 写文件 → 必须串行（officecli 驱动真实 Office，本质是单实例，并发写会抢文件锁）
  - 实现：两层信号量，`sem` 控总并发、`office_sem` 把 Office 操作压成串行。
- **LangGraph 状态校验的坑**：`invoke` 的输入会按 OfficeState 的 TypedDict 校验，**10 个字段一个都不能少**，缺 `review_pass`/`human_approved` 这类"暂时用不到"的键直接 KeyError。初始化 state 时全字段给默认值。

### 2.6 Docker —— 消灭"在我机器上是好的"

- **三个词**：镜像（image）= 打包好的系统快照；容器（container）= 镜像跑起来的实例（类 vs 对象）；Dockerfile = 做镜像的菜谱；docker-compose = 一键编排。
- **Dockerfile 逐行逻辑**：基于 slim 版 Python → 先只 COPY requirements.txt 装包（**利用缓存：改代码不用重装依赖**）→ 再 COPY 全部代码 → 声明端口 → 启动命令。
- **架构决策（面试必讲）**：工具的运行时依赖决定部署拓扑——
  - **进容器**：Agent 服务、contact server（纯 Python，容器内当 stdio 子进程跑没问题）
  - **不进容器**：officecli（驱动真实 Office，Linux 容器装不了 Office）
  - 配套代码要求：**单个 MCP server 启动失败只告警跳过，不影响其他 server**——容器里没有 officecli 时服务照样起，contact 查询、进度、trace 照常演示。
  - 长期方案：MCP 从 stdio 换成 streamable HTTP transport，office server 留在 Windows 宿主机，容器里的 Agent 走网络调用——"工具即插即用"的完整形态。
- **volumes**：`./outputs:/app/outputs` 让容器里写的产物实时同步到宿主机目录——容器删了数据还在。

---

## 三、Day5 调试案例库（面试弹药，全部来自真实验证）

### 案例 1：`.with_retry()` 的返回值不是 llm —— 包装顺序陷阱

- **现象**：照指南给 `ChatOpenAI(...).with_retry(...)` 加上重试后，executor 一启动就 AttributeError：`RunnableRetry` object has no attribute `bind_tools`。
- **根因**：LangChain 的 `.with_retry()` 返回的是 `RunnableRetry` 包装器，它只暴露 invoke/batch/stream，**丢掉了原 llm 的 `bind_tools` 方法**。而 executor 需要 `llm.bind_tools(tools)`。
- **修法**：拆开两步——先 `bind_tools` 再套重试：`with_retry(llm.bind_tools(tools))`。`RunnableBinding`（bind_tools 的结果）本身也是 Runnable，可以接着 `.with_retry()`。重试逻辑收敛成一个 `with_retry(runnable)` 辅助函数供全项目复用。
- **面试讲法**："LangChain 的 Runnable 是一层包一层的洋葱结构，每包一层能力就受一层限制。遇到'包装后方法消失'，要想清楚哪步依赖哪个方法，把依赖排前面。"

### 案例 2：Windows GBK 控制台 —— `¥` 字符引发的批量任务失败

- **现象**：5 人批量生成，2 人成功 1 人失败，errors 里躺着一条 `'gbk' codec can't encode character '\xa7'/'¥'`。
- **根因**：LLM 生成的工具参数里带了薪资金额 `¥8000`，executor 打印日志时往 **GBK 编码的控制台**输出，GBK 字符集没有 `¥`，直接 UnicodeEncodeError——不是模型错了，是**打印日志这步崩了**，整个任务陪葬。
- **修法**：服务入口强制 UTF-8：
  ```python
  for _stream in (sys.stdout, sys.stderr):
      if hasattr(_stream, "reconfigure"):
          _stream.reconfigure(encoding="utf-8", errors="replace")
  ```
  与启动终端的代码页无关，一劳永逸。同类问题（✓/⚠️ 等非 GBK 字符 print）统一换成 `[OK]`/`[WARN]`。
- **面试讲法**："Windows 上跑 LLM 应用，编码是第一坑。原则：进程内显式钉死 UTF-8，不相信外部环境；日志打印这种'辅助动作'绝不能有杀死主流程的能力——也可用 errors='replace' 兜底。"

### 案例 3：`os.path.commonpath` 跨盘符抛 ValueError

- **现象**：路径白名单单测时，用 C 盘路径去匹配 D 盘白名单目录，直接抛 `ValueError: Paths don't have the same drive`。
- **根因**：`commonpath` 要求所有参数在同一盘符/文件系统内，这是文档里不显眼的行为。
- **修法**：`try/except ValueError: continue`——盘符不同必然不在白名单内，跳过即可。
- **面试讲法**："安全校验函数自己先崩了，等于没设防还多了个 bug。边界输入（跨盘符、空串、None）都要进单测。"

### 案例 4：requirements.txt 里的平台专属包杀死 Docker 构建

- **现象**：`pip freeze` 生成的 requirements.txt 含 `pywin32`，Docker（Linux）里 `pip install -r` 直接失败。
- **根因**：`pip freeze` 记录的是**本机**环境，不是**项目**依赖。本机环境混着别的项目装的包（pywin32 是 Windows 专属）。
- **修法**：删 pywin32、补 openpyxl，清单里只留项目真正 import 的包。
- **面试讲法**："pip freeze 管'复刻我的机器'，手写清单管'项目需要什么'，两回事。CI 里在干净容器装一遍 requirements，是检验清单纯度的最狠手段。"

### 案例 5：一次"部分成功"的批量任务 —— 进程即内存

- **现象**：验证时服务进程被杀，重启后查旧 task_id 返回 `unknown`；批量任务进度停在 1/5。
- **根因**：进度表 `tasks = {}` 是**内存字典**，进程死则数据亡。这在 demo 阶段是合理简化（指南明确说"内存版进度表"），但要能讲清楚生产版怎么演进：Redis/数据库 + 任务队列（Celery/RQ）。
- **面试讲法**："我这版是刻意做轻——演示和面试场景单进程内存表足够，且引出'生产环境怎么演进'的讨论，反而是加分项。能讲清'什么时候该上什么'比盲目堆组件重要。"

### 案例 6：文本替换工具"部分匹配"的教训（工程习惯）

- **现象**：一次批量替换报告 "2 edits, 1 replacement"——第二个 edit 实际没生效，靠重读文件核对才发现某行还是旧代码。
- **根因**：old 字符串里逗号后的空格数与文件实际不一致（两个空格 vs 一个空格），肉眼看起来一模一样。
- **修法**：核对工具返回的替换计数，不对劲就重读文件 ground truth 再改。
- **面试讲法**："工具说'成功'不等于真的改了——任何批量修改都要用'读回 + 运行验证'闭环。信任但验证（trust but verify）。"

---

## 四、面试预判问答

**Q：你的配置化有什么实际价值？**
A：三个。① 改行为零改码：调并发度、换模型只动 config.yaml；② 演示"工具即插即用"——MCP server 清单在 config 里，加三行就接入新工具，Agent 代码零改动；③ 部署分离：同一份代码，本地和 Docker 读不同的 allowed_dirs/超时配置。

**Q：trace 和 print 日志的区别？**
A：print 是给人看的、跑完就没、没法聚合；trace 是结构化的数据（thread_id/node/duration/detail 入库），可查询可聚合可回放——`GROUP BY node` 算平均耗时占比，直接支撑"每个环节值不值得存在"的消融叙事。print 回答"刚才发生了啥"，trace 回答"这一百次任务里瓶颈在哪"。

**Q：重试策略怎么设计的？为什么这么设计？**
A：白名单制——只对四类临时性错误重试（429/超时/断连/5xx），指数退避加随机抖动，最多 3 次。400 类绝不重试。理由是"乱重试比不重试更糟"：系统性错误（如模型截断 JSON）重试一万次还是错，纯烧额度；这类错误走确定性修复（闭合补全）。这背后是 LLM 工程的核心世界观：**概率性问题用重试解，确定性问题用代码解**。

**Q：Agent 的安全怎么保证？prompt 注入怎么办？**
A：四层。① 路径白名单：Agent 自生成的路径属不可信输入，写盘前过 realpath + commonpath 校验；② 无 shell=True，列表传参天然免疫命令注入；③ 工具调用限额防死循环烧额度；④ HITL 高危操作人工审批。prompt  injection 的标准答案：**工具返回的内容是不可信输入，不能当指令执行；真正的高危操作靠 HITL 人工兜底**。

**Q：并发怎么控制的？为什么不能全并发？**
A：分级信号量：总闸门 `sem` 限整体并发（压 LLM 限流），`office_sem` 把 Office 操作压成串行——officecli 驱动真实 Office 是单实例，并发写会抢文件锁。原则一句话：**并发度按工具的资源特性分级，不是越大越好**。无状态工具（通讯录查询）可以放心并发 4。

**Q：Docker 部署你怎么取舍的？officecli 进容器了吗？**
A：没有，这是刻意的架构决策——**工具的运行时依赖决定部署拓扑**：officecli 驱动真实 Office，Linux 容器装不了 Office。所以 Agent 服务和纯 Python 的 contact server 进容器，office server 留宿主机；代码上配套做了"单个 server 启动失败自动跳过"，容器里没有 officecli 服务照样起。长期演进方向是 MCP 换 streamable HTTP transport，office server 留在 Windows 宿主机，容器内 Agent 走网络调用——那才是"工具即插即用"的完整形态。

**Q：验证怎么做的？**
A：真实端到端：起 uvicorn → POST /batch 提交 5 人劳动合同 → 轮询进度 → 5/5 零错误 → 逐份检查 docx 内容 → 查 /traces 时间线确认节点耗时与自修复回路（reviewer 驳回 → repairer → executor 重试 → 通过）。期间还顺手验证了两条新防线：闭合补全修复日志、路径白名单单测（穿越/跨盘符拦截）。

**Q：这版离生产还有多远？**
A：三层差距，能逐层讲：① 进度表从内存字典换 Redis/DB + 任务队列；② 无鉴权，要加 API key/token；③ 单进程 uvicorn 换多 worker + 反向代理。另外 Office 串行是当前的吞吐瓶颈，解法就是上面说的 MCP HTTP 化 + office server 独立部署。

---

## 五、经验原则（Day5 新增，可刻进脑子里）

1. **三类错误三条路**：临时性→重试，系统性→确定性修复，逻辑性→质检回路。乱重试比不重试更糟。
2. **重试白名单制**：宁可漏重试一个可重试的错误，绝不多重试一个不可重试的错误——400 重试是纯烧额度。
3. **超时后要注销自己**：挂起的等待者不清除，迟到响应就会张冠李戴。
4. **Agent 自生成的内容 = 不可信输入**：路径、命令、文件内容，和用户输入同等级设防；防御落在代码上，不落在模型的自觉上。
5. **Windows 上跑 LLM 应用：进程内显式钉死 UTF-8**，不要相信外部终端的代码页。
6. **pip freeze ≠ 依赖清单**：前者复刻机器，后者声明需求；在干净容器里装一遍才算数。
7. **并发按资源特性分级**：单实例资源（Office、数据库写）必须串行，无状态工具放心并发。
8. **工具的运行时依赖决定部署拓扑**：进不进容器由工具需要什么运行时决定，不由代码写在哪决定。
9. **任何批量修改 trust but verify**：替换工具报"成功"也要读回核对，运行验证才算闭环。
10. **环境依赖全部显式化**：API key 走环境变量、路径绝对化、编码钉死——隐式依赖是线上事故的头号来源。
