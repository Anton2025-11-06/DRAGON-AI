# -*- coding: utf-8 -*-
"""知识评测服务（RAGAS）：可选库列表 / 发起评测 / 运行历史 / 详情 / 删除。

四条主线
--------
1. **鉴权两层**：菜单与「能不能进评测页」走功能权限（ai:kb:eval，挂在 router 上）；
   「能不能在评测页看到这个库、能不能对它跑评测」走知识库的 **eval ACL**——列表用
   ``build_visible_cond(action=ACTION_EVAL)`` 下推，发起前用 ``ensure_action(ACTION_EVAL)`` 单点卡。
   eval 不在 scope 里，所以同部门不会自动放开评测可见性（需求落地）。
2. **模型页自选**：生成 / 裁判 / 相似度三个模型全部评测页选（不依赖库表 chat_model_id），
   发起前校验类型：生成与裁判必须 text_to_text，相似度必须是可喂文本的向量能力。
3. **配置快照**：检索参数与三个模型 id 全落 run 行，后续库配置变更不影响历史回看。
4. **运行历史是个人资产**：仅归属人 + ADMIN 可见（跨部门不共享）；能不能对非本人库发起
   评测靠的是库的 eval ACL，不代表能看别人的评测记录。

执行不在这里：create_run 落库后投 ARQ ragflow 队列即返回，真正的召回/生成/打分在
``rag_eval_runner`` 里跑（见该模块）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func, select, update

from common.common_constants import rag_constant as RC
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import is_admin
from common.common_permission.resource_guard import (
    ACTION_EVAL, build_visible_cond, ensure_action,
)
from service.service_rag.models.kb_entity import (
    KnowledgeBase, RagEvalItem, RagEvalRun,
)
from service.service_rag.schemas.rag_schema import (
    EvalRunCreateReq, EvalRunPageReq, EvalRunResp,
)
from service.service_rag.services.kb_service import KnowledgeBaseService
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.task_service import RagTaskService

# resource_guard 里的资源码（与 RESOURCE_SPECS 键一致）
RESOURCE = "knowledge_base"


def _fmt_dt(value: Optional[datetime]) -> Optional[str]:
    return value.strftime("%Y-%m-%d %H:%M:%S") if isinstance(value, datetime) else None


async def _metric_avgs_map(session, run_ids: list[int]) -> dict[int, dict[str, Any]]:
    """一批 run 的五指标均值（只统计 DONE 且该项非空的条项），一次聚合查完避免逐 run N+1。

    ragas 某些指标可能整列拿不到分（个别条项失败），AVG 会自动跳过 NULL；全列 NULL 时
    func.avg 返回 None，这里回落成 None 让前端显示「—」而不是 0。
    """
    if not run_ids:
        return {}
    cols = {
        RC.EVAL_METRIC_FAITHFULNESS: RagEvalItem.faithfulness,
        RC.EVAL_METRIC_ANSWER_RELEVANCY: RagEvalItem.answer_relevancy,
        RC.EVAL_METRIC_CONTEXT_PRECISION: RagEvalItem.context_precision,
        RC.EVAL_METRIC_CONTEXT_RECALL: RagEvalItem.context_recall,
        RC.EVAL_METRIC_ANSWER_CORRECTNESS: RagEvalItem.answer_correctness,
    }
    fields = [RagEvalItem.run_id] + [func.avg(col).label(name) for name, col in cols.items()]
    rows = (await session.execute(
        select(*fields).where(
            RagEvalItem.run_id.in_(run_ids),
            RagEvalItem.status == RC.EVAL_ITEM_DONE,
        ).group_by(RagEvalItem.run_id))).all()
    out: dict[int, dict[str, Any]] = {}
    for row in rows:
        rid = int(row[0])
        out[rid] = {name: (round(float(getattr(row, name)), 4)
                           if getattr(row, name) is not None else None)
                    for name in cols}
    return out


def _run_to_resp(run: RagEvalRun, *, metric_avgs: Optional[dict] = None) -> EvalRunResp:
    return EvalRunResp(
        run_id=run.run_id, kb_id=run.kb_id, kb_name=run.kb_name, name=run.name,
        generation_model_id=run.generation_model_id, judge_model_id=run.judge_model_id,
        embed_model_id=run.embed_model_id, top_k=run.top_k,
        score_threshold=float(run.score_threshold or 0.0), retrieval_mode=run.retrieval_mode,
        with_graph=bool(run.with_graph), graph_source_chunks=bool(run.graph_source_chunks),
        total_pairs=run.total_pairs, done_pairs=run.done_pairs,
        status=run.status, status_label=RC.EVAL_STATUS_LABELS.get(run.status, run.status),
        avg_latency_ms=run.avg_latency_ms, error=run.error, created_by=run.created_by,
        create_time=_fmt_dt(run.create_time), update_time=_fmt_dt(run.update_time),
        metric_avgs=metric_avgs or {},
    )


def _item_to_dict(item: RagEvalItem) -> dict:
    """逐问答对结果 → 前端契约字典（camelCase，与 EvalRunResp.dump() 同口径）。"""
    return {
        "itemId": item.item_id, "runId": item.run_id, "question": item.question,
        "reference": item.reference, "generatedAnswer": item.generated_answer,
        "contexts": item.contexts or [],
        "faithfulness": item.faithfulness, "answerRelevancy": item.answer_relevancy,
        "contextPrecision": item.context_precision, "contextRecall": item.context_recall,
        "answerCorrectness": item.answer_correctness,
        "tookRecallMs": item.took_recall_ms, "tookGenerateMs": item.took_generate_ms,
        "tookScoreMs": item.took_score_ms, "status": item.status,
        "statusLabel": RC.EVAL_STATUS_LABELS.get(item.status, item.status),
        "error": item.error,
    }


class RagEvalService:
    """知识评测服务（全静态方法，与其它 rag service 的调用口径一致）"""

    # ==================== 一、可选库列表 ====================
    @staticmethod
    async def kbs(login_user: dict) -> list[dict]:
        """评测页的知识库下拉：按 eval ACL 过滤。

        归属人/ADMIN 天然可见自己的库；非本人库必须被显式授了 eval 才出现在这里——
        这正是需求「授权了评测 ACL 才能看到非自己创建的知识库」的直接落地。
        """
        async with mysql_client.get_session() as session:
            conds = [KnowledgeBase.is_deleted == 0]
            visible = build_visible_cond(login_user, RESOURCE,
                                         KnowledgeBase.kb_id, KnowledgeBase.created_by,
                                         action=ACTION_EVAL)
            if visible is not None:
                conds.append(visible)
            rows = (await session.execute(
                select(KnowledgeBase).where(*conds)
                .order_by(KnowledgeBase.kb_id.desc()))).scalars().all()
            return [{"id": r.kb_id, "name": r.kb_name, "kbType": r.kb_type,
                     "kbTypeLabel": RC.KB_TYPE_LABELS.get(r.kb_type, r.kb_type),
                     "embeddingModelId": r.embedding_model_id,
                     "graphEnabled": bool(r.graph_enabled), "docCount": r.doc_count}
                    for r in rows]

    # ==================== 二、发起评测 ====================
    @staticmethod
    async def create_run(login_user: dict, req: EvalRunCreateReq) -> dict:
        """落 run + 全部 item（PENDING），再投 ARQ ragflow 队列，返回 {runId, totalPairs}。

        卡点评：
        - eval ACL：对 kb_id 卡 ACTION_EVAL（非本人库没被授权就在这里 403）；
        - 模型类型：生成/裁判 text_to_text，相似度可喂文本的向量；
        - 数据集：问答对不能为空（否则投一个空任务到 worker 只是浪费一次入队）。
        """
        pairs = [p for p in (req.pairs or []) if str(p.question or "").strip()]
        if not pairs:
            raise ValueError("请至少录入一个有效的问答对（问题不能为空）")

        async with mysql_client.get_session() as session:
            kb = await KnowledgeBaseService.load(session, req.kb_id)
            if not kb:
                raise ValueError("知识库不存在")
            await ensure_action(login_user, RESOURCE, req.kb_id, ACTION_EVAL,
                                owner_id=kb.created_by, session=session)

            # 三个模型全部页自选并即时校验类型（不依赖库表 chat_model_id）
            gen_cfg = await RagModelService.require_config(req.generation_model_id, "生成答案的对话模型")
            RagModelService.ensure_chat_capable(gen_cfg, "生成答案的对话模型")
            judge_cfg = await RagModelService.require_config(req.judge_model_id, "裁判模型")
            RagModelService.ensure_chat_capable(judge_cfg, "裁判模型")
            embed_cfg = await RagModelService.require_config(req.embed_model_id, "相似度向量模型")
            RagModelService.ensure_embed_capable(embed_cfg, "相似度向量模型")

            # 检索参数：显式给了就用给的，否则回落库的 retrieve_config 默认
            kb_cfg = KnowledgeBaseService.retrieve_of(kb)
            top_k = int(req.top_k or kb_cfg["top_k"])
            score_threshold = float(req.score_threshold
                                    if req.score_threshold is not None
                                    else kb_cfg["score_threshold"])

            run = RagEvalRun(
                kb_id=kb.kb_id, kb_name=kb.kb_name,
                name=(req.name or "").strip() or f"{kb.kb_name} 评测",
                generation_model_id=req.generation_model_id,
                judge_model_id=req.judge_model_id, embed_model_id=req.embed_model_id,
                top_k=top_k, score_threshold=score_threshold,
                retrieval_mode=(req.retrieval_mode or None),
                with_graph=1 if req.with_graph else 0,
                graph_source_chunks=1 if req.graph_source_chunks else 0,
                total_pairs=len(pairs), done_pairs=0,
                status=RC.EVAL_STATUS_PENDING,
                created_by=int(login_user.get("user_id") or 0),
                owner_dept_id=int(login_user.get("dept_id") or 0))
            session.add(run)
            await session.flush()
            run_id = run.run_id
            for p in pairs:
                session.add(RagEvalItem(
                    run_id=run_id, question=str(p.question).strip(),
                    reference=(str(p.reference).strip() if p.reference else None),
                    status=RC.EVAL_ITEM_PENDING))
            await session.commit()

        # 落库成功再入队：任务只带 run_id，其余参数 worker 从库里读（快照已在 run 行上）
        job_id = await RagTaskService.enqueue(
            RC.RAG_TASK_EVAL, [run_id], job_key=f"eval:{run_id}", unique=False)
        log.info("知识评测已提交: run_id={} kb={} pairs={} job={}",
                 run_id, req.kb_id, len(pairs), job_id)
        return {"runId": run_id, "totalPairs": len(pairs)}

    # ==================== 三、运行历史 ====================
    @staticmethod
    async def page_runs(login_user: dict, req: EvalRunPageReq) -> dict:
        """运行历史分页（个人资产：仅归属人 + ADMIN 全见）。"""
        async with mysql_client.get_session() as session:
            conds = [RagEvalRun.is_deleted == 0]
            if not is_admin(login_user):
                conds.append(RagEvalRun.created_by == int(login_user.get("user_id") or 0))
            if req.kb_id:
                conds.append(RagEvalRun.kb_id == int(req.kb_id))
            if req.status:
                conds.append(RagEvalRun.status == req.status)
            total = (await session.execute(
                select(func.count()).select_from(RagEvalRun).where(*conds))).scalar() or 0
            rows = (await session.execute(
                select(RagEvalRun).where(*conds)
                .order_by(RagEvalRun.run_id.desc())
                .limit(req.size).offset((req.current - 1) * req.size))).scalars().all()
            run_ids = [r.run_id for r in rows]
            avgs = await _metric_avgs_map(session, run_ids)
            records = [_run_to_resp(r, metric_avgs=avgs.get(r.run_id, {})).dump() for r in rows]
            return {"records": records, "total": total,
                    "current": req.current, "size": req.size}

    # ==================== 四、详情 ====================
    @staticmethod
    async def run_detail(login_user: dict, run_id: int) -> dict:
        """评测详情：run + 逐问答对 items + 五指标均值（仅归属人 + ADMIN 可看）。"""
        async with mysql_client.get_session() as session:
            run = (await session.execute(
                select(RagEvalRun).where(RagEvalRun.run_id == int(run_id),
                                         RagEvalRun.is_deleted == 0))).scalar_one_or_none()
            if not run:
                raise ValueError("评测记录不存在")
            if not is_admin(login_user) and run.created_by != int(login_user.get("user_id") or 0):
                raise ValueError("无权查看这条评测记录")
            avgs = await _metric_avgs_map(session, [run.run_id])
            resp = _run_to_resp(run, metric_avgs=avgs.get(run.run_id, {}))
            items = (await session.execute(
                select(RagEvalItem).where(RagEvalItem.run_id == run.run_id)
                .order_by(RagEvalItem.item_id.asc()))).scalars().all()
            data = resp.dump()
            data["items"] = [_item_to_dict(i) for i in items]
            return data

    # ==================== 五、删除 ====================
    @staticmethod
    async def delete_run(login_user: dict, run_id: int) -> bool:
        """软删一次运行（run 有 is_deleted；items 无软删列，随 run 隐藏不出现在详情里）。"""
        async with mysql_client.get_session() as session:
            run = (await session.execute(
                select(RagEvalRun).where(RagEvalRun.run_id == int(run_id),
                                         RagEvalRun.is_deleted == 0))).scalar_one_or_none()
            if not run:
                raise ValueError("评测记录不存在")
            if not is_admin(login_user) and run.created_by != int(login_user.get("user_id") or 0):
                raise ValueError("无权删除这条评测记录")
            await session.execute(update(RagEvalRun).where(
                RagEvalRun.run_id == run.run_id).values(is_deleted=1))
            await session.commit()
        log.info("知识评测记录已删除: run_id={}", run_id)
        return True

