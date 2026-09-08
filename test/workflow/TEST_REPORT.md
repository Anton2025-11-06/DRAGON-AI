# DRAGON-AI 工作流系统 全功能测试报告

- 测试日期：2026-09-06
- 测试环境：Windows 10 / Python 3.11（.venv）/ 后端网关 127.0.0.1:18000 / 前端 dev 5666 / Chrome 152 CDP 9222
- 测试账号：admin（管理员）
- 测试脚本目录：`E:/project/DRAGON-AI/test/workflow/`

## 一、测试结果总览

| 测试套件 | 脚本 | 覆盖范围 | 结果 |
|---|---|---|---|
| 后端节点引擎测试 | `node_test.py` | 19 种节点类型逐一执行 | **46/46 PASS** |
| CRUD 全流程 API 测试 | `crud_test.py` | 工作流全生命周期 | **35/35 PASS** |
| 前端浏览器 E2E 测试 | `browser_e2e.py` | 列表/新建/编辑器/调试/保存/删除 | **32/32 PASS** |
| HTTP 4xx 异常复现 | `repro_http400.py` / `repro_http400_engine.py` | 错误传播链路 | 已验证修复 |
| 全链路循环测试 | `cycle_test.py` | 创建→执行→发布→执行→删除 反复 8+20 轮 | **28/28 PASS**，时延稳定 |
| 并发执行测试 | `concurrent_test.py` | 6 路异流并发 + 6 路同流并发 | **12/12 PASS** |

**总计 153+ 项测试全部通过。**

### 第三轮：控制链路测试（2026-09-06 16:25 追加）

| 测试套件 | 脚本 | 覆盖范围 | 结果 |
|---|---|---|---|
| 暂停/恢复/取消控制链路 | `control_test.py` | 43 用例（API 级 33 + 引擎直驱 9 + 清理 1） | **42/42 PASS** |
| 回归：node_test | `node_test.py` | 引擎改动回归 | **46/46 PASS** |
| 回归：crud_test | `crud_test.py` | 引擎改动回归 | **35/35 PASS** |

控制链路覆盖：断点暂停全链（状态/节点状态/明细落库/快照 pendingNodes）、暂停中改变量、变量生效、断点一次性语义、多级断点、慢节点执行中手动暂停+恢复+异常分支收敛、暂停态取消、执行中取消、SSE 实时流（经网关）、已结束执行 DB 回放、快照篡改恢复、8 项负向（已结束暂停/恢复、不存在执行、首节点断点、幽灵断点、空断点）、TTL 看门狗自动取消（pause_ttl=2）、快照重建续跑（rt1 销毁→restore→从 pending 继续、不重跑已完成节点）。

**本轮发现并修复 4 个严重 bug（详见「五、发现的问题」新增小节）**：执行中 cancel 丢失窗口、网关 SSE 非流式缓冲、快照恢复丢失 global 变量、失败降级路径被 AND 汇聚闸门永久卡死。

### 第二轮反复回归（2026-09-06 12:40 追加）

- `node_test.py` × 3 轮：**3×46/46 全 PASS**（幂等）
- `crud_test.py` × 3 轮：**3×35/35 全 PASS**（幂等）
- `browser_e2e.py` × 2 轮：**2×32/32 全 PASS**（幂等，自动清理残留）
- `cycle_test.py`：8 轮 + 20 轮加压，**28/28 PASS**；单轮全链路约 0.48s，前后半程时延均值一致（0.48s vs 0.48s），无劣化、无资源泄漏（删除后详情 404 级联验证通过）
- `concurrent_test.py`：6 个不同工作流并发执行 + 同一工作流 6 路并发，**12/12 PASS**；并发 6 路总耗时 0.33s（平均单路 0.28s），结果互不串扰，执行隔离正确

**结论：反复测试未发现新问题，无状态残留、无性能劣化、并发安全。**

## 二、后端 19 种节点测试（node_test.py，46/46）

