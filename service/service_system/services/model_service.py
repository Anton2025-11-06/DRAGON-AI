# -*- coding: utf-8 -*-
"""模型广场服务：模型 CRUD + 申请审批 + api-key 签发（全部基于 SQLAlchemy ORM）"""
import json
import secrets
import time
from datetime import datetime

import httpx
from sqlalchemy import func, select, update

from common.common_constants.model_constant import (
    MODEL_TYPE_LABELS,
    MODEL_TYPES_ALL,
    MODEL_TYPES_STREAMABLE,
    PROVIDERS_ALL,
    PROVIDER_LABELS,
    MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR,
)
from common.common_constants.model_registry import MODEL_REGISTRY
from common.common_entity.rbac_entity import Dept
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_model import entry as cm_entry
from service.service_gateway.util.model_gateway_cache import ModelGatewayCache
from service.service_system.models.model import Model, ModelApply
from service.service_system.schemas.model_schema import (
    ApplyPageRequest, ModelApplyRequest, ModelAuditRequest, ModelPageRequest,
    ModelSaveRequest, ModelTestRequest,
)

# 模型分类字典（12 类型，统一入口：common.common_constants.model_constant）
CATEGORIES = MODEL_TYPE_LABELS
# 模型提供商：仅 common_model 实现的 3 家（openai=通用 OpenAI 兼容客户端，其余 OpenAI 兼容厂商归此）
PROVIDERS = PROVIDER_LABELS
# api-key 前缀
API_KEY_PREFIX = "mk_"
APPLY_STATUS = {0: "待审批", 1: "已通过", 2: "已拒绝"}


