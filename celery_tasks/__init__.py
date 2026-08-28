import os

from celery import Celery

broker_url = os.environ.get("CELERY_BROKER_URL", "redis://10.88.128.15:26379/2")

app = Celery('all-tasks',
             broker=broker_url,
             backend=broker_url,
             )

app.autodiscover_tasks(
    packages=['celery_tasks.tasks.add', 'celery_tasks.tasks.delete', 'celery_tasks.tasks.email',
              'celery_tasks.tasks.vectorize'])

app.conf.beat_schedule = {
    # 任务2：每隔30秒执行一次加法任务
    'delete log  per 30 seconds': {
        'task': 'celery_tasks.tasks.delete.tasks.delete_log',
        'args': ("login_log",),  # 任务入参
        'schedule': 3.0,  # 间隔时间，单位秒
        'options': {"queue": "clean_queue"}
    },
}


# ===================== 多队列核心配置 =====================
# 1. 定义所有队列名称、路由规则
app.conf.task_routes = {
    # 格式：任务全路径 : {"queue": "队列名"}
    "celery_tasks.tasks.add": {"queue": "math_queue"},
    "celery_tasks.tasks.send_email": {"queue": "email_queue"},
    "celery_tasks.tasks.delete_log": {"queue": "clean_queue"},
}

# 2. 声明队列（可选但推荐，启动时自动创建）
app.conf.task_queues = {
    "math_queue": {"exchange": "math_queue", "routing_key": "math_queue"},
    "email_queue": {"exchange": "email_queue", "routing_key": "email_queue"},
    "clean_queue": {"exchange": "clean_queue", "routing_key": "clean_queue"},
    # 知识库向量化（解析+分块+embedding+入Milvus）
    "vectorize_queue": {"exchange": "vectorize_queue", "routing_key": "vectorize_queue"},
    # 可选：默认队列，未指定路由的任务进这里
    "default": {"exchange": "default", "routing_key": "default"},
}

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    # 至少一次消费
    task_acks_late=True,
    enable_utc=True
)

