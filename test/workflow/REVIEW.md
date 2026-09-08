# MaxKB → DRAGON-AI 工作流移植 · 测试与复盘报告

日期：2026-09-05 ~ 2026-09-06
范围：service_workflow 后端（FastAPI + 引擎）+ ui-ai/web-antd 前端（Vue3 + VueFlow）
测试基线：网关 18000 / 前端 dev 5666 / 工作流服务 9003

---

## 一、测试成果总览

### 1. 后端 API 全量测试 —— 22/22 PASS
脚本：`E:/project/DRAGON-AI/test/workflow/api_test.py`

覆盖 19 组端点：登录、节点定义、工作流 CRUD、保存图、详情、校验、发布、版本历史、
同步/异步执行、执行历史、执行详情、节点明细、快照、复制、回滚、归档、删除、
分页（GET/POST/405 分支）、404 分支。

### 2. SSE 事件流验证
`GET /workflow-executions/{id}/subscribe` 返回 text/event-stream，
事件序列：workflow.started → node.started/completed ×N → workflow.completed，完整无丢失。

### 3. 浏览器端到端验证（CDP 直驱 Chrome）
- 登录（admin token 注入）→ 工作流列表 → 编辑器（节点面板 5 组 19 种节点、画布、属性面板）
- 保存/重命名 → DB 落库 DRAFT + 完整 graph
- 调试面板 → 预览运行 → execute-async 200 → SSE 订阅 → 节点追踪/变量实时展示
- **修复后回归：零 Vue 错误，执行结果正常展示**

---

## 二、发现并修复的 Bug（本轮）

### Bug 1（前端·严重）：预览运行导致 Vue 崩溃
- **现象**：点击「预览运行」后 console 报 20+ 次
  `Maximum recursive updates exceeded in component <PreviewRunner>`，
  页面无运行结果展示。后端执行与 SSE 链路均正常。
- **根因**：`DynamicInputForm.vue` 两个 deep watch 互踢形成回环——
  `watch(props.values)` → 重建 `formValues`（新对象引用）→
  `watch(formValues)` → `emit('update:values', {...})` → 父组件 v-model 更新
  → 回到起点。每次都产生新引用，watch 必然再触发，无限循环。
- **修复**：`PreviewRunner.vue` 的输入组件同步外部值前做浅比较
  （`shallowEqualValues`），内容未变化不重建对象，回环在第二轮终止。
- **回归**：修复后预览运行零错误，节点追踪 2、变量 1 正常展示。

### Bug 2（后端·严重）：Nacos 配置中心返回空配置时服务启动崩溃
- **现象**：`Service service_workflow init dependencies failed: 'NoneType' object has no attribute 'get'`
- **根因**：`common/common_app/bootstrap.py` 中 `get_config_content()` 可返回
  None（如 data_id 不存在），`app.state.config = yml_config or {}` 做了兜底，
  但后续 `_init_redis(yml_config.get(...))` 直接使用原变量。
- **修复**：归一化 `yml_config = yml_config or {}`；且配置为空但设置了
  `NACOS_FALLBACK_CONFIG` 时回落本地降级配置，保证 Redis/MySQL 可初始化。
- **回归**：空配置场景服务正常以降级配置启动，22/22 PASS 无回归。

### Bug 3（环境）：Nacos 服务端认证 API 损坏导致服务失联
- **现象**：昨晚 23:59 后 Nacos v1/v3 登录接口均 500
  （`Handle API Compatibility failed` / `No message available`），
  SDK gRPC 注册拿不到 access token；工作流服务 4 个进程僵死
  （进程在、端口不监听、注册丢失），网关全部 503「服务 service_workflow 无可用实例」。
- **处置**：
  1. 清理僵尸进程；以 `nacos_server_address=10.88.129.3:8848` +
     空凭据（跳过登录）+ `NACOS_FALLBACK_CONFIG` 重启，gRPC 直连注册成功（持久实例）；
  2. 曾用 v1 HTTP API 手工注册临时实例恢复网关转发（服务端实例 API 免认证可用）；
  3. 保底脚本 `nacos_heartbeat.py`（临时实例心跳保活，最终未启用——持久实例不需要）。