class ModelService:
    """模型广场：模型管理 + 申请审批 + api-key 签发"""

    # ==================== 常用参数派生 ====================

    @staticmethod
    def _cast_default(value, ptype: str):
        """按参数类型把 common_params 的 default 转为调用注入值；转换失败原样返回。"""
        if value is None or value == "":
            return None
        try:
            if ptype == "boolean":
                if isinstance(value, str):
                    return value.strip().lower() in ("1", "true", "yes", "on")
                return bool(value)
            if ptype == "integer":
                return int(float(value))
            if ptype == "number":
                return float(value)
            if ptype == "object":
                return json.loads(value) if isinstance(value, str) else value
        except (ValueError, TypeError, json.JSONDecodeError):
            return value
        return value

    @staticmethod
    def _derive_model_params(common_params) -> dict:
        """由 common_params 富列表派生调用注入字典 {name: 转型后default}。"""
        out = {}
        for p in (common_params or []):
            name = p.get("name") if isinstance(p, dict) else getattr(p, "name", None)
            if not name:
                continue
            default = p.get("default") if isinstance(p, dict) else getattr(p, "default", None)
            ptype = p.get("type") if isinstance(p, dict) else getattr(p, "type", "string")
            casted = ModelService._cast_default(default, ptype or "string")
            if casted is not None:
                out[name] = casted
        return out

    # ==================== 模型网关缓存 ====================

    @staticmethod
    def _config_dict(m) -> dict:
        """模型 ORM → 网关路由配置 JSON"""
        return {
            "model_id": m.id,
            "name": m.name,
            "category": m.category,
            "model_name": m.model_name,
            "provider": m.provider,
            "base_url": m.base_url or "",
            "rate_limit_qps": m.rate_limit_qps,
            "status": 1 if m.status else 0,
            "api_key": m.api_key,
            "model_params": m.model_params or {},
            "supports_stream": int(m.supports_stream or 0),
            "supports_thinking": int(m.supports_thinking or 0),
            "supports_function_call": int(m.supports_function_call or 0),
        }

    # ==================== 模型 CRUD ====================

    @staticmethod
    def _dict(m: Model, with_secret: bool = False) -> dict:
        """ORM 实体 → 响应字典

        不变量：base_url/gateway_url/api_key 是同一组管理端凭据，只在 with_secret=True 时出现。
        对外调用统一走网关，普通用户不需要厂商基址；调用方按“是否模型管理场景”决定 with_secret。
        """
        data = {
            "id": m.id,
            "name": m.name,
            "category": m.category,
            "category_label": CATEGORIES.get(m.category, m.category),
            "provider": m.provider,
            "provider_label": PROVIDERS.get(m.provider, m.provider),
            "model_name": m.model_name,
            "rate_limit_qps": m.rate_limit_qps,
            "status": bool(m.status),
            "supports_stream": bool(m.supports_stream),
            "supports_thinking": bool(m.supports_thinking),
            "supports_function_call": bool(m.supports_function_call),
            "stream_param": m.stream_param,
            "thinking_param": m.thinking_param,
            "common_params": m.common_params or [],
            "created_by": m.created_by,
            "create_time": m.create_time,
            "update_time": m.update_time,
            "tutorial_md": m.tutorial_md or ""
        }
        if with_secret:
            data["base_url"] = m.base_url
            data["gateway_url"] = m.gateway_url
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
        """模型详情：with_secret=True 时才带 base_url/gateway_url/api_key（模型管理场景）"""
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                raise ValueError("模型不存在")
            return ModelService._dict(m, with_secret=with_secret)

    @staticmethod
    def _validate_meta(category: str, provider: str):
        if category not in MODEL_TYPES_ALL:
            raise ValueError(f"不支持的分类: {category}，可选: {', '.join(MODEL_TYPES_ALL)}")
        if provider not in PROVIDERS_ALL:
            raise ValueError(f"不支持的提供商: {provider}，可选: {', '.join(PROVIDERS_ALL)}")

    @staticmethod
    async def create(req: ModelSaveRequest, created_by: int = 0) -> int:
        """新增模型：分类/提供商校验 + 限流参数归一 + 常用参数派生注入"""
        ModelService._validate_meta(req.category, req.provider)
        common_params = [p.model_dump() for p in (req.common_params or [])]
        async with mysql_client.get_session() as session:
            m = Model(
                name=req.name, category=req.category, provider=req.provider,
                model_name=req.model_name, base_url=req.base_url or None,
                gateway_url=req.gateway_url or None,
                api_key=req.api_key or None,
                rate_limit_qps=max(0, req.rate_limit_qps),
                supports_stream=1 if req.supports_stream else 0,
                supports_thinking=1 if req.supports_thinking else 0,
                supports_function_call=1 if req.supports_function_call else 0,
                stream_param=req.stream_param or None,
                thinking_param=req.thinking_param or None,
                common_params=common_params or None,
                model_params=ModelService._derive_model_params(common_params) or None,
                tutorial_md=req.tutorial_md or None,
                status=1 if req.status else 0, created_by=created_by or 0,
            )
            session.add(m)
            await session.commit()
            await session.refresh(m)
            log.info(f"Model created: {req.name} ({req.category}/{req.provider})")
            # MySQL 为主数据源：同步写入网关缓存（失败不阻塞，由对账兑底）
            await ModelGatewayCache.write_config(ModelService._config_dict(m))
            return m.id

    @staticmethod
    async def update(model_id: int, req: ModelSaveRequest) -> bool:
        """修改模型：None 字段（base_url/api_key/tutorial_md）保持原值，空串表示清空"""
        ModelService._validate_meta(req.category, req.provider)
        common_params = [p.model_dump() for p in (req.common_params or [])]
        async with mysql_client.get_session() as session:
            m = await session.get(Model, model_id)
            if not m:
                raise ValueError("模型不存在")
            m.name = req.name
            m.category = req.category
            m.provider = req.provider
            m.model_name = req.model_name
            if req.gateway_url is not None:
                m.gateway_url = req.gateway_url or None
            if req.base_url is not None:
                m.base_url = req.base_url or None
            if req.api_key is not None:
                m.api_key = req.api_key or None
            if req.tutorial_md is not None:
                m.tutorial_md = req.tutorial_md or None
            m.rate_limit_qps = max(0, req.rate_limit_qps)
            m.supports_stream = 1 if req.supports_stream else 0
            m.supports_thinking = 1 if req.supports_thinking else 0
            m.supports_function_call = 1 if req.supports_function_call else 0
            m.stream_param = req.stream_param or None
            m.thinking_param = req.thinking_param or None
            m.common_params = common_params or None
            m.model_params = ModelService._derive_model_params(common_params) or None
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
    async def registry(provider: str = None, category: str = None) -> list:
        """模型标识注册表（全部条目经真实 API 调用验证，数据源 common_constants/model_registry.py）。

        添加模型弹窗下拉数据源：按 (类型, 厂家) 过滤；未填时返回全部。
        """
        items = []
        for prov, cats in MODEL_REGISTRY.items():
            if provider and prov != provider:
                continue
            for cat, models in cats.items():
                if category and cat != category:
                    continue
                for m in models:
                    items.append({
                        "provider": prov, "provider_label": PROVIDERS.get(prov, prov),
                        "category": cat, "category_label": CATEGORIES.get(cat, cat),
                        "model_name": m,
                    })
        return items

    @staticmethod
    async def test(req: ModelTestRequest):
        """模型测试（SSE 流式）：走 common_model 按 (类型, 供应商) 真实调用，逐帧 yield `data: {json}\\n\\n`。

        - 无 inputs 时用 PROBE_PAYLOADS 默认探测入参；有 inputs 时按类型翻译为真实入参。
        - params 合并进 model_params 注入调用；thinking 仅对话类生效（走 kwargs）。
        - 流式：先逐块产出 {"type":"chunk","content","reasoning_content"} 增量帧，
          末尾再产出一条 {"type":"result",...} 汇总帧；非流式直接产出汇总帧。
        - 校验/调用异常均转为 success=false 的 result 帧（异步生成器内 raise 无法被路由捕获）。
        """
        import time as _t

        def _sse(payload: dict) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"

        def _fail(message: str, latency_ms: int = 0) -> str:
            return _sse({"type": "result", "success": False,
                         "message": message, "latency_ms": latency_ms})

        base_url = (req.base_url or "").rstrip("/")
        if not base_url:
            yield _fail("请填写接口地址"); return
        if not base_url.startswith(("http://", "https://")):
            yield _fail("接口地址需以 http(s):// 开头"); return
        if req.category not in MODEL_TYPES_ALL:
            yield _fail(f"不支持的分类: {req.category}"); return
        if req.provider not in PROVIDERS_ALL:
            yield _fail(f"不支持的提供商: {req.provider}"); return
        config = cm_entry.config_from_row({
            "model_id": 0, "category": req.category, "provider": req.provider,
            "model_name": req.model_name, "base_url": base_url, "api_key": req.api_key or "",
            "model_params": req.params or {}, "status": 1,
        })
        if not cm_entry.supports(req.category, req.provider):
            yield _fail(f"{req.category} 类型暂未支持供应商 {req.provider}，请提需求 "); return
        t0 = _t.monotonic()
        try:
            inst = cm_entry.instantiate(req.category, config)
            if req.inputs:
                kwargs = cm_entry.body_to_kwargs(req.category, req.inputs)
            else:
                kwargs = dict(cm_entry.PROBE_PAYLOADS.get(req.category, {}))
            # thinking 仅对话类子类会 pop 处理，非对话类传入会被 SDK 拒收
            if req.thinking and req.category in (
                    MT_TEXT_TO_TEXT, MT_IMAGE_UNDERSTAND, MT_VIDEO_UNDERSTAND, MT_OCR):
                kwargs["thinking"] = True
            stream_on = bool(req.stream) and req.category in MODEL_TYPES_STREAMABLE
            content_acc = ""
            reasoning_acc = ""
            usage_acc: dict = {}
            if stream_on:
                async for c in inst.astream(**kwargs):
                    piece = c.content or ""
                    reason = c.reasoning_content or ""
                    content_acc += piece
                    reasoning_acc += reason
                    if c.usage:
                        usage_acc = c.usage
                    if piece or reason:
                        yield _sse({"type": "chunk", "content": piece,
                                    "reasoning_content": reason})
                r = cm_entry.ModelResult(content=content_acc,
                                         reasoning_content=reasoning_acc,
                                         usage=usage_acc)
            else:
                r = await inst.ainvoke(**kwargs)
            ms = int((_t.monotonic() - t0) * 1000)
            urls = list(r.urls or []) if r.urls else ([r.url] if r.url else [])
            if not urls and r.audio_bytes:
                import base64
                fmt = (r.raw or {}).get("format") or "wav"
                urls = ["data:audio/{};base64,{}".format(
                    fmt, base64.b64encode(r.audio_bytes).decode())]
            vectors = None
            if r.vectors:
                dim = len(r.vectors[0]) if r.vectors else 0
                vectors = {"dim": dim, "count": len(r.vectors or []),
                           "sample": [round(v, 4) for v in (r.vectors[0][:8] if r.vectors else [])]}
            yield _sse({"type": "result", "success": True, "message": f"调用成功（{ms}ms）",
                        "latency_ms": ms,
                        "data": {"category": req.category, "provider": req.provider,
                                 "model": inst.model, "streamed": stream_on,
                                 "content": r.content or None,
                                 "reasoning": r.reasoning_content or None,
                                 "urls": urls, "vectors": vectors, "scores": r.scores,
                                 "usage": r.usage}})
        except Exception as e:  # noqa: BLE001
            yield _fail(f"测试异常：{type(e).__name__}: {str(e)[:200]}",
                        int((_t.monotonic() - t0) * 1000))

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
                "model_name": m.model_name, "api_key": a.api_key,
                # 不给 base_url：用户调用入口是 gateway_url（缺省前端自己拼 /api/model），
                # 厂商基址属于管理端凭据
                "gateway_url": m.gateway_url,
                "tutorial_md": m.tutorial_md or "",
                "rate_limit_qps": m.rate_limit_qps,
                "supports_stream": bool(m.supports_stream),
                "supports_thinking": bool(m.supports_thinking),
                "stream_param": m.stream_param,
                "thinking_param": m.thinking_param,
                "common_params": m.common_params or [],
                "apply_time": a.apply_time,
            } for a, m in rows]
