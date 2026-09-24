STATUS_UN_AUTH = 403
ROP_UN_AUTH = "未登录，禁止操作！"
ROP_WITHOUT_PERMISSION = "权限不足，禁止操作！"

STATUS_UNKNOW = 500
ROP_UNKNOW = "无效的操作！"


SERVICE_SYSTEM = "service_system"
SERVICE_SYSTEM_PORT = 9001

SERVICE_RAG = "service_rag"
SERVICE_RAG_PORT = 9002

SERVICE_WORKFLOW = "service_workflow"
SERVICE_WORKFLOW_PORT = 9003

SERVICE_LOGIN = "service_login"
SERVICE_LOGIN_PORT = 9004

SERVICE_GATEWAY = "service_gateway"
SERVICE_GATEWAY_PORT = 18000

SERVICE_AGENT = "service_agent"
SERVICE_AGENT_PORT = 9005

SERVICE_SKILL = "service_skill"
SERVICE_SKILL_PORT = 9006

ARQ_WORKFLOW = 'arq_workflow'

ARQ_RAG = 'arq_rag'

ARQ_HARNESS = 'arq_harness'

PREFIX_LOGIN = "login:"

# 单位秒，8小时
TOKEN_EXPIRE = 8 * 60 * 60

# ---- 限流 ----
PREFIX_RATE_LIMIT = "rate_limit"
# 限流策略配置（hash）：field=模块桶名，value=JSON {limit, window, enabled}
PREFIX_RATE_LIMIT_CONFIG = "rate_limit_config"

# ---- AI 模型网关 ----
# 模型路由配置（hash）：field=model_id，value=JSON（模型路由/限流参数，不含管理端密钥）
PREFIX_MODEL_RATE_LIMIT_CONFIG = "model_rate_limit_config"
# api-key 映射（hash）：field=api_key，value=JSON {model_id, user_id, apply_id, create_time}
PREFIX_MODEL_RATE_LIMIT = "model_rate_limit"

# ---- 工作流 API Key（第三方 API 执行网关鉴权） ----
# 配置（hash）：field=api_key，value=JSON {workflowId, rateLimit(QPS), expireTime, status}
# 工作流服务增删改时同步写入，网关读取做鉴权与限流
PREFIX_WORKFLOW_API_KEY = "workflow_api_key_config"
# QPS 计数（双层 hash）：外层 field=api_key，内层 field=qps，value=每秒次数
PREFIX_WORKFLOW_API_KEY_QPS = "workflow_api_key_qps"
# 模型 QPS 计数（双层 hash）：外层 field=model_id，内层 field=qps，value=每秒次数
PREFIX_MODEL_RATE_LIMIT_QPS = "model_rate_limit_qps"


# 限流桶保留名（不等于可转发的模块段，不会与业务模块撞名）
BUCKET_GATEWAY = "gateway"    # /api/{无效模块} 与路径探测类垃圾流量的归口桶
BUCKET_GLOBAL = "global"      # 非 /api/** 的其余请求
# 网关自带的第一方入口段：不是业务模块，但属于正常入口，不并入 gateway 桶
# （/api/model 的限流走 api-key + 模型 QPS，见 model_proxy_router）
GATEWAY_OWN_ENTRIES = {"model"}


# 可配置的模块桶候选（前端下拉数据源，与 _bucket_of 的分桶口径对应）
# 限流单位：每个client ip 一个window + limit
MODULES = [
    {"bucket": "login", "name": "登录认证", "default": {"limit": 300, "window": 60}},
    {"bucket": "system", "name": "系统管理", "default": {"limit": 300, "window": 60}},
    {"bucket": "rag", "name": "知识库", "default": {"limit": 300, "window": 60}},
    {"bucket": "workflow", "name": "智能体编排", "default": {"limit": 300, "window": 60}},
    {"bucket": "datasets", "name": "数据集", "default": {"limit": 300, "window": 60}},
    {"bucket": "train", "name": "模型训练", "default": {"limit": 300, "window": 60}},
    {"bucket": "inference", "name": "模型推理", "default": {"limit": 300, "window": 60}},
    {"bucket": "notebook", "name": "Notebook", "default": {"limit": 300, "window": 60}},
    {"bucket": "eval_model", "name": "模型评测", "default": {"limit": 300, "window": 60}},
    # 网关模块自身：随机前缀/探测流量每个新前缀都是一个无限流的新桶，故统一归口到这里
    {"bucket": BUCKET_GATEWAY, "name": "网关（无效模块/探测流量）",
     "default": {"limit": 60, "window": 60}},
]

# 模块名别名 → Nacos 注册名（网关转发的唯一依据，不在表内即 404）
# 不含 gateway：网关不把自己代理给自己，/internal/** 只能内网直连调用
SERVICE_ALIASES = {
    "system": "service_system",
    "login": "service_login",
    "rag": "service_rag",
    "workflow": "service_workflow",
    "datasets": "service_datasets",
    "train": "service_train",
    "inference": "service_inference",
    "notebook": "service_notebook",
    "eval_model": "service_eval_model",
}


# ---- 技能/文件共享目录（环境变量可覆盖） ----
SKILL_SHARE_DIR = "/data/skills"
UPLOAD_TMP_DIR = "/data/tmp"
MAX_SKILL_SIZE = 100 * 1024 * 1024      # 技能包最大 100MB
MAX_DOC_SIZE = 50 * 1024 * 1024         # 文档最大 50MB

# ---- Celery 队列 ----
QUEUE_VECTORIZE = "vectorize_queue"
QUEUE_PARSE = "parse_queue"

# ---- 知识库状态 ----
DOC_STATUS_PENDING = 0      # 待处理
DOC_STATUS_PARSING = 1      # 解析中
DOC_STATUS_VECTORIZING = 2  # 向量化中
DOC_STATUS_READY = 3        # 已完成
DOC_STATUS_FAILED = -1      # 失败
