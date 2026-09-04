from datetime import datetime

from sqlalchemy import func, select

from common.common_entity.rbac_entity import OperateLog
from common.common_mysql.mysql import mysql_client
from service.service_system.schemas.rbac_schema import OperateLogQuery


class LogService:

    @staticmethod
    async def list_operate_logs(page: int = 1, page_size: int = 10, q: OperateLogQuery = None):
        """
        操作日志分页查询（系统审计）：
        支持按操作人/模块/方法/状态码/trace_id/时间范围筛选，按时间倒序
        """
        async with mysql_client.get_session() as session:
            stmt = select(OperateLog)
            q = OperateLogQuery() if q is None else q

            if q.username:
                stmt = stmt.where(OperateLog.username.like(f"%{q.username}%"))
            if q.module:
                stmt = stmt.where(OperateLog.module == q.module)
            if q.method:
                stmt = stmt.where(OperateLog.method == q.method)
            if q.status is not None:
                stmt = stmt.where(OperateLog.status == q.status)
            if q.trace_id:
                stmt = stmt.where(OperateLog.trace_id == q.trace_id)
            if q.start_time:
                stmt = stmt.where(OperateLog.create_time >= datetime.fromisoformat(q.start_time))
            if q.end_time:
                stmt = stmt.where(OperateLog.create_time <= datetime.fromisoformat(q.end_time))

            total = (await session.execute(
                select(func.count()).select_from(stmt.subquery()))).scalar()

            rows = (await session.execute(
                stmt.order_by(OperateLog.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()

            items = [{
                "log_id": r.log_id, "trace_id": r.trace_id, "user_id": r.user_id,
                "username": r.username or "-", "module": r.module or "-",
                "operation": r.operation, "method": r.method, "path": r.path,
                "params": r.params, "ip": r.ip, "user_agent": r.user_agent,
                "status": r.status, "cost_ms": r.cost_ms, "error_msg": r.error_msg,
                "create_time": r.create_time,
            } for r in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def trace_detail(trace_id: str) -> dict:
        """
        按链路追踪 ID 查询该请求全链路明细：
        返回主日志及其关联的（同一次请求内的）全部日志记录
        """
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(OperateLog).where(OperateLog.trace_id == trace_id)
                .order_by(OperateLog.create_time.asc()))).scalars().all()
            return {
                "trace_id": trace_id,
                "total": len(rows),
                "items": [{
                    "log_id": r.log_id, "user_id": r.user_id, "username": r.username or "-",
                    "module": r.module or "-", "operation": r.operation, "method": r.method,
                    "path": r.path, "params": r.params, "ip": r.ip,
                    "status": r.status, "cost_ms": r.cost_ms, "error_msg": r.error_msg,
                    "create_time": r.create_time,
                } for r in rows],
            }