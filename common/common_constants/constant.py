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
SERVICE_LOGIN_PORT = 9044

SERVICE_GATEWAY = "service_gateway"
SERVICE_GATEWAY_PORT = 18000

SERVICE_AGENT = "service_agent"
SERVICE_AGENT_PORT = 9005

SERVICE_SKILL = "service_skill"
SERVICE_SKILL_PORT = 9006


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
# 模型 QPS 计数（双层 hash）：外层 field=model_id，内层 field=qps，value=每秒次数
PREFIX_MODEL_RATE_LIMIT_QPS = "model_rate_limit_qps"


# 可配置的模块桶候选（与网关 _SERVICE_ALIASES 对应，login/global 另有默认策略可覆盖）
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
    # AI 模型网关 /api/model 入口：默认按 IP 60 次/分钟防爆破
    {"bucket": "model", "name": "AI模型网关", "default": {"limit": 60, "window": 60}},
]

SERVICE_ALIASES = {
    "system": "service_system",
    "login": "service_login",
    "gateway": "service_gateway",
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