# 模型接口通用化设计：语义槽 + 接口描述（Interface Schema）

> 目标：一套元数据驱动的机制，覆盖不同厂商、不同类型（文本生成 / 文生图 / 语音 / Embedding / Rerank 等）模型的参数暴露、前端渲染、请求组装与响应解析，同时保持对现有调用方式的兼容。

---

## 1. 背景与问题

### 1.1 三个使用场景

| 场景 | 现状 | 痛点 |
|---|---|---|
| 模型对话页 | 写死「深度思考 / 流式输出 / 联网搜索」三个开关，直接调 `/api/model` | ① 参数语义由前端硬编码，换厂商不可用；② 只能覆盖文本生成，文生图/语音等模型无法对话 |
| 外部模型接口直调 | `/api/model` body 原样透传 + `extra_body` 展开 | 使用者不知道有哪些参数、参数含义、body 该长什么样 |
| 模型管理 | 维护 `model_params`（JSON 透传）但不解释语义 | 管理员无法表达"该模型支持什么、怎么用" |

### 1.2 差异的本质：三个维度

1. **能力类型差异**：文本生成 / 文生图 / 语音识别 / Embedding / Rerank —— 输入输出的"语义"不同（有的是 prompt、有的是图片、有的是 query+文档）。
2. **请求结构差异**：同一语义在不同厂商的 body 里处于完全不同的物理位置。例如"用户输入"可以是：
   - OpenAI chat：`$.messages[0].content`（单层）
   - 百炼文生图：`$.input.messages[0].content[0].text`（三层，且包在 `input` 里）
   - 某些厂商：`$.prompt` 或 `$.data.prompt.text`（三层以上、key 不叫 messages）
3. **参数集合差异**：同一个"温度"参数，可能是 `temperature`、`top_p`、`params.temperature`，也可能不支持。

**结论：任何"在代码里写死 N 种格式"的方案都会被第 N+1 种厂商击穿。必须把"格式知识"从代码中剥离，变成模型上的元数据。**

---

## 2. 核心设计思想

**语义与物理位置解耦。**

- 系统定义一组**固定的语义槽（Semantic Slots）**：`prompt`、`image`、`audio`、`documents`、`query`、`output`……语义是稳定的、前端只认识语义槽。
- 每个模型通过一份**接口描述（Interface Schema）**声明：每个语义槽映射到请求 body 的哪个 **JSONPath**、支持哪些**参数（参数目录）**、响应内容在哪里提取。
- 请求流程变成：**前端提交语义化请求 → 网关按 Schema 翻译成厂商 body → 转发 → 按 Schema 解析响应**。
- 任何厂商、任何层级结构、任何 key 命名，都是"一份 JSON 描述"的事，不再需要改代码。

```
前端（只懂语义）
   │  { prompt: "画一只猫", images: [...], params: { seed: 42 } }
   ▼
翻译层（读模型的 Interface Schema，套 JSONPath）
   │  { model: "...", input: { messages: [{ role:"user", content: [{ text: "画一只猫" }] }] }, parameters: { seed: 42 } }
   ▼
厂商接口
   │  响应（任意格式）
   ▼
解析层（按 Schema 的 response 映射提取）
   │  { content: "图片URL...", reasoning: "", done: true }
   ▼
前端 / 外部调用者
```

---

## 3. 语义槽（Semantic Slots）定义

系统内置一套**固定槽位集合**（默认全量，按模型类型裁剪）。每个槽位有固定语义，前端据此渲染对应控件：

| 槽位 | 语义 | 前端控件 | 适用类型 |
|---|---|---|---|
| `prompt` | 用户主输入（文本） | 文本框 | TEXT_GEN / IMAGE_GEN / AUDIO_GEN / MULTIMODAL |
| `image` | 图片输入（多张：URL 或 base64） | 图片上传 | IMAGE_GEN（图生图）/ MULTIMODAL |
| `audio` | 音频输入 | 音频上传 | AUDIO_GEN（语音识别/合成输入） |
| `documents` | 文档/长文本输入 | 文件/文本粘贴 | RAG / RERANK / MULTIMODAL |
| `query` | 检索/排序的主查询串 | 文本框 | RERANK / EMBEDDING（query 型） |
| `passages` | 待排序/待向量化的候选列表 | JSON 文本域（外部调用为主） | RERANK / EMBEDDING（documents 型） |
| `output` | 输出设定（响应格式：text/json/image/audio） | 下拉 | 全部 |

> 槽位集合是**封闭的、可扩展的**：将来出现新输入类型，在槽位表中加一项并配一个前端控件即可，历史模型描述不受影响。
>
> 关键点：**前端永远不读厂商 body，只认槽位**。`prompt` 到底是 `messages` 还是 `input.messages[0].content[0].text`，由接口描述决定。