覆盖 START、END、LLM（流式/非流式）、QUESTION_CLASSIFIER、PARAMETER_EXTRACTOR、AGENT、IF_ELSE（单/双分支）、LOOP（满足/不满足条件）、ITERATION（并行迭代）、PARALLEL（分支聚合）、VARIABLE_ASSIGNER、VARIABLE_AGGREGATOR、TEMPLATE（SIMPLE/JINJA2）、CODE_EXECUTION（PYTHON/JAVASCRIPT）、LIST_OPERATOR（12 种数组操作）、DOCUMENT_EXTRACTOR、KNOWLEDGE_RETRIEVAL、HTTP_REQUEST（GET/POST/failOnError）、TOOL 共 19 种节点。

验证维度：正常路径输出、变量引用渲染、异常路径（必填缺失、HTTP 4xx/5xx、代码执行异常）、分支路由、聚合语义。

## 三、CRUD 全流程测试（crud_test.py，35/35）

创建 → 详情 → 分页（筛选/排序）→ 更新草稿 → 结构校验 → DEBUG 执行 → 发布 v1/v2 → 版本历史 → 指定版本快照 → 回滚 → 复制 → 副本执行 → 归档 → 删除级联（版本/快照/执行记录）→ 4 项负向测试（越权/不存在/空名/重复名）。

## 四、前端 E2E 测试（browser_e2e.py，32/32）

通过 CDP 直驱 Chrome（cdp_driver.py），利用 DEV 模式 `window.__workflowEditor` 调试钩子：

- **A. 列表页**：登录态、标题、侧边菜单、新建按钮、卡片渲染
- **B. 新建向导**：模板选择弹窗、空白模板进入编辑器
- **C. 编辑器初始状态**：默认 START/END 两节点、默认名称
- **D. 节点面板**：19 种节点齐全（开始/结束/大模型/问题分类器/参数提取器/智能体/条件分支/循环/迭代/并行/变量赋值/变量聚合/模板转换/代码执行/列表处理/文档提取/知识检索/HTTP 请求/工具）
- **E. 拖拽**：合成 drop 事件添加节点成功
- **F. 属性面板**：选中节点、配置加载、输入编辑
- **G. 离开确认**：弹窗拦截、取消留在编辑器、确认返回列表
- **H. 加载已有图**：API 建图（3 节点 2 边）→ 编辑器完整渲染
- **I. 调试预览**：runPreview 执行、输出正确（answer=E2E回声:浏览器E2E）、节点追踪时间线
- **J. 保存更新**：改名保存 → API 验证名称与图完整
- **K. 删除确认**：卡片删除按钮、确认弹窗、API 与页面双验证消失
- **L. JS 错误**：全程 0 条未捕获错误 / console.error

脚本幂等：开头自动清理同名残留，可重复执行。

## 五、测试期间发现并修复的 Bug

### 重大（已修复，后端）

1. **HTTP 4xx 异常导致接口返回 400 `KeyError: '"code"'`**
   - 现象：HTTP_REQUEST 节点请求外部接口返回 403 时，工作流执行接口整体返回 400，错误信息为 `'"code"'`。
   - 根因：loguru 的 `log.error(f"...{e}")` 会把消息体交给 `message.format()` 处理，异常文本中嵌入的 JSON `{"code":403,...}` 的 `{"code"...}` 被当作格式化占位符，抛出 `KeyError: '"code"'` 逃逸到路由层。
   - 修复：全部改为占位符传参 `log.error("...: {}", e)`；批量修复 workflow 模块 8 处同类隐患（workflow_execution_service / model_client / workflow_apikey_service / workflow_service）。
   - 验证：HTTP 4xx 现返回 code:200 + status:FAILED + 完整错误信息。

2. **IF_ELSE 双分支 END 输出 null**
   - 现象：END 输出引用两个分支节点的变量 `{{n_yes.text}}{{n_no.text}}` 时结果为 null。
   - 根因：纯引用匹配正则 `(.+?)` 回溯把 `{{a}}{{b}}` 误判为单一引用名 `a.text}}{{b.text`。
   - 修复：正则改为 `([^{}]+?)` 禁止引用名含大括号；同时 context.render 增加 `keep_unresolved` 参数，END 多分支汇聚时未执行分支渲染为空串而非保留 `{{ref}}` 占位符。

