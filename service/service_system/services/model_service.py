# -*- coding: utf-8 -*-
"""模型广场服务：模型 CRUD + 申请审批 + api-key 签发（全部基于 SQLAlchemy ORM）"""
import json
import secrets
import time
from datetime import datetime

import httpx
from sqlalchemy import func, select, update

from common.common_entity.rbac_entity import Dept
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_gateway.util.model_gateway_cache import ModelGatewayCache
from service.service_system.models.model import Model, ModelApply
from service.service_system.schemas.model_schema import (
    ApplyPageRequest, ModelApplyRequest, ModelAuditRequest, ModelPageRequest,
    ModelSaveRequest, ModelTestRequest,
)
from common.common_httpx.httpx import httpx_pool

# 模型分类（7 类）
CATEGORIES = {
    "TEXT_GEN": "文本生成",
    "EMBEDDING": "向量化",
    "RERANK": "重排序",
    "MULTIMODAL": "多模态",
    "IMAGE_GEN": "图片生成",
    "AUDIO_GEN": "语音生成",
    "VIDEO_GEN": "视频生成",
}
# 模型提供商
PROVIDERS = {
    "deepseek": "DeepSeek",
    "qwen": "通义千问",
    "doubao": "豆包",
    "hunyuan": "腾讯混元",
    "kimi": "Kimi",
    "openai": "OpenAI",
}
# api-key 前缀
API_KEY_PREFIX = "mk_"
APPLY_STATUS = {0: "待审批", 1: "已通过", 2: "已拒绝"}


