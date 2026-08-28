from celery_tasks import app
from common.common_log.log_init import log



@app.task(rate_limit='100/s')
def send_email(target):
    log.info("send email success:" + target)
    return "send email success:" + target