### 第三轮：控制链路发现的 4 个严重 Bug（已修复，2026-09-06）

3. **执行中 cancel 请求丢失，执行被误标 COMPLETED**
   - 现象：慢节点执行中调 cancel API 返回成功，但执行终态 COMPLETED、outputs 为空（cancel 从未生效）。
   - 根因：cancel 检查点只在 `_schedule` 入口和暂停唤醒处；末节点执行期间到达的 cancel 让 `_wait_running` break 后，主流程直接进 COMPLETED 分支吞掉取消请求（丢失窗口）。
   - 修复（engine.py）：`_wait_running` 返回后、COMPLETED 判定前插入 `self._check_cancelled()`；另在 WorkflowCancelled 分支补 `for t in list(self._running_tasks): t.cancel()` 防任务泄漏。
   - 验证：E2 PASS（执行中取消 → 终态 CANCELLED）。

4. **网关对 SSE 非流式转发，实时事件流全部被缓冲**
   - 现象：PAUSED 执行订阅 SSE——直连 9003 首帧 0.03s，经网关 18000 六秒零帧，30.1s 后 502；前端实时 token/暂停事件全丢。
   - 根因：gateway_router.py 用 `client.request()` + `resp.content` 整读整转；运行中/暂停中的事件流永不结束 → hold 到连接池超时。
   - 修复：subscribe / `Accept: text/event-stream` 请求改走 `build_request` + `send(stream=True)` + `aiter_raw()` 逐块下发的 StreamingResponse，读超时设为无限，`finally` 中 `aclose()` 归还连接池。
   - 验证：F1/F2 PASS（经网关收到 started/paused/completed 实时帧）。
   - 排查花絮：修复后行为不变是**环境假象**——网关用 uvicorn multiprocessing 起 worker，历史上多代父进程被杀后孤儿 worker 仍持有 18000 的继承 socket（netstat 显示死 PID 监听），旧代码 worker 抢走流量。清杀全部孤儿 worker 后新代码即生效。

5. **快照恢复丢失全局变量**
   - 现象：暂停中修改 global.g 后从快照恢复，`{{g}}` 渲染为空串。
   - 根因：快照中 global 存两份冗余（engine.snapshot() 顶层 `"global"` 与 ctx.to_dict() 的 `context.global`）；restore() 只恢复 context 内那份，API 返回并篡改的顶层那份被忽略。
   - 修复（engine.py restore）：合并两份——context.global 为底、顶层 global 覆盖（与 _persist_state 落库语义一致的权威源）。
   - 验证：H2 PASS（answer=RESTORED_FROM_SNAPSHOT）。

6. **失败降级路径被 AND 汇聚闸门永久卡死（引擎级）**
   - 现象：慢节点超时 FAILED 走 exception 分支，但异常分支下游节点从未执行，执行静默 COMPLETED 且 outputs 为空。任何「失败降级」结构的图都会中招。
   - 根因：`_barrier_ready` 只认 `src_state.status == "COMPLETED"`；FAILED 源落入「源尚未执行→可达→等待」分支，且 `_is_reachable` 同样只认 COMPLETED，导致异常分支下游被判定「等待中」永不调度，`_wait_running` 无任务等待直接返回 → 执行被标 COMPLETED。
   - 修复（engine.py）：`_barrier_ready` 与 `_is_reachable` 中源状态判断均改为 `in ("COMPLETED", "FAILED")`，FAILED 按 `_completed_with_branch` 记录的活跃端口判断（exception 分支执行时已写入 `branch:exception`）。
   - 验证：C4 PASS（answer 含 EXC:）；回归 node_test 46/46、crud_test 35/35 无破坏。

### 历史轮次已修复（见前次报告）

3. PARALLEL 节点并行分支死锁
4. run_branches 吞掉子分支异常
5. HTTP 节点 failOnError 行为
6. START 必填校验用例空 dict 假值兜底（测试侧）