### 3.1 槽位与参数的辨析

- **槽位（slot）**：承载"用户业务内容"的输入/输出位置（prompt、image……），前端渲染输入控件。
- **参数（parameter）**：承载"调节行为"的键值项（temperature、seed、stream、reasoning……），前端渲染为开关/下拉/数字输入等。

两者天然区分：**内容是函数参数，参数是配置项。** 现有代码里的"深度思考 / 流式 / 联网搜索"本质上只是三个预设参数，应当退化为"参数目录里的普通项 + 厂商模板的默认映射"，而不是写死的前端逻辑。

---

## 4. 接口描述（Interface Schema）—— 一份 JSON 说清一切

存储于 `tb_model` 新增列 `interface_schema`（MySQL JSON），结构如下（完整示例）：

```jsonc
{
  "version": 1,

  // 1) 所基于的预设模板（仅作展示/回填用，翻译时以本文件为准）
  "template": "dashscope-image",

  // 2) 请求体组装
  "body": {
    // 请求体静态骨架：body.payload 与 body.sealed 合并后，再写入动态槽位/参数
    // 条件片段：满足 cond（JSONPath 存在且 truthy）才合并
    "sealed": [
      { "path": "$.input.messages[0].role", "value": "user", "cond": "$.input.messages[0]" }
    ],
    // 动态片段：运行期由翻译层填充，value 支持常量
    "payload": [
      // 语义槽绑定：prompt 槽位 → 三层结构 input.messages[0].content[0].text
      { "path": "$.input.messages[0].content[0].text", "from": "slot.prompt" },
      { "path": "$.input.messages[0].content[0].type", "value": "text" },
      // 参数绑定：前端参数目录里的 seed → 厂商位置 parameters.seed
      { "path": "$.parameters.seed", "from": "param.seed" },
      { "path": "$.parameters.prompt_extend", "from": "param.prompt_extend", "default": false }
    ]
  },

  // 3) 参数目录：前端渲染 + 外部文档 + 校验 的唯一来源
  "params": [
    {
      "key": "prompt_extend",
      "label": "提示词扩展",
      "desc": "是否自动扩写提示词，提升生图效果",
      "type": "boolean",
      "default": true,
      "group": "生成"
    },
    {
      "key": "seed",
      "label": "随机种子",
      "desc": "相同种子可复现同图",
      "type": "integer",
      "min": 0,
      "max": 2147483647,
      "group": "生成"
    },
    {
      "key": "reasoning",
      "label": "深度思考",
      "desc": "让模型先推理再回答",
      "type": "boolean",
      "default": false,
      "group": "行为"
    },
    {
      "key": "stream",
      "label": "流式输出",
      "desc": "边生成边返回（SSE）",
      "type": "boolean",
      "default": true,
      "group": "传输"
    }
  ],

  // 4) 响应解析
  "response": {
    // 各语义输出的提取路径（JSONPath）
    "content":  { "path": "$.output.choices[0].message.content" },
    "image":    { "path": "$.output.choices[0].message.image_url.url" },
    "thinking": { "path": "$.output.choices[0].message.reasoning_content" },
    "error":    { "path": "$.output.message" },
    // Embedding/Rerank 专用
    "embeddings": { "path": "$.output.embeddings" },
    "scores":     { "path": "$.output.results" },
    // 非 JSON 响应处理：text 直接透传 / url 取 Content-Location 头
    "kind": "json",
    // 流式：SSE 数据帧里的增量路径（data: {...} 解析后的相对路径）
    "stream": {
      "enabled": true,
      "deltaContent":  { "path": "$.output.choices[0].delta.content" },
      "deltaThinking": { "path": "$.output.choices[0].delta.reasoning_content" },
      // SSE data 帧格式："json" 或 "json-delta"（data: [DONE] 结束）
      "frame": "json"
    }
  }
}
```

### 4.1 JSONPath 约定（最小实现，不做完整标准）

- 仅支持两类语法，翻译层实现不超过 50 行：
  - `$.a.b[0].c` —— 对象导航 + 数组下标
  - `$.a[*].d` —— 通配数组元素（用于"每个元素都要写相同路径"的场景，如 messages 数组）
- **写入（set）**：路径不存在时**自动创建中间对象/数组**，数组中越界下标直接 append。
- **读取（get）**：路径不存在返回 undefined，不报错。
- 好处：三层、任意 key 名、任意层级嵌套（哪怕十层）都是一段路径字符串，**天然覆盖用户担心的"三层结构 / file_url / 任意 key"问题**。

### 4.2 参数目录类型系统（`params[].type`）

