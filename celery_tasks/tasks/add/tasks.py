from celery_tasks import app
from common.common_log.log_init import log


@app.task
def add(a, b):
    log.info(f"success add log from task: {a, b}")
    return a + b