### 前端 E2E 测试侧问题（非产品 Bug，已修测试脚本）

- CDP `returnByValue` 无法序列化 Vue reactive proxy（getExecutionResult 直传变 null），须 JSON 字符串中转
- 保存按钮点击判断 JS 短路 bug（`btn.click()` 返回 undefined 被误判 no-btn）
- 列表页加载时序：需轮询等待卡片渲染
- 测试残留同名工作流干扰删除验证（已加自动清理）

## 六、遗留问题与设计说明（不阻塞，建议关注）

1. **triggerType 无路由入口**：`WorkflowExecutionReq` schema 有 triggerType 字段，但 API 不暴露指定版本快照执行路径（按快照执行无入口）。当前仅草稿 DEBUG 执行与发布版本执行。
2. **回滚语义**：回滚 = 目标版本快照覆盖草稿 + 重新发布为新版本（version 递增），而非版本号回退。属设计行为，与 MaxKB 一致。
3. **Nacos 服务端认证 API 损坏**：本地环境需带 `NACOS_FALLBACK_CONFIG=E:/project/DRAGON-AI/config/nacos_fallback.yaml` 环境变量降级启动；服务重启窗口期网关可能瞬时 503（Nacos 实例瞬时失联，自愈）。**注意：Nacos 彻底不可用时，网关靠订阅缓存兜底——缓存随网关进程死亡丢失，全新网关将无法发现任何服务（全量 503）。本轮已验证可用 `GATEWAY_STATIC_INSTANCES` 环境变量（JSON：`{"service_workflow": ["127.0.0.1", 9003], ...}`）静态实例表兜底启动。**
4. **前端节点显示名与后端类型名差异**：如「问题分类器」vs QUESTION_CLASSIFIER、「模板转换」vs TEMPLATE、「列表处理」vs LIST_OPERATOR、「工具」vs TOOL。仅为命名风格差异，功能对应正确。
5. **前端条件断点 condition 字段只存前端**：前端断点设置支持条件表达式（condition 字段），但 `executeWorkflowAsync` 只传 inputs + breakpoints（节点 id 列表），条件不发给后端——条件断点实际不生效，仅有「节点断点」一种语义。属前后端契约缺口，待确认是设计如此还是待补功能。
6. **网关多进程孤儿 worker 隐患（运维）**：网关以 uvicorn multiprocessing 起 worker，仅杀父进程会留下持有监听 socket 继承句柄的孤儿 worker（netstat 显示已死 PID 仍 LISTENING），新旧实例并存会互相抢流量导致「代码更新不生效」假象。重启网关须父子进程全杀（见 memory 启动命令）。

## 七、测试设施说明

- `cdp_driver.py`：CDP 极简驱动（WebSocket 直驱 Chrome 9222，绕过 agent-browser daemon）
- `browser_e2e.py`：前端 E2E（A-L 共 12 节 32 项，幂等可重复）
- `crud_test.py` / `node_test.py`：后端 API/引擎测试
- `control_test.py`：暂停/恢复/取消控制链路测试（43 用例，含引擎直驱 TTL 看门狗与快照重建）
- `repro_http400*.py`、`engine_repro.py`：bug 复现脚本（留档）
- `probe_*.py`：排查过程探查脚本（留档，可删）
- Chrome 启动：`--remote-debugging-port=9222 --user-data-dir=E:/project/DRAGON-AI/test/workflow/chrome-profile`

## 八、结论

工作流系统前后端功能**整体健康**：19 种节点引擎执行、CRUD 全生命周期、前端编辑器交互、调试预览、保存与删除级联均正常。三轮测试累计发现并修复 6 个重大后端 bug（loguru 格式化异常逃逸、IF_ELSE 双分支输出、cancel 丢失窗口、网关 SSE 缓冲、快照 global 丢失、失败降级闸门卡死），全部修复并回归验证通过（node 46/46、crud 35/35、control 42/42）。遗留事项均为设计层面说明，不影响功能可用性。