| type | 前端控件 | 校验 |
|---|---|---|
| `boolean` | 开关按钮 | — |
| `string` | 文本框 | maxLength |
| `enum` | 下拉（`options: [{label, value}]`） | 值域 |
| `integer` | 数字输入 | min/max |
| `number` | 数字输入（小数） | min/max |
| `json` | JSON 文本域（外部调用时原样透传） | 可解析 |
| `flag` | 可勾选按钮组（多选，`options`） | 值域 |

**"使用者不知道有哪些参数、参数含义"问题 → 参数目录就是答案**：前端渲染、外部文档、参数校验都从同一份 `params` 生成，单一事实来源。

---

## 5. 内置预设模板库（开箱即用）

管理员选"预设模板"一键回填，再微调。模板就是上面 Schema 的静态实例。首批建议 6 个：

| 模板 | 适配 | prompt 槽位物理位置 | 说明 |
|---|---|---|---|
| `openai-chat` | OpenAI / DeepSeek / Kimi 等 chat 兼容 | `$.messages[*].content` | 含 stream、reasoning（部分厂商） |
| `dashscope-image` | 百炼文生图（qwen-image） | `$.input.messages[0].content[0].text` | 三层结构 + `parameters` 参数区 |
| `dashscope-asr` | 百炼语音识别 | `$.input.file_urls[*]`（audio 槽） | 演示"key 不是 messages"的场景 |
| `openai-embedding` | OpenAI 兼容 Embedding | `$.input`（query/documents 槽） | 响应取 `$.data[*].embedding` |
| `cohere-rerank` | Cohere / Jina 兼容 Rerank | `$.query` + `$.documents[*]` | 响应取 `$.results[*].relevance_score` |
| `generic-json` | 未知厂商兜底：语义请求整体透传 | — | 当 Schema 无绑定时的最后手段 |

> 模板库只是**起步加速器**，不是硬编码分支。翻译层对模板没有任何特判，全部走 JSONPath 通用机制——`openai-chat` 与 `dashscope-image` 在代码里没有任何区别，都是"读 Schema → 填路径"。

---

## 6. 翻译层（网关）执行流程

```
POST /api/model/invoke 或 /api/model（带上 X-User-Api-Key）
  语义请求体：
  {
    "model": "qwen-image-3.0-pro",
    "slots": { "prompt": "...", "image": ["url1"], "documents": [...] },
    "params": { "seed": 42, "prompt_extend": true }
  }
```

1. **读 Schema**：按 model_id 从缓存取 `interface_schema`（与现有 ModelGatewayCache 同级缓存）。
2. **组装 body**：
   - 合并 `body.sealed`（静态骨架，条件满足才写）；
   - 遍历 `body.payload`：`from: "slot.xxx"` → 从 `slots.xxx` 取值写入 path；`from: "param.xxx"` → 从 `params.xxx` 取值写入 path（`default` 兜底）；
   - `slots`/`params` 中**未在 Schema 绑定的键**：有 `passthrough` 开关可整体并入顶层 `extra_body`（保持现有透传能力）。
3. **补全**：`model_name` 写入 Schema 声明的模型名字段（`body.modelPath`，默认 `$.model`）。
4. **转发**：上游 URL 仍用 `base_url`（保持现有规则），head 仍带管理端密钥。
5. **解析响应**：按 `response` 映射提取 content / image / thinking / embeddings / scores；流式按 `response.stream` 的增量路径逐帧翻译为前端统一 chunk（与现在 SSE 结构一致）；错误响应按 `response.error` 提取文案，fallback 到现有 OpenAI 错误结构。
6. **输出**：统一返回语义化结果结构（与前端 Flow 层约好的形状，前端完全不需要感知厂商差异）。

### 6.1 与现有 `/api/model` 的关系（兼容策略）

| 入口 | 请求体 | 适用 |
|---|---|---|
| `POST /api/model`（现状，保留） | 厂商原始 body，原样透传 | 高级用户、已接入的第三方、OpenAI SDK 兼容 |
| `POST /api/model/invoke`（新增） | 语义化请求（slots + params） | 平台自家页面、希望"只看参数大全就能调"的第三方 |

两个入口并存、共用鉴权/限流/密钥体系；`/api/model` 行为完全不变，老客户端零影响。

---

## 7. 前后端改动方案

### 7.1 模型管理（高级设置 → 新增"接口配置"Tab）

- **预设模板选择**：下拉（上文 6 个模板），选中自动回填 Schema JSON，可继续编辑。
- **Schema 编辑器**：两个层次：
  - 表单模式（推荐）：参数目录表格化（key/名称/含义/类型/默认值/分组）+ 槽位绑定下拉（prompt→路径，用带预览的路径输入框）。
  - JSON 模式：直接粘贴/编辑 JSON，实时校验（`version`/必填字段/路径语法），错误逐条列出。
