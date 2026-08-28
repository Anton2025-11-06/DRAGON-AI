from celery_tasks import app
from common.common_log.log_init import log


@app.task
def delete_log(table):
    log.info("success delete log from table:" + table)
    return "success delete log from table:" + table