class ModelService:
    """模型广场：模型管理 + 申请审批 + api-key 签发"""

    # ==================== 模型网关缓存 ====================

    @staticmethod
    def _config_dict(m) -> dict:
        """模型 ORM → 网关路由配置 JSON（不含管理端密钥，转发时回查 MySQL）"""
        return {
            "model_id": m.id,
            "name": m.name,
            "category": m.category,
            "model_name": m.model_name,
            "provider": m.provider,
            "base_url": m.base_url or "",
            # 直连标志 + 后缀列表（网关非直连时校验后缀合法性）
            "is_direct": 1 if m.is_direct else 0,
            "suffixes": [s.get("url") for s in (m.suffixes or []) if s.get("url")],
            "rate_limit_qps": m.rate_limit_qps,
            "status": 1 if m.status else 0,
            "api_key": m.api_key,
        }

    # ==================== 模型 CRUD ====================

    @staticmethod
    def _dict(m: Model, with_secret: bool = False) -> dict:
        """ORM 实体 → 响应字典（默认隐藏 api_key/tutorial_md）"""
        data = {
            "id": m.id,
            "name": m.name,
            "category": m.category,
            "category_label": CATEGORIES.get(m.category, m.category),
            "provider": m.provider,
            "provider_label": PROVIDERS.get(m.provider, m.provider),
            "model_name": m.model_name,
            "base_url": m.base_url,
            "gateway_url": m.gateway_url,
            "is_direct": bool(m.is_direct),
            "suffixes": m.suffixes or [],
            "rate_limit_qps": m.rate_limit_qps,
            "status": bool(m.status),
            "created_by": m.created_by,
            "create_time": m.create_time,
            "update_time": m.update_time,
            "tutorial_md": m.tutorial_md or ""
        }
        if with_secret:
            data["api_key"] = m.api_key
        return data

    @staticmethod
    async def page(page_req: ModelPageRequest, user_id: int = None) -> dict:
        """分页查询模型列表（人人可看）；附带当前用户对每行的最新申请状态"""
        filters = []
        if page_req.name:
            filters.append(Model.name.like(f"%{page_req.name}%"))
        if page_req.category:
            filters.append(Model.category == page_req.category)
        if page_req.provider:
            filters.append(Model.provider == page_req.provider)
        if page_req.status is not None:
            filters.append(Model.status == (1 if page_req.status else 0))

        # 当前用户申请状态子查询（取该模型最新一条申请的状态，避免 N+1）
        apply_subq = (
            select(ModelApply.status)
            .where(ModelApply.model_id == Model.id, ModelApply.user_id == user_id)
            .order_by(ModelApply.id.desc())
            .limit(1)
            .scalar_subquery()
        ) if user_id else None

        base = select(Model)
        if filters:
            base = base.where(*filters)

        async with mysql_client.get_session() as session:
            total = (await session.execute(
                select(func.count()).select_from(base.subquery()))).scalar() or 0
            stmt = base.order_by(Model.id.desc()) \
                .offset((page_req.current - 1) * page_req.size).limit(page_req.size)
            if apply_subq is not None:
                stmt = stmt.add_columns(apply_subq.label("apply_status"))
                rows = (await session.execute(stmt)).all()
                items = []
                for m, apply_status in rows:
                    item = ModelService._dict(m)
                    item["apply_status"] = apply_status
                    items.append(item)
            else:
                rows = (await session.execute(stmt)).scalars().all()
                items = [ModelService._dict(m) for m in rows]
            return {"total": total, "items": items}

    @staticmethod
    async def detail(model_id: int, with_secret: bool = False) -> dict:
        """模型详情：with_secret=True 时包含 api_key/tutorial_md（管理员场景）"""
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                raise ValueError("模型不存在")
            return ModelService._dict(m, with_secret=with_secret)

    @staticmethod
    def _validate_meta(category: str, provider: str):
        if category not in CATEGORIES:
            raise ValueError(f"不支持的分类: {category}，可选: {', '.join(CATEGORIES)}")
        if provider not in PROVIDERS:
            raise ValueError(f"不支持的提供商: {provider}，可选: {', '.join(PROVIDERS)}")

    @staticmethod
    def _normalize_suffixes(items) -> list:
        """后缀列表归一化：仅保留有 url 的行，url 统一补前导 /，去重"""
        seen, result = set(), []
        for item in items or []:
            url = (item.url if isinstance(item, dict) else getattr(item, "url", "") or "").strip()
            if not url:
                continue
            if not url.startswith("/"):
                url = "/" + url
            if url in seen:
                continue
            seen.add(url)
            desc = ((item.desc if isinstance(item, dict) else getattr(item, "desc", "")) or "").strip()
            result.append({"url": url, "desc": desc})
        return result

    @staticmethod
    async def create(req: ModelSaveRequest, created_by: int = 0) -> int:
        """新增模型：分类/提供商校验 + 限流参数归一"""
        ModelService._validate_meta(req.category, req.provider)
        async with mysql_client.get_session() as session:
            m = Model(
                name=req.name, category=req.category, provider=req.provider,
                model_name=req.model_name, base_url=req.base_url or None,
                is_direct=1 if req.is_direct else 0,
                gateway_url=req.gateway_url or None,
                suffixes=ModelService._normalize_suffixes(req.suffixes) or None,
                api_key=req.api_key or None,
                rate_limit_qps=max(0, req.rate_limit_qps),
                tutorial_md=req.tutorial_md or None,
                status=1 if req.status else 0, created_by=created_by or 0,
            )
            session.add(m)
            await session.commit()
            await session.refresh(m)
            log.info(f"Model created: {req.name} ({req.category}/{req.provider})")
            # MySQL 为主数据源：同步写入网关缓存（失败不阻塞，由对账兜底）
            await ModelGatewayCache.write_config(ModelService._config_dict(m))
            return m.id

    @staticmethod
    async def update(model_id: int, req: ModelSaveRequest) -> bool:
        """修改模型：None 字段（base_url/api_key/tutorial_md）保持原值，空串表示清空"""
        ModelService._validate_meta(req.category, req.provider)
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                raise ValueError("模型不存在")
            m.name = req.name
            m.category = req.category
            m.provider = req.provider
            m.model_name = req.model_name
            m.is_direct = 1 if req.is_direct else 0
            if req.gateway_url is not None:
                m.gateway_url = req.gateway_url or None
            if req.base_url is not None:
                m.base_url = req.base_url or None
            if req.suffixes is not None:
                m.suffixes = ModelService._normalize_suffixes(req.suffixes) or None
            if req.api_key is not None:
                m.api_key = req.api_key or None
            if req.tutorial_md is not None:
                m.tutorial_md = req.tutorial_md or None
            m.rate_limit_qps = max(0, req.rate_limit_qps)
            m.status = 1 if req.status else 0
            await session.commit()
            log.info(f"Model updated: id={model_id}")
            # 刷新网关缓存中的路由配置（含限流参数变更）
            await ModelGatewayCache.write_config(ModelService._config_dict(m))
            return True

    @staticmethod
    async def delete(model_id: int) -> bool:
        """删除模型：历史待审批申请一并置为拒绝"""
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                return False
            await session.execute(
                update(ModelApply)
                .where(ModelApply.model_id == model_id, ModelApply.status == 0)
                .values(status=2, reject_reason="模型已删除"))
            await session.delete(m)
            await session.commit()
            log.info(f"Model deleted: id={model_id}")
            # 删除模型：同步清理网关缓存（路由配置 + 全部已签发 api-key 吊销）
            await ModelGatewayCache.remove_config(model_id)
            await ModelGatewayCache.remove_keys(model_id=model_id)
            return True

    @staticmethod
    async def toggle_status(model_id: int, status: bool) -> bool:
        """切换启用状态：停用后网关拒绝转发（保留 api-key，恢复启用即可用）"""
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                return False
            m.status = 1 if status else 0
            await session.commit()
            # 刷新网关缓存 status，让停用立即生效
            await ModelGatewayCache.write_config(ModelService._config_dict(m))
            return True

    @staticmethod
    async def test(req: ModelTestRequest) -> dict:
        """连通性测试：按分类调用对应 OpenAI 兼容探测端点，返回耗时与结果"""
        base_url = (req.base_url or "").rstrip("/")
        if not base_url:
            raise ValueError("请填写接口地址")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("接口地址需以 http(s):// 开头")
        # 非直连模型：拼接接口后缀进行探测（如 /v1/chat/completions）
        if req.suffix_url:
            base_url = base_url + "/" + req.suffix_url.lstrip("/")

        headers = {}
        if req.api_key:
            headers["Authorization"] = f"Bearer {req.api_key}"

        # 按分类选择通用探测端点（OpenAI 兼容协议）
        if req.category == "EMBEDDING":
            body = {"model": req.model_name, "input": "hi"}
        elif req.category == "RERANK":
            body = {"model": req.model_name, "query": "hi", "documents": ["hello", "hi"]}
        else:
            body = {
                "model": req.model_name,
                "messages": [{"role": "user", "content": "hi"}],
                "max_tokens": 5,
                "stream": False
            }
        start = time.time()
        try:
            resp = await httpx_pool.client.post(base_url, json=body, headers=headers)
            latency = int((time.time() - start) * 1000)
            try:
                data = resp.json()
            except ValueError:
                # 非 JSON 响应（如 HTML 错误页）：降级为纯文本（完整返回）
                data = resp.text
            if resp.status_code < 300:
                log.info(f"Model test OK: {base_url} ({resp.status_code}, {latency}ms)")
                return {"success": True,
                        "message": f"调用成功（HTTP {resp.status_code}，{latency}ms）",
                        "latency_ms": latency,
                        "data": data}
            try:
                detail = data.get("error", {}).get("message") if isinstance(data, dict) else ""
                if not detail:
                    detail = data.get("message", "") if isinstance(data, dict) else ""
                if not detail:
                    detail = str(data)[:200]
            except Exception:
                detail = resp.text[:200]
            log.warning(f"Model test HTTP {resp.status_code}: {base_url} {detail}")
            return {"success": False,
                    "message": f"接口返回 HTTP {resp.status_code}：{detail}",
                    "latency_ms": latency,
                    "data": data}
        except httpx.TimeoutException:
            log.warning(f"Model test timeout: {base_url}")
            return {"success": False, "message": "请求超时（15s），请检查接口地址与网络"}
        except httpx.HTTPError as e:
            log.warning(f"Model test connect fail: {base_url} {e}")
            return {"success": False, "message": f"连接失败：{e}"}
        except Exception as e:
            log.error(f"Model test error: {base_url} {e}")
            return {"success": False, "message": f"测试异常：{e}"}

    # ==================== 申请审批 ====================

    @staticmethod
    async def apply(model_id: int, user_id: int, username: str,
                    dept_id: int, body: ModelApplyRequest) -> int:
        """申请使用模型：同一模型同一用户仅允许一条有效申请（待审批/已通过不可重复，仅被拒绝后可重新申请）"""
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                raise ValueError("模型不存在")
            if not m.status:
                raise ValueError("模型已停用，无法申请")
            latest = (await session.execute(
                select(ModelApply)
                .where(ModelApply.model_id == model_id, ModelApply.user_id == user_id)
                .order_by(ModelApply.id.desc()).limit(1))).scalar_one_or_none()
            if latest and latest.status == 1:
                raise ValueError("您已通过该模型的申请，无需重复申请")
            if latest and latest.status == 0:
                raise ValueError("您有待审批的申请，请等待管理员审批")
            record = ModelApply(
                model_id=model_id, user_id=user_id, username=username,
                dept_id=dept_id or 0, reason=body.reason or None)
            session.add(record)
            await session.commit()
            await session.refresh(record)
            log.info(f"Model apply: user={username} model_id={model_id}")
            return record.id

    @staticmethod
    async def applies_page(page_req: ApplyPageRequest = None) -> dict:
        """申请列表（审批端）"""
        current = page_req.current if page_req else 1
        size = page_req.size if page_req else 10
        status = page_req.status if page_req else None
        username = page_req.username if page_req else None
        model_id = page_req.model_id if page_req else None
        filters = []
        if status is not None:
            filters.append(ModelApply.status == status)
        if username:
            filters.append(ModelApply.username.like(f"%{username}%"))
        if model_id:
            filters.append(ModelApply.model_id == model_id)
        base = (select(ModelApply, Model.name, Dept.dept_name)
                .join(Model, Model.id == ModelApply.model_id, isouter=True)
                .join(Dept, Dept.dept_id == ModelApply.dept_id, isouter=True))
        if filters:
            base = base.where(*filters)
        async with mysql_client.get_session() as session:
            total = (await session.execute(
                select(func.count()).select_from(
                    select(ModelApply).where(*filters).subquery()))).scalar() or 0
            rows = (await session.execute(
                base.order_by(ModelApply.id.desc())
                .offset((current - 1) * size).limit(size))).all()
            items = []
            for a, model_name, dept_name in rows:
                items.append({
                    "id": a.id, "model_id": a.model_id,
                    "model_name": model_name or "-",
                    "user_id": a.user_id, "username": a.username,
                    "dept_id": a.dept_id, "dept_name": dept_name or "-",
                    "reason": a.reason, "status": a.status,
                    "status_label": APPLY_STATUS.get(a.status, a.status),
                    "api_key": a.api_key, "reject_reason": a.reject_reason,
                    "audit_by": a.audit_by, "audit_time": a.audit_time,
                    "apply_time": a.apply_time,
                })
            return {"total": total, "items": items}

    @staticmethod
    async def audit(apply_id: int, req: ModelAuditRequest, audit_by: str) -> dict:
        """审批：通过时后台创建 api-key（mk_ + 32 位随机 hex）；行锁防并发重复审批"""
        api_key = None
        async with mysql_client.get_session() as session:
            record = (await session.execute(
                select(ModelApply).where(ModelApply.id == apply_id).with_for_update()
            )).scalar_one_or_none()
            if not record:
                raise ValueError("申请记录不存在")
            if record.status != 0:
                raise ValueError("该申请已处理，请勿重复审批")
            if req.approve:
                api_key = API_KEY_PREFIX + secrets.token_hex(16)
                record.status = 1
                record.api_key = api_key
                record.reject_reason = None
            else:
                record.status = 2
                record.reject_reason = req.reject_reason or "未通过审批"
            record.audit_by = audit_by
            record.audit_time = datetime.now()
            await session.commit()
            log.info(f"Model apply audited: id={apply_id} approve={req.approve}")
            # 审批通过：先吊销该用户该模型的旧 key（拒绝→重申请→再通过时防旧凭证残留），
            # 再登记新 api-key 到网关缓存（MySQL 为准，失败由对账兜底）
            if req.approve and api_key:
                await ModelGatewayCache.remove_keys(user_id=record.user_id, model_id=record.model_id)
                await ModelGatewayCache.add_key(api_key, record.model_id, record.user_id or 0, record.id)
            return {"apply_id": apply_id, "approve": req.approve, "api_key": api_key}

    @staticmethod
    async def my_keys(user_id: int) -> list:
        """我的已通过申请：模型接口信息 + api-key + 使用教程"""
        async with mysql_client.get_session() as session:
            rows = (await session.execute(
                select(ModelApply, Model)
                .join(Model, Model.id == ModelApply.model_id)
                .where(ModelApply.user_id == user_id, ModelApply.status == 1,
                       Model.status == 1)
                .order_by(ModelApply.id.desc()))).all()
            return [{
                "apply_id": a.id, "model_id": m.id, "name": m.name,
                "category": m.category, "category_label": CATEGORIES.get(m.category, m.category),
                "provider": m.provider, "provider_label": PROVIDERS.get(m.provider, m.provider),
                "model_name": m.model_name, "base_url": m.base_url, "api_key": a.api_key,
                "gateway_url": m.gateway_url, "is_direct": bool(m.is_direct),
                "suffixes": m.suffixes or [],
                "tutorial_md": m.tutorial_md or "",
                "rate_limit_qps": m.rate_limit_qps,
                "apply_time": a.apply_time,
            } for a, m in rows]
