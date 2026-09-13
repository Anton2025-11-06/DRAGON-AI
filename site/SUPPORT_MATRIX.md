# DRAGON-AI common_model 模型能力实测矩阵

> 本矩阵由 2026-09-12 对两家真实 API 逐类型实测得出（`site/_probe_stage*.py`），
> 作为 `common_model` 供应商/类型注册（动态发现）的依据，**不硬编码**支持关系：
> 只有实际能被某供应商子类成功调用的 (类型, 供应商) 组合才会 `@register`。

## 端点与凭证基线
- 智谱 OpenAI 兼容基址：`https://open.bigmodel.cn/api/paas/v4/`
- 通义 兼容模式：`https://dashscope.aliyuncs.com/compatible-mode/v1`
- 通义 原生：`https://dashscope.aliyuncs.com/api/v1`（rerank / wanx 异步 / multimodal-embedding / paraformer / qwen-tts）
- SDK 选型：**统一 openai SDK(AsyncOpenAI) + httpx 连接池**；智谱**放弃官方 zai-sdk/zhipuai**（内部全同步、性能差）。

## 支持矩阵（✓=实测通过）

| 模型类型 (code) | stream | 智谱 zhipu | 通义 dashscope | openai(通用兼容客户端) |
|---|---|---|---|---|
| 文生文 text_to_text | 是 | ✓ glm-4-flash | ✓ qwen-turbo | ✓（指向任一兼容端点） |
| 文本向量 text_embedding | 否 | ✓ embedding-3 (2048) | ✓ text-embedding-v3 (1024) | ✓ |
| 文本重排 text_rerank | 否 | ✓ POST /v4/rerank {model:rerank} | ✓ 原生 gte-rerank-v2 | ✗ 非 OpenAI 标准端点 |
| 图片向量 image_embedding | 否 | ✗ 无端点(404/模型不存在) | ✓ multimodal-embedding-v1 (1024) | ✗ |
| 文生图 text_to_image | 否 | ✓ cogview-3-flash /images/generations | ✓ wanx2.1-t2i-turbo 异步 | ✓ /images/generations |
| 音频转文字 audio_to_text | 否 | ✓ glm-asr /audio/transcriptions | ✓ paraformer-v2 异步 | ✓ /audio/transcriptions |
| 图片理解 image_understand | 是 | ✓ glm-4v-flash | ✓ qwen-vl-plus | ✓ chat + image_url |
| 视频理解 video_understand | 是 | ✓ glm-4v-plus | ✓ qwen-vl-plus/max | ✓ chat + video_url |
| OCR ocr | 否 | ✓ glm-ocr / glm-4v | ✓ qwen-vl-ocr | ✓ chat + image_url |
| 图生视频 image_to_video | 否 | ✓ cogvideox-flash(异步) first_frame | ✓ wanx2.1-i2v-turbo(异步) | ✗ |
| 文生视频 text_to_video | 否 | ✓ cogvideox-flash(异步) | ✓ wanx2.1-t2v-turbo(异步) | ✗ |
| 文生音频 text_to_audio | 否 | ✓ glm-tts voice=female | ✓ qwen-tts voice=Cherry | ✓ /audio/speech |

## 异步任务（通义/智谱 视频、通义图片生成为「提交-轮询」范式）
- 通义：submit 返回 `output.task_id` → 轮询 `GET /api/v1/tasks/{task_id}` 至 `SUCCEEDED`。
- 智谱：`POST /v4/videos/generations` 返回 `task_id` → `GET /v4/videos/generations?task_id=xxx` 轮询至 `status=OK`。

## 统计
- 智谱支持 **11/12**（缺图片向量）；通义支持 **12/12**；openai 通用客户端覆盖 OpenAI 标准子集（文生文/向量/图片理解/OCR/文生图/ASR/TTS）。

## 接入性能基准（`site/_perf_bench.py`，并发20×3轮，qwen 兼容模式 chat）
| 方式 | 平均墙钟 | 平均QPS | 时延均 | P95 |
|---|---|---|---|---|
| AsyncOpenAI SDK | 0.42s | 48.0 | 318ms | 422ms |
| httpx 连接池直连 | 0.38s | 52.6 | 311ms | 438ms |

**结论（选型依据）**：两者共用同一 keep-alive 连接池，服务端网络时延（~300ms）占绝对主导；
http_pool 仅比 SDK 快约 10%（封装/对象构造开销），差异接近噪声量级。据此：
- **OpenAI 兼容协议（chat/embedding/images/audio）**：沿用 **AsyncOpenAI SDK** —— 与实测差距可忽略，
  换来更强的协议兼容/错误处理/流式解析与可维护性；客户端按 (base_url,api_key) `lru_cache` 复用连接池。
- **非 OpenAI 标准端点（智谱/通义 rerank、通义 multimodal-embedding、wanx/paraformer 异步任务、
  智谱 cogvideox 异步视频）**：只能走 **http_pool 直连**（SDK 无对应方法）。
- 网关热路径如需极致吞吐，可在 SDK 之上直接换 http_pool（接口已统一，切换成本低）。
