import sys
from contextvars import ContextVar

from loguru import logger

# 链路追踪 ID：请求进入时由中间件写入，日志格式自动带上 trace_id，实现全链路可观测
trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def _inject_trace(record):
    """loguru patch：每次写日志时从 contextvar 读取 trace_id 注入 extra，缺省显示 '-'"""
    record["extra"]["trace_id"] = trace_id_var.get()


logger = logger.patch(_inject_trace)

# 移除 loguru 内置默认 stderr sink：否则每条日志会同时被默认格式（毫秒、无 trace_id）
# 和自定义格式各输出一遍，造成重复打印
logger.remove()

logger.add(
    sys.stdout,
    format=("{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} "
            "| trace={extra[trace_id]} - {message}"),
    level="INFO"
)


# 文件输出：写到当前目录 ./，超 10MB 自动轮转，保留 7 天，utf-8 防中文乱码
logger.add(
    "./logs/app.log",
    format=("{time:YYYY-MM-DD HH:mm:ss} | {level} | {name}:{function}:{line} "
            "| trace={extra[trace_id]} - {message}"),
    level="INFO",
    rotation="10 MB",     # 或 "1 day" 按天切
    retention="7 days",
    encoding="utf-8",
    enqueue=True,         # 异步写文件，多线程/后台任务下不阻塞业务
)

# 对外导出（业务模块统一 from common.common_log.log_init import log 使用）
log = logger