- **校验**：保存前校验参数 key 唯一、路径语法合法、`params` 内已绑定的键都在目录中存在。
- **连通性测试**：现有 `test` 接口复用语义化入口跑一次最小请求（prompt="hi"），把组装出的厂商 body 展示给管理员看（所见即所得）。

### 7.2 模型对话页（动态渲染）

- 页面初始化：拉取当前模型的 `interface_schema`。
- **输入区**：按槽位渲染（`prompt` 出文本框、`image` 出上传、`audio` 出音频上传）；无 `prompt` 槽位的模型（纯 Rerank 等）不显示对话框，提示用"模型调用"页。
- **参数区**：按 `params` 目录渲染控件（boolean→开关按钮、enum→下拉、integer→数字输入……），按 group 分组折叠。**"深度思考/流式/联网搜索"不再是写死按钮，而是参数目录里出现才渲染的普通项**——彻底解决"不是所有模型都支持这三个参数"的问题。
- **发送**：组装 `{ slots, params }` 调 `/api/model/invoke`，按响应映射的语义字段渲染气泡（文本/图片预览/引用）。
- 兼容：旧模型无 Schema → 走默认模板（TEXT_GEN→openai-chat），页面表现与现在一致。

### 7.3 参数大全（解决"使用者不知道有哪些参数"）

- **入口 1（我的 API Key）**：`GET /api/system/models/my-keys` 每个 key 关联返回 `schema`（参数目录 + 槽位说明 + 一个最小请求示例），或提供 `GET /api/model/meta/{model_id}` 匿名可查（含 key 鉴权版本返回完整含敏感说明）。
- **入口 2（对话页）**：动态渲染的参数控件自带 tooltip（`label` + `desc`）。
- **入口 3（外部调用）**：`/api/model/invoke` 的 OpenAPI 文档（FastAPI 自动生成）里透出 `slots`/`params` 结构 + 每模型示例，第三方"看一眼文档就能调"。

---

## 8. 数据与接口变更清单

| 变更 | 内容 |
|---|---|
| `tb_model` | 新增 `interface_schema`（JSON，nullable）；老数据为 NULL |
| 操作日志 | 可附加 schema_key 便于审计（可选） |
| 网关缓存 | `ModelGatewayCache` 增 `interface_schema` 字段（模型详情更新时失效重建，沿用现有缓存失效机制） |
| 新接口 | `POST /api/model/invoke`（语义化入口，同 `/api/model` 鉴权/限流）/ `GET /api/model/meta/{model_id}`（参数大全） |
| 模型管理接口 | create/modify 接收 `interface_schema`；detail 返回（管理员可见） |
| my-keys | 返回各 key 的 `interface_schema`（截断敏感字段） |

---

## 9. 实施路线（建议分三期）

**一期（闭环核心）**
1. `tb_model` 加列 + 网关翻译层 + `/api/model/invoke` + `/api/model/meta`；
2. Schema JSON 完整校验器；
3. 内置 2 个模板（openai-chat、dashscope-image）并完成注册→调用→解析全链路手工验证。

**二期（前端体验）**
4. 模型管理"接口配置"Tab（表单 + JSON 双模式）；
5. 对话页动态渲染（槽位控件 + 参数目录按钮组），替换写死三开关；
6. my-keys 返回参数大全 + 对话页 tooltip 文档。

**三期（模板扩充与打磨）**
7. 补齐 asr / embedding / rerank / 语音模板；
8. 连通性测试展示组装 body；
9. 第三方调用文档页（按模型生成 OpenAI 风格文档）。

---

## 10. 边界与风险

| 风险 | 对策 |
|---|---|
| Schema 维护成本高 | 模板库 + 表单化编辑器 + 测试时展示组装结果，把成本降到"选模板改两行" |
| 管理员填错路径导致调用失败 | 保存前路径语法校验；连通性测试可回显组装 body；错误响应透出"命中的 schema 路径"帮助排错 |
| 老模型无 Schema | 默认按 category 套模板（行为不回归）；页面逻辑对"无 schema"有兜底 |
| JSONPath 实现被复杂表达式击穿 | 明确只支持导航语法（路径+下标+通配），超出即校验拒绝 |
| 安全（参数注入/伪造路径） | Schema 只做"写入已知路径"白名单，翻译层不接受调用方传入路径；`interface_schema` 仅管理员可写 |
| 性能 | Schema 走网关缓存；组装为纯内存 JSON 操作，单次开销微秒级，可忽略 |

---

## 11. 一句话总结

> **把"模型长什么样"从代码里拿出来，变成模型自己身上的一份 JSON（Interface Schema）：语义槽解决"输入是什么"，参数目录解决"能调什么"，JSONPath 绑定解决"放在哪、从哪拿"。代码永远只写一次，厂商差异永远只写 JSON。**