- **遗留**：Nacos 服务端认证组件待运维修复；修复后建议恢复
  `nacos_name/nacos_password` 正常凭据启动并回退降级配置依赖。

---

## 三、历史问题修复记录（前几轮，摘要）

| 问题 | 根因 | 修复 |
|---|---|---|
| 测试账号 403 无权限 | e2etest 只有 USER 角色无 permission | DB 绑定 ADMIN 角色（JWT 含 ADMIN 全放行） |
| REPLY 节点不存在 | 引擎注册表 19 种节点无 REPLY（MaxKB 指定回复未移植） | 测试图改用 START→TEMPLATE→END |
| START_NO_FIELDS 警告 + 执行 FAILED | 图契约误用 `config`/`x`/`y`/`variable` 键 | 按引擎契约改用 `data`/`position`/`name`/`defaultValue` |
| create/copy 返回 int 误判 | data 直接是新 id（int） | 测试脚本类型兼容 |
| 版本历史为空误报 | publish 图校验失败返回 HTTP 200 + body.code 400 | 统一按 `body.code==200` 判定成功 |
| agent-browser 启动失败 | 本机 Chrome 152 不写 DevToolsActivePort，daemon 拿不到随机端口 | 放弃 daemon，自研 `cdp_driver.py`（固定端口 9222 + WebSocket 直驱） |

---

## 四、架构与工程决策记录

1. **前端契约为后端唯一标准**：API 路由、字段命名、图结构序列化全部对齐
   `ui-ai/.../api/ai-workflow/index.ts` 与 `types.ts`。
2. **引擎图契约**：节点配置在 `node.data`、位置在 `node.position`；
   START 字段键 `name/defaultValue/required`；END 输出键 `outputs:[{name,value}]`。
3. **响应规范**：错误返回 HTTP 200 + body `code:400`；判定成功必须 HTTP 200 且
   `body.code==200`。
4. **执行模式**：DEBUG 走草稿图（workflowVersion=0），API/AGENT 触发走发布快照。
5. **SSE 鉴权**：前端用带 `Authorization: Bearer` 头的 sse.js（非原生 EventSource），
   经 vite 代理透传正常。
6. **测试基础设施**（统一收敛在 `E:/project/DRAGON-AI/test/workflow/`）：
   - `api_test.py` —— 后端 22 项全量断言
   - `cdp_driver.py` —— CDP 直驱浏览器（eval/nav/screenshot/inject/run）
   - `js/hook_all.js` —— 文档启动前注入：fetch/XHR 捕获 + error/unhandledrejection
     /console.error/Vue warn 捕获
   - `js/login.js`、`js/inject_token.js` —— 登录与 token 注入
     （vben 持久化键 `pmf-web-antd-5.5.9-dev-core-access`）
   - `nacos_heartbeat.py` —— Nacos 临时实例保活（备用）
   - `chrome-profile/` —— 测试专用 Chrome 用户目录

---

## 五、遗留事项

1. **Nacos 认证修复**（运维）：服务端 v1/v3 登录接口 500；当前以空凭据 + 本地降级运行。
2. **需外部依赖的节点未测**：LLM / AGENT / KNOWLEDGE_RETRIEVAL / HTTP_REQUEST / TOOL
   （需真实模型、知识库、MCP 服务）。
3. **列表分页 size 疑似不生效**：`size=50` 时 total=1 但浏览器显示 2 页——未定论，
   建议专项复测。
4. **REPLY（指定回复）节点未移植**：MaxKB 有此节点类型，引擎注册表（19 种）无。
   属有意裁剪还是遗漏，需产品确认。
5. **MaxKB 版本对齐**：基于 MaxKB 2.10.5 移植；后续 MaxKB 升级的节点/能力需跟踪。

---

## 六、结论

- 后端：22/22 API 断言通过，执行/SSE/版本/回滚链路完整。
- 前端：列表、新建、编辑器、调试面板、预览运行端到端可用；
  修复了导致预览运行崩溃的 watch 回环（本轮最关键前端 bug）。
- 服务稳定性：修复了空配置启动崩溃；经受住了 Nacos 故障切换的降级考验。
- 移植整体达到「前后端可用、契约对齐、关键路径有自动化回归」的状态。
