# -*- coding: utf-8 -*-
"""知识评测执行体（RAGAS）：供 ARQ ragflow 任务调用，跑一次 run 的全部问答对。

三段流水（对每个问答对）
------------------------
1. **召回**：与检索页同一套门面（``_SearchRunner`` + ``qa_context``），只是登录态换成
   run 创建者 + ADMIN 系统态——worker 没有请求上下文，而 create_run 已经对该库卡过
   eval ACL，这里按 ``[kb_id]`` 白名单裸查即可（不走 use/view 档，避免非本人库被剔空）。
2. **生成**：召回拼成 ``build_rag_qa_prompt`` 后交「生成答案的对话模型」（页选的，不用库表
   chat_model_id），得到本次答案。
3. **打分**：汇总所有问答对的 ``SingleTurnSample`` 交给 ``ragas.evaluate``，五个指标一次算完
   （裁判模型 + 相似度模型都是页选的）。ragas 是同步接口且内部自起事件循环，放
   ``asyncio.to_thread`` 里跑；适配层带着主管线解析好的 config，打分线程不碰 MySQL。

并发与容错
----------
- 外层 ``asyncio.Semaphore(EVAL_CONCURRENCY)`` 并发逐问答对做「召回+生成」（用户要求并发查询）；
  ragas 内部打分另有自己的并发，不在这一层控制。
- 单个问答对失败（没召回到资料、模型报错、没拿到分）只把该 item 置 FAILED + error，不整单失败。
- run 的终态与计数只看**拿到分的条数**：至少一条出分就 DONE（done_pairs = 出分条数，
  部分失败把条数与首条原因写进 run.error），一条都没出分就 FAILED。打分段整段不可用时，
  列表不能还顶着「已完成」，而「召回+生成成功」的条数不能当完成数用（那正是两边对不上的来源）。
- 计数回读库里的条项状态，不按本轮跑过的条累加：一个只补跑部分条项的 run 重跑时，
  之前已落结果的条项照样算数（与 _metric_avgs_map 只认 DONE 的口径一致）。
"""
from __future__ import annotations

import asyncio
import math
import time
from typing import Any, Optional

from sqlalchemy import func, select, update

from common.common_constants import rag_constant as RC
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from service.service_rag.models.kb_entity import RagEvalItem, RagEvalRun
from service.service_rag.schemas.rag_schema import RetrieveReq
from service.service_rag.services import rag_settings as settings
from service.service_rag.services.ragas_adapter import build_embeddings, build_judge_llm
from service.service_rag.services.rag_model import RagModelService
from service.service_rag.services.retrieval_service import RagRetrievalService, qa_context

_METRIC_ATTRS = [
    RC.EVAL_METRIC_FAITHFULNESS, RC.EVAL_METRIC_ANSWER_RELEVANCY,
    RC.EVAL_METRIC_CONTEXT_PRECISION, RC.EVAL_METRIC_CONTEXT_RECALL,
    RC.EVAL_METRIC_ANSWER_CORRECTNESS,
]


def _sys_login_user(run: RagEvalRun) -> dict:
    """run 创建者的系统态登录载荷：user_id 用归属人（审计如实），roles 给 ADMIN 旁路检索白名单。"""
    return {
        "user_id": int(run.created_by or 0),
        "dept_id": int(run.owner_dept_id or 0),
        "roles": ["ADMIN"],
        "permissions": [],
    }


def _clean_score(value: Any) -> Optional[float]:
    """ragas 指标值归一：None / NaN → None，其余转 float（保留 4 位）。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f):
        return None
    return round(f, 4)


async def _recall_and_generate(run: RagEvalRun, item: RagEvalItem, login_user: dict,
                               gen_config) -> dict:
    """一个问答对的「召回 + 生成」：返回 contexts / answer / 两段耗时；失败抛异常由上层捕获。"""
    req = RetrieveReq(
        kb_ids=[run.kb_id], query=item.question, top_k=run.top_k,
        score_threshold=float(run.score_threshold or 0.0), mode=run.retrieval_mode,
        with_graph=bool(run.with_graph), chat_model_id=run.generation_model_id,
        graph_source_chunks=bool(run.graph_source_chunks))
    runner = RagRetrievalService._runner(login_user, req)
    rows = await runner.execute()
    took_recall_ms = int(runner.took_ms or 0)

    contexts = [{"content": str(r.get("content") or ""),
                 "score": float(r.get("score") or 0.0),
                 "recall": str(r.get("recall") or "CHUNK"),
                 "kb_id": int(r.get("kb_id") or 0),
                 "doc_id": int(r.get("doc_id") or 0),
                 "chunk_id": int(r.get("chunk_id") or 0)}
                for r in rows if str(r.get("content") or "").strip()]
    context_text = qa_context(rows, runner.graph)
    if not context_text:
        raise ValueError("没有召回到可用资料，无法生成答案（确认该库已解析完成、阈值别太高）")

    prompt = settings.build_rag_qa_prompt(item.question, context_text)
    t0 = time.perf_counter()
    answer = await RagModelService.chat(gen_config, prompt)
    took_generate_ms = int((time.perf_counter() - t0) * 1000)
    return {"contexts": contexts, "answer": answer,
            "took_recall_ms": took_recall_ms, "took_generate_ms": took_generate_ms}


async def _score_samples(samples: list[dict], judge_config, embed_config) -> list[dict]:
    """把问答对交给 ragas 打分（同步、自起事件循环，放 to_thread 里调）。

    返回与 ``samples`` 同序的指标字典列表。ragas 导入/接口异常在这里原样抛出，
    由调用方兜成「指标全 NULL」而不是推翻整场评测。
    """
    from ragas import aevaluate
    from ragas.dataset_schema import EvaluationDataset, SingleTurnSample
    from ragas.metrics import (_AnswerCorrectness, _Faithfulness,
                               _LLMContextPrecisionWithReference, _LLMContextRecall,
                               _ResponseRelevancy)

    built = [SingleTurnSample(user_input=s["user_input"],
                              retrieved_contexts=s["retrieved_contexts"],
                              response=s["response"],
                              reference=s.get("reference") or "")
             for s in samples]
    dataset = EvaluationDataset(samples=built)
    metrics = [_Faithfulness(), _ResponseRelevancy(), _LLMContextPrecisionWithReference(),
               _LLMContextRecall(), _AnswerCorrectness()]
    llm = build_judge_llm(0, label="裁判模型", config=judge_config)
    embeddings = build_embeddings(0, label="相似度向量模型", config=embed_config)
    result = await aevaluate(dataset, metrics=metrics, llm=llm, embeddings=embeddings)
    # to_pandas 的行序与 dataset 一致；缺列（某指标整场没算出来）按 None 处理
    records = result.to_pandas().to_dict("records")
    return [{m: rec.get(m) for m in _METRIC_ATTRS} for rec in records]


async def execute(run_id: int) -> None:
    """跑一次评测运行：召回+生成（并发）→ ragas 打分 → 逐 item 回填 → run 收口。"""
    async with mysql_client.get_session() as session:
        run = (await session.execute(
            select(RagEvalRun).where(RagEvalRun.run_id == int(run_id)))).scalar_one_or_none()
        if run is None:
            log.warning("评测运行不存在，跳过: run_id={}", run_id)
            return
        if run.status not in (RC.EVAL_STATUS_PENDING, RC.EVAL_STATUS_RUNNING):
            log.info("评测运行已是终态，跳过: run_id={} status={}", run_id, run.status)
            return
        items = list((await session.execute(
            select(RagEvalItem).where(RagEvalItem.run_id == run.run_id,
                                      RagEvalItem.status == RC.EVAL_ITEM_PENDING)
            .order_by(RagEvalItem.item_id.asc()))).scalars().all())
        run_ref = run

    try:
        await _run(run=run_ref, items=items)
    except Exception as e:  # noqa: BLE001  整体兜底：把意外写成 run 级 FAILED，不让 worker 任务裸抛
        log.error("评测运行整体失败: run_id={}: {}", run_id, e)
        reason = str(e)[:1000]
        async with mysql_client.get_session() as session:
            await session.execute(update(RagEvalRun).where(
                RagEvalRun.run_id == int(run_id)).values(
                status=RC.EVAL_STATUS_FAILED, error=reason))
            # 还没跑到的条项一并收口：详情里留着一行 PENDING 看着像还在排队，
            # 而这一场其实已经不会再有人去跑它了
            await session.execute(update(RagEvalItem).where(
                RagEvalItem.run_id == int(run_id),
                RagEvalItem.status == RC.EVAL_ITEM_PENDING).values(
                status=RC.EVAL_ITEM_FAILED, error=reason))
            await session.commit()


async def _run(*, run: RagEvalRun, items: list[RagEvalItem]) -> None:
    run_id = run.run_id
    login_user = _sys_login_user(run)
    gen_config = await RagModelService.require_config(run.generation_model_id, "生成答案的对话模型")

    async with mysql_client.get_session() as session:
        await session.execute(update(RagEvalRun).where(
            RagEvalRun.run_id == run_id).values(status=RC.EVAL_STATUS_RUNNING))
        await session.commit()

    sem = asyncio.Semaphore(max(1, RC.EVAL_CONCURRENCY))
    work: list[dict] = []

    async def _one(item: RagEvalItem) -> None:
        entry = {"item": item, "sample": None, "error": None,
                 "took_recall_ms": 0, "took_generate_ms": 0, "contexts": []}
        async with sem:
            try:
                res = await _recall_and_generate(run, item, login_user, gen_config)
                entry.update(res)
                entry["sample"] = {
                    "user_input": item.question,
                    "retrieved_contexts": [c["content"] for c in res["contexts"]],
                    "response": res["answer"],
                    "reference": item.reference or "",
                }
            except Exception as e:  # noqa: BLE001  单对失败只记在这一行
                entry["error"] = str(e)[:1000]
        work.append(entry)

    await asyncio.gather(*[_one(i) for i in items])
    # gather 完成顺序不定，按 item_id 复原与入参同序（打分结果逐行回填要对得上）
    order = {it.item_id: idx for idx, it in enumerate(items)}
    work.sort(key=lambda w: order.get(w["item"].item_id, 0))

    scored = [w for w in work if w["sample"] is not None]
    score_rows: list[dict] = []
    took_score_total_ms = 0
    if scored:
        judge_config = await RagModelService.require_config(run.judge_model_id, "裁判模型")
        embed_config = await RagModelService.require_config(run.embed_model_id, "相似度向量模型")
        t0 = time.perf_counter()
        try:
            score_rows = await _score_samples([w["sample"] for w in scored], judge_config, embed_config)
        except Exception as e:  # noqa: BLE001  打分段失败不推翻召回与生成
            log.error("RAGAS 打分失败: run_id={}: {}", run_id, e)
            for w in scored:
                w["error"] = f"打分失败：{str(e)[:400]}"
            score_rows = []
        took_score_total_ms = int((time.perf_counter() - t0) * 1000)

    per_score_ms = int(took_score_total_ms / len(scored)) if scored else 0

    # 回填：逐 item 落五指标 + 三段耗时 + 状态，再按实底的条项状态收 run 的终态
    scored_idx = 0
    latencies: list[int] = []
    async with mysql_client.get_session() as session:
        for w in work:
            item = w["item"]
            sample = w["sample"]
            metrics: dict[str, Any] = {m: None for m in _METRIC_ATTRS}
            status = RC.EVAL_ITEM_FAILED
            error = w.get("error")
            took_score_ms = 0
            if sample is not None:
                if score_rows and scored_idx < len(score_rows):
                    row = score_rows[scored_idx]
                    scored_idx += 1
                    metrics = {m: _clean_score(row.get(m)) for m in _METRIC_ATTRS}
                    took_score_ms = per_score_ms
                    # 打分跑通就算这条完成：个别指标缺值用 NULL 表达，不再另设状态
                    status = RC.EVAL_ITEM_DONE
                    error = None
                elif not error:
                    # 召回与生成都成了、却没轮到打分结果（ragas 返回行数少于样本数）：
                    # 失败必须带原因，否则详情里只剩一个说不清为什么的「失败」
                    error = "打分段没有返回这一条的结果"
            w["status"] = status
            w["error"] = error
            values = {
                "generated_answer": (w.get("answer") if sample else None),
                "contexts": (w.get("contexts") if sample else None),
                "took_recall_ms": int(w.get("took_recall_ms") or 0),
                "took_generate_ms": int(w.get("took_generate_ms") or 0),
                "took_score_ms": took_score_ms,
                "status": status, "error": error,
                **metrics,
            }
            await session.execute(update(RagEvalItem).where(
                RagEvalItem.item_id == item.item_id).values(**values))
            latencies.append(int(w.get("took_recall_ms") or 0) + int(w.get("took_generate_ms") or 0))
        # 计数以库里这一场实际的条项状态为准（含本次未重跑的历史结果），不拿本轮 work 累加
        counts = {str(st): int(cnt) for st, cnt in (await session.execute(
            select(RagEvalItem.status, func.count()).where(
                RagEvalItem.run_id == run_id).group_by(RagEvalItem.status))).all()}
        done_pairs = counts.get(RC.EVAL_ITEM_DONE, 0)
        failed_pairs = counts.get(RC.EVAL_ITEM_FAILED, 0)
        # 总数用 run 行上的快照（建 run 时就落定），拿不到才回落到实际条项数
        total = int(run.total_pairs or 0) or sum(counts.values())
        avg_latency = int(sum(latencies) / len(latencies)) if latencies else 0
        first_error = next((str(w["error"]) for w in work
                            if w.get("status") == RC.EVAL_ITEM_FAILED and w.get("error")), "")
        reason = f"：{first_error}"[:900] if first_error else "（条项没有留下原因）"
        # 一条分都没拿到 = 这场评测没产出任何结论，整单判 FAILED；只挂一部分仍算 DONE，
        # 但失败条数与原因留在 run.error 上（否则列表只能看到一个光头「已完成」）
        if done_pairs == 0:
            run_status = RC.EVAL_STATUS_FAILED
            run_error = f"{total} 个问答对没有一条拿到分{reason}"
        elif failed_pairs > 0:
            run_status = RC.EVAL_STATUS_DONE
            run_error = f"{failed_pairs}/{total} 个问答对失败{reason}"
        else:
            run_status = RC.EVAL_STATUS_DONE
            run_error = ""
        await session.execute(update(RagEvalRun).where(
            RagEvalRun.run_id == run_id).values(
            status=run_status, done_pairs=done_pairs,
            avg_latency_ms=avg_latency, error=(run_error[:1000] or None)))
        await session.commit()
    log.info("评测运行收口: run_id={} 出分={}/{} 失败={} 终态={} 平均延迟={}ms",
             run_id, done_pairs, total, failed_pairs, run_status, avg_latency)
