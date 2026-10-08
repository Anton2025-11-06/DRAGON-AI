# -*- coding: utf-8 -*-
"""rag 流水线的任务编排：入队幂等、文档/知识库互斥锁、进度读写、抽取断点、状态机流转。

为什么要单独一层（而不是散在 doc_service 与 parse_service 里）
--------------------------------------------------------------
API 进程与 rag worker 进程对「这篇文档此刻跑到哪儿了」的理解必须完全一致：页面轮询的进度、
worker 写的进度、状态流转校验若分两处各拼一次字符串，迟早出现「worker 写的 key
页面读不到」。本模块就是那**唯一的 key 拼法**与**唯一的流转表**。

五类职责
--------
1. **入队**：任务名取自 rag_constant（与 worker 注册同源，不一致就是投了没人消费）；
   投递走 common_arq 的 rag 流水线，切片号按 job_id 取模，与 workflow 队列完全隔离（SPEC §4.1）。
2. **锁**：文档级 / 知识库级互斥（SET NX + TTL）。锁由 **worker 持有**，API 侧只读它来判断
   「正在处理中」，所以入队与执行不会互相卡死。
3. **进度**：两条独立的 Redis hash——向量化的 ``rag:progress:{doc_id}`` 与图谱的
   ``rag:graph:progress:{doc_id}``（``scope`` 参数选哪一条）。字段名 = ``RAG_PROGRESS_FIELDS``，
   TTL 24h。百分比由「阶段序号 + 阶段内完成比」算出，同一轮里单调不回退。
   为什么要拆两条：一篇文档可以「向量已完成、图谱正在跑」，挤在一个 hash 里时
   图谱进度会把向量化进度覆成 100%，页面上两条进度条根本无法各自说话。
4. **图谱抽取断点**：``rag:kg:ckpt:{doc_id}`` 只存**已经抽成功的批**的归一化结果，让失败的
   图谱构建下次只补没抽完的批（格式与作废口径见 service_rag.services.kg_ckpt）。
5. **状态机**：只按 ``DOC_STATUS_TRANSITIONS`` 落库，非法流转记日志并拒绝（不静默改状态）。

权威状态在 MySQL，Redis 里只有进度与抽取断点：Redis 被清空或 worker 重启都不影响
「这篇文档到底完成了没有」的判定，页面最多是进度条空白一会儿，最坏是图谱多烧一遍模型调用。
"""
from __future__ import annotations

import secrets
from datetime import datetime
from typing import Any, Optional, Sequence

from sqlalchemy import select, update

from common.common_arq.queue import (
    PIPELINE_GRAPH,
    PIPELINE_RAG,
    enqueue_job,
    next_split_number,
)
from common.common_constants import rag_constant as RC
from common.common_log.log_init import log
from common.common_permission.resource_guard import maybe_session
from common.common_redis.redis import client as redis_client
from service.service_rag.models.kb_entity import Document

# 两套进度阶段（页面上是两条分段进度条，所以必须各自一张表）
#   阶段增删只改 rag_constant.RAG_STAGES / RAG_GRAPH_STAGES，这里自动跟上
_SCOPES: dict[str, dict[str, Any]] = {
    RC.PROGRESS_SCOPE_VECTOR: {"name": RC.PROGRESS_SCOPE_VECTOR, "stages": RC.RAG_STAGES},
    RC.PROGRESS_SCOPE_GRAPH: {"name": RC.PROGRESS_SCOPE_GRAPH, "stages": RC.RAG_GRAPH_STAGES},
}
for _cfg in _SCOPES.values():
    _cfg["codes"] = [code for code, _label in _cfg["stages"]]
    _cfg["labels"] = dict(_cfg["stages"])
    # 每个阶段占的百分比区间（100/阶段数，浮点算完再取整，避免最后一段凑不满 100）
    _cfg["span"] = 100.0 / max(1, len(_cfg["codes"]))


def _scope_cfg(scope: Optional[str]) -> dict[str, Any]:
    """取某一档进度的阶段表；写错 scope 时回落到向量档（不能让一次写进度把流水线抛死）。"""
    cfg = _SCOPES.get(str(scope or RC.PROGRESS_SCOPE_VECTOR))
    if cfg is not None:
        return cfg
    log.warning(f"未登记的进度维度: {scope}（合法值 {list(_SCOPES)}），已按向量进度处理")
    return _SCOPES[RC.PROGRESS_SCOPE_VECTOR]

# 释放锁的 Lua：token 相等才删，避免把别人刚拿到的锁释放掉
_LUA_RELEASE = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
else
    return 0
end
"""

# 错误列长度（tb_document.error_msg 是 VARCHAR(1000)，落库前按它截断）
ERROR_MSG_MAX = 1000


def _raw_redis():
    """取底层 redis-py 客户端；未初始化返回 None（进度这类旁路信息可以直接降级）。"""
    return getattr(redis_client, "client", None)


def _must_redis():
    """互斥锁必须问 Redis 要答案：拿不到连接时报错，而不是当作「没人占用」放进第二个任务。"""
    raw = _raw_redis()
    if raw is None:
        raise RuntimeError("Redis 未初始化：rag 流水线需要它做互斥锁与进度（服务开 "
                           "enable_redis，worker 的 bootstrap 已初始化业务 Redis）")
    return raw


class RagTaskService:
    """rag 异步任务的任务层（全静态方法，与其它 service 的调用口径一致）"""

    # ==================== 一、入队 ====================

    @staticmethod
    def build_job_id(task: str, identity: Any, *, unique: bool = False) -> str:
        """拼 arq 的 job_id：``rag:job:{任务名}:{对象标识}``（对象标识通常是 doc_id）。

        同一对象同一任务天然幂等——重复投递会被 arq 拒掉。但**重试必须换新 id**：
        arq 会保留已完成任务的结果一段时间（keep_result），沿用旧 id 重试会被当成
        「重复投递」静默跳过，文档就永远停在排队中；``unique=True`` 给标识加时间戳避开这个坑。
        """
        base = f"{RC.RAG_JOB_PREFIX}{task}:{identity}"
        if not unique:
            return base
        return f"{base}#{int(datetime.now().timestamp() * 1000)}"

    @staticmethod
    async def enqueue(task: str, args: Optional[Sequence[Any]] = None, *,
                      job_key: Optional[Any] = None, doc_id: Optional[int] = None,
                      unique: bool = False,
                      pipeline: str = PIPELINE_RAG) -> str:
        """向 rag/graph 流水线投递一个任务，返回 job_id（落 tb_document.task_id 供回查）。

        :param task: 短任务名（``RC.RAG_TASK_*``），展开成 worker 注册的完整函数路径
        :param args: 位置参数（与 arq_tasks/tasks 里任务函数签名一致）
        :param job_key: 显式的幂等标识（删库清理用 ``purge:{kb_id}``）；与 doc_id 二选一
        :param doc_id: 文档级任务的对象标识，等价于 ``job_key=doc_id``
        :param unique: True 时每次投递都是新 job_id（失败重试、手动重跑用）
        :param pipeline: 目标流水线：文档解析走 PIPELINE_RAG，图谱构建走 PIPELINE_GRAPH
                         （两者队列与切片数配置各自独立，慢图谱不再堵解析队列）
        :raises ValueError: 任务名未登记 / 缺标识 / 重复投递被拒
        """
        if task not in RC.RAG_TASKS_ALL:
            raise ValueError(f"未登记的 rag 任务名: {task}（合法值 {RC.RAG_TASKS_ALL}）")
        identity = doc_id if doc_id is not None else job_key
        if identity is None or str(identity).strip() == "":
            raise ValueError(f"{task} 入队缺少对象标识（doc_id 或 job_key）")
        job_id = RagTaskService.build_job_id(task, identity, unique=unique)
        # 切片号按 job_id 取模：同一文档稳定落同一切片队列，扩容只加本流水线的切片数
        split_number = await next_split_number(job_id, pipeline)
        ok = await enqueue_job(RC.rag_task_name(task), list(args or []),
                               job_id=job_id, split_number=split_number,
                               pipeline=pipeline)
        if not ok:
            raise ValueError(f"任务未能入队（同一对象的同名任务还在队列里）：{job_id}")
        return job_id

    @staticmethod
    def task_of(kb_type: str) -> str:
        """按知识库类型选解析任务：doc 走解析流水线，image/audio_video 走媒体流水线。

        两条流水线分开（SPEC §4.1）：媒体流水线没有解析与分块阶段，混进一个任务函数
        就得在流水线里堆一层「这是哪种库」的分支，两侧配置项还得各自解释一遍。
        """
        kb_type = str(kb_type or "").strip().lower()
        if kb_type == RC.KB_TYPE_DOC:
            return RC.RAG_TASK_PARSE
        if kb_type in (RC.KB_TYPE_IMAGE, RC.KB_TYPE_AUDIO_VIDEO):
            return RC.RAG_TASK_MEDIA
        raise ValueError(f"不支持的知识库类型：{kb_type}")

    @staticmethod
    async def enqueue_document(doc_id: int, kb_type: str, *, retry: bool = False,
                               sidecar: str = RC.RAG_SIDECAR_AUTO, session=None) -> str:
        """投递一篇文档的解析任务，并把文档摆回排队中（写 task_id、清上一轮错误）。

        ``retry=True`` 时换新 job_id：失败重试是最常见的动作，沿用旧 id 会被 arq 的
        结果保留期判成重复投递，页面表现就是「点了重试但状态一直不动」。
        ``sidecar`` 决定要不要复用上一轮的解析产物（取值见 ``RC.RAG_SIDECAR_MODES``）：
        只改分块配置用 reuse、改了解析配置用 reparse，其余一律 auto。
        """
        if sidecar not in RC.RAG_SIDECAR_MODES:
            raise ValueError(f"未知的解析产物复用策略：{sidecar}"
                             f"（合法值 {RC.RAG_SIDECAR_MODES}）")
        task = RagTaskService.task_of(kb_type)
        # 第二个位置参数是任务选项：arq 把 args 直递任务函数，多一个默认 None 的形参就能把
        # 「要不要重解析」这类意图带到 worker，不必再开一条 Redis 旁路传参
        job_id = await RagTaskService.enqueue(task, [doc_id, {"sidecar": sidecar}],
                                              doc_id=doc_id, unique=bool(retry))
        await RagTaskService.transition(
            doc_id, RC.DOC_STATUS_PENDING, task_id=job_id, error_msg="",
            stage="download", reset_progress=True, session=session,
            total=0, message="已排队，等待处理" if not retry else "已重新排队")
        return job_id

    @staticmethod
    async def enqueue_graph(doc_id: int, *, kb_type: str = RC.KB_TYPE_DOC,
                            retry: bool = False, force: bool = False,
                            ckpt: str = RC.RAG_KG_CKPT_RESUME,
                            session=None) -> str:
        """投递文档级图谱构建（库开关自动触发与页面手动按钮都走这里）。

        ``force=True`` 才重抽已构建的文档：重跑一次要再走一遍大模型抽取，不该默认发生。
        ``ckpt`` 决定认不认上一轮的抽取断点（``RC.RAG_KG_CKPT_RESUME`` / ``RESTART``）：
        手动重跑最常见的情形就是「只差最后几批」，所以默认续抽；要全量重抽时传 restart。
        """
        if str(kb_type or "").strip().lower() != RC.KB_TYPE_DOC:
            raise ValueError("只有文档问答型知识库支持知识图谱")
        if ckpt not in RC.RAG_KG_CKPT_MODES:
            raise ValueError(f"未知的图谱抽取断点策略：{ckpt}"
                             f"（合法值 {RC.RAG_KG_CKPT_MODES}）")
        # 图谱逐批走大模型、比文档解析更耗时，投到独立的 graphflow 队列，
        # 不再占用解析 worker 的并发槽位（rag worker 已不注册 build_document_graph）
        job_id = await RagTaskService.enqueue(
            RC.RAG_TASK_GRAPH, [doc_id, {"force": bool(force), "ckpt": ckpt}],
            doc_id=doc_id, unique=bool(retry), pipeline=PIPELINE_GRAPH)
        async with maybe_session(session) as s:
            await s.execute(update(Document).where(Document.doc_id == int(doc_id)).values(
                graph_state=RC.KG_STATE_BUILDING))
            await s.commit()
        return job_id

    @staticmethod
    async def enqueue_purge(kb_id: int, doc_ids: Optional[Sequence[int]] = None) -> str:
        """投递知识库/文档删除后的底层清理（ES + Neo4j + 存储对象）。

        标识用 ``purge:{kb_id}``（+ 文档集合指纹）：删同一库的重复投递被 arq 拒掉即可，
        不需要像文档重试那样换新 id——清理本身是幂等的 delete_by_query。
        """
        ids = sorted(int(d) for d in (doc_ids or []))
        identity = f"purge:{int(kb_id)}" + (f":{len(ids)}" if ids else "")
        return await RagTaskService.enqueue(RC.RAG_TASK_KB_PURGE,
                                            [int(kb_id), ids] if ids else [int(kb_id)],
                                            job_key=identity)

    # ==================== 二、互斥锁（worker 持有，API 只读） ====================

    @staticmethod
    async def acquire_doc(doc_id: int) -> Optional[str]:
        """拿文档锁；已被占用返回 None（worker 据此直接跳过，不重复跑一遍流水线）。"""
        return await RagTaskService._acquire(RC.doc_lock_key(doc_id), RC.RAG_LOCK_TTL)

    @staticmethod
    async def release_doc(doc_id: int, token: Optional[str]) -> bool:
        return await RagTaskService._release(RC.doc_lock_key(doc_id), token)

    @staticmethod
    async def acquire_kb(kb_id: int) -> Optional[str]:
        """拿知识库级清理锁（同一库的 purge 不并发跑，避免 delete_by_query 互相干扰）。"""
        return await RagTaskService._acquire(RC.kb_lock_key(kb_id), RC.RAG_LOCK_TTL)

    @staticmethod
    async def release_kb(kb_id: int, token: Optional[str]) -> bool:
        return await RagTaskService._release(RC.kb_lock_key(kb_id), token)

    @staticmethod
    async def _acquire(key: str, ttl: int) -> Optional[str]:
        token = secrets.token_hex(8)
        got = await _must_redis().set(key, token, nx=True, ex=ttl)
        return token if got else None

    @staticmethod
    async def _release(key: str, token: Optional[str]) -> bool:
        if not token:
            return False
        raw = _raw_redis()
        if raw is None:
            return False
        try:
            return bool(await raw.eval(_LUA_RELEASE, 1, key, token))
        except Exception as e:  # noqa: BLE001
            # 释放失败不影响正确性：锁有 TTL，最坏几小时后自动放开
            log.warning(f"释放流水线锁失败（交给 TTL 兜底）{key}: {e}")
            return False

    # ==================== 三、进度（Redis hash，向量/图谱各一份） ====================

    @staticmethod
    def stage_flags(scope: str, stage: str, *, finished: bool = False) -> list[dict[str, Any]]:
        """把阶段表摊成前端分段进度条的数组：每项 {code,label,done}。

        分段渲染的口径（需求「每个阶段完成对应那段绿色、没完成蓝色」）：
        当前阶段**之前**的阶段算已完成；当前阶段本身算进行中（done=False）——
        阶段内还有 done/total 的批次比，段内百分比由 percent 体现；
        ``finished=True``（如状态已 PROCESSED）则全段算完成。
        Redis 被清、进度为空时 stage 不在表里：全段未绿（宁可不显示完不成乱显示）。
        """
        cfg = _scope_cfg(scope)
        codes: list[str] = cfg["codes"]
        idx = codes.index(stage) if stage in codes else -1
        flags: list[dict[str, Any]] = []
        for i, code in enumerate(codes):
            if finished:
                done = True
            elif idx < 0:
                done = False
            else:
                done = i < idx
            flags.append({"code": code, "label": cfg["labels"].get(code, code), "done": done})
        return flags

    @staticmethod
    async def set_progress(doc_id: int, *, scope: str = RC.PROGRESS_SCOPE_VECTOR,
                           stage: Optional[str] = None,
                           done: Optional[int] = None, total: Optional[int] = None,
                           message: Optional[str] = None, percent: Optional[int] = None,
                           reset: bool = False) -> dict[str, Any]:
        """写进度（worker 每个阶段边界调用；页面轮询读同一份）。

        :param scope: 进度维度：``vector``（解析→向量化）或 ``graph``（图谱构建），
                      两者落在不同的 Redis key 上，互不覆写
        百分比默认由「阶段序号 + 阶段内完成比」推算，并且**同一轮里不回退**：
        批次失败重试、阶段之间来回切换都不该让进度条倒着走（用户看到回退就认定出问题了）。
        ``reset=True`` 表示新一轮开始，忽略上一轮的百分比。
        """
        cfg = _scope_cfg(scope)
        key = RC.progress_key(doc_id, cfg["name"])
        raw = _raw_redis()
        if raw is None:
            return {}
        old: dict[str, Any] = {}
        try:
            if not reset:
                old = {k.decode("utf-8"): (v.decode("utf-8", "ignore")
                                           if isinstance(v, bytes) else v)
                       for k, v in (await raw.hgetall(key) or {}).items()}
        except Exception as e:  # noqa: BLE001
            log.warning(f"读取旧进度失败 doc={doc_id} scope={scope}: {e}")

        cur_stage = str(stage or old.get("stage") or "")
        if cur_stage and cur_stage not in cfg["codes"]:
            log.warning(f"未登记的进度阶段: {cur_stage}（维度 {cfg['name']}，"
                        f"见 rag_constant.RAG_STAGES / RAG_GRAPH_STAGES）")
        cur_total = _as_int(total if total is not None else old.get("total"), 0)
        cur_done = _as_int(done if done is not None else old.get("done"), 0)
        guess = _calc_percent(cfg, cur_stage, cur_done, cur_total)
        base = max(0, _as_int(old.get("percent"), 0)) if not reset else 0
        cur_percent = _as_int(percent, guess)
        cur_percent = max(cur_percent, min(base, 100))

        mapping = {
            "stage": cur_stage,
            "percent": str(max(0, min(100, cur_percent))),
            "total": str(cur_total),
            "done": str(cur_done),
            "message": str(message if message is not None else (old.get("message") or ""))[:500],
            "update_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            await raw.hset(key, mapping=mapping)
            await raw.expire(key, RC.RAG_PROGRESS_TTL)
        except Exception as e:  # noqa: BLE001
            # 进度是旁路信息：写不进去只记日志，绝不能让一篇文档因为进度失败而解析失败
            log.warning(f"写进度失败 doc={doc_id} scope={scope}: {e}")
            return mapping
        return mapping

    @staticmethod
    def _progress_view(doc_id: int, cfg: dict[str, Any], data: Optional[dict[Any, Any]],
                       *, finished: bool) -> dict[str, Any]:
        """把一份进度 hash 摊成页面要的字典（单篇读与整屏批量读共用这一份口径）。

        缺键、读失败、Redis 没起都传空字典进来：数字列照样归 0、分段数组照样全未绿，
        而不是留个空字符串让调用方去 int()——批量读里一篇文档的抖动不该把整屏打成 500。
        """
        out: dict[str, Any] = {field: "" for field in RC.RAG_PROGRESS_FIELDS}
        out["doc_id"] = int(doc_id)
        out["scope"] = cfg["name"]
        for field, value in (data or {}).items():
            name = field.decode("utf-8") if isinstance(field, bytes) else str(field)
            out[name] = (value.decode("utf-8", "ignore")
                         if isinstance(value, bytes) else value)
        for num_field in ("percent", "total", "done"):
            out[num_field] = _as_int(out.get(num_field), 0)
        cur_stage = str(out.get("stage") or "")
        out["stage_label"] = cfg["labels"].get(cur_stage, cur_stage or None)
        out["stages"] = RagTaskService.stage_flags(str(cfg["name"]), cur_stage,
                                                   finished=finished)
        if finished:
            # 已完成但 Redis 里百分比没走满（比如收尾时没再写一次进度）：按完成口径说 100
            out["percent"] = 100
        return out

    @staticmethod
    async def progress(doc_id: int, *, scope: str = RC.PROGRESS_SCOPE_VECTOR,
                       finished: bool = False) -> dict[str, Any]:
        """读单个维度的进度（缺键补空，返回字典的键与 RAG_PROGRESS_FIELDS 一致，额外附 stages 分段数组）。

        :param finished: 该维度已整体完成（向量档看 status=PROCESSED、图谱档看 graph_state=2），
                         由调用方传进来：分段数组要全部置绿时靠它，不靠 Redis 里的残留阶段名。
        """
        cfg = _scope_cfg(scope)
        raw = _raw_redis()
        data: dict[Any, Any] = {}
        if raw is not None:
            try:
                data = await raw.hgetall(RC.progress_key(doc_id, cfg["name"])) or {}
            except Exception as e:  # noqa: BLE001
                log.warning(f"读进度失败 doc={doc_id} scope={scope}: {e}")
        return RagTaskService._progress_view(doc_id, cfg, data, finished=finished)

    @staticmethod
    async def progress_pair(doc_id: int, *, vector_finished: bool = False,
                            graph_finished: bool = False) -> dict[str, Any]:
        """一次读回单个文档两个维度的进度（单篇轮询的取数入口，形状与批量读一致）。"""
        return {
            RC.PROGRESS_SCOPE_VECTOR: await RagTaskService.progress(
                doc_id, scope=RC.PROGRESS_SCOPE_VECTOR, finished=vector_finished),
            RC.PROGRESS_SCOPE_GRAPH: await RagTaskService.progress(
                doc_id, scope=RC.PROGRESS_SCOPE_GRAPH, finished=graph_finished),
        }

    @staticmethod
    async def progress_pair_many(doc_ids: Sequence[int], *,
                                 vector_finished: Optional[dict[int, bool]] = None,
                                 graph_finished: Optional[dict[int, bool]] = None,
                                 ) -> dict[int, dict[str, Any]]:
        """一次 pipeline 读回多篇文档的双维度进度：``{doc_id: 与 progress_pair 同形}``。

        整屏轮询若逐篇调 progress_pair 就是 2N 次往返（一页十行二十次），列表页每几秒
        刷一次时这部分比那条 SQL 还贵；合成一条 pipeline 后只剩一次往返。
        finished 按 doc_id 传两档各自的映射（权威来源是 MySQL 的状态列，不是 Redis 残留），
        映射里没有的文档按「未完成」出——宁可进度条不满，也不把没跑完的说成 100%。
        """
        ids: list[int] = []
        for one in doc_ids or []:
            try:
                doc_id = int(one)
            except (TypeError, ValueError):
                continue
            if doc_id not in ids:
                ids.append(doc_id)
        if not ids:
            return {}
        scopes = [RC.PROGRESS_SCOPE_VECTOR, RC.PROGRESS_SCOPE_GRAPH]
        cfgs = {scope: _scope_cfg(scope) for scope in scopes}
        finished_maps = {RC.PROGRESS_SCOPE_VECTOR: vector_finished or {},
                         RC.PROGRESS_SCOPE_GRAPH: graph_finished or {}}
        keys = [RC.progress_key(doc_id, cfg["name"])
                for doc_id in ids for cfg in cfgs.values()]
        raw = _raw_redis()
        datas: dict[str, dict[Any, Any]] = {}
        if raw is not None:
            try:
                # transaction=False：只读的一批命令不必套 MULTI/EXEC
                pipe = raw.pipeline(transaction=False)
                for key in keys:
                    pipe.hgetall(key)
                for key, data in zip(keys, await pipe.execute()):
                    datas[key] = data or {}
            except Exception as e:  # noqa: BLE001
                # 整批读失败时按「进度为空」出：比逐篇读更耐抖（一次抖动不丢整屏进度）
                log.warning(f"批量读进度失败（{len(ids)} 篇）: {e}")
        out: dict[int, dict[str, Any]] = {}
        for doc_id in ids:
            out[doc_id] = {
                scope: RagTaskService._progress_view(
                    doc_id, cfgs[scope],
                    datas.get(RC.progress_key(doc_id, cfgs[scope]["name"]), {}),
                    finished=bool(finished_maps[scope].get(doc_id)))
                for scope in scopes}
        return out

    @staticmethod
    async def clear_progress(doc_id: int, *, scope: Optional[str] = None) -> None:
        """清进度。``scope`` 缺省清两个维度（重新解析这种全量重跑）；
        只重跑图谱时传 ``graph``，不要把向量进度一并抹白。"""
        raw = _raw_redis()
        if raw is None:
            return
        scopes = [scope] if scope else list(_SCOPES)
        keys = [RC.progress_key(doc_id, _scope_cfg(one)["name"]) for one in scopes]
        await raw.delete(*keys)

    # ==================== 四、图谱抽取断点（Redis hash，整份可丢） ====================

    @staticmethod
    async def kg_ckpt_load(doc_id: int) -> dict[str, str]:
        """读整份抽取断点（``{字段名: JSON 字符串}``，含 ``meta`` 签名）。

        Redis 不可用、键不存在、读失败一律回空字典：断点是省钱手段而不是正确性依赖，
        拿不到就退化成从第一批重抽，绝不能因为旁路存储抖动把图谱构建打成失败。
        """
        raw = _raw_redis()
        if raw is None:
            return {}
        try:
            data = await raw.hgetall(RC.kg_ckpt_key(doc_id)) or {}
        except Exception as e:  # noqa: BLE001
            log.warning(f"读图谱抽取断点失败 doc={doc_id}: {e}")
            return {}
        out: dict[str, str] = {}
        for field, value in data.items():
            name = field.decode("utf-8") if isinstance(field, bytes) else str(field)
            out[name] = (value.decode("utf-8", "ignore")
                         if isinstance(value, bytes) else str(value))
        return out

    @staticmethod
    async def kg_ckpt_save(doc_id: int, fields: dict[str, str]) -> bool:
        """写断点字段（每批抽成功落一次盘，顺带把 ``meta`` 签名一起写上）。

        逐批写而不是一次写：一次写完就只是把「攒在内存里」换了个地方，
        第 40 批崩时前 39 批的模型调用照样作废，续抽也就无从谈起。
        """
        mapping = {str(k): str(v) for k, v in (fields or {}).items() if k}
        if not mapping:
            return False
        raw = _raw_redis()
        if raw is None:
            return False
        try:
            key = RC.kg_ckpt_key(doc_id)
            await raw.hset(key, mapping=mapping)
            await raw.expire(key, RC.KG_CKPT_TTL)
        except Exception as e:  # noqa: BLE001
            log.warning(f"写图谱抽取断点失败 doc={doc_id}: {e}（这一批下次只能重抽）")
            return False
        return True

    @staticmethod
    async def kg_ckpt_drop(doc_id: int) -> None:
        """删断点：写入图谱成功后（结果已落 Neo4j）、以及 restart 档开工前调用。"""
        raw = _raw_redis()
        if raw is None:
            return
        try:
            await raw.delete(RC.kg_ckpt_key(doc_id))
        except Exception as e:  # noqa: BLE001
            log.warning(f"清理图谱抽取断点失败 doc={doc_id}: {e}")

    # ==================== 五、状态机 ====================

    @staticmethod
    async def current_status(doc_id: int, *, session=None) -> Optional[str]:
        """读文档当前状态（行不存在返回 None，软删行也照读：清理链路要认它）。"""
        async with maybe_session(session) as s:
            return (await s.execute(
                select(Document.status).where(Document.doc_id == int(doc_id))
            )).scalar_one_or_none()

    @staticmethod
    def can_transition(current: Optional[str], target: str) -> bool:
        """按 DOC_STATUS_TRANSITIONS 判定流转合法性（同态视为允许，便于重复落同一状态）。"""
        if not current:
            return False
        if current == target:
            return True
        return target in (RC.DOC_STATUS_TRANSITIONS.get(current) or [])

    @staticmethod
    async def transition(doc_id: int, to_status: str, *,
                         error_msg: Optional[str] = None,
                         task_id: Optional[str] = None,
                         scope: str = RC.PROGRESS_SCOPE_VECTOR,
                         stage: Optional[str] = None,
                         total: Optional[int] = None,
                         done: Optional[int] = None,
                         message: Optional[str] = None,
                         percent: Optional[int] = None,
                         reset_progress: bool = False,
                         session=None,
                         **fields: Any) -> bool:
        """状态流转 + 同步进度（一次调用的效果 = 改状态 + 写进度，页面不会看到两者对不上）。

        :param error_msg: 非 None 即写错误列（FAILED 用）；空串表示清空上一轮错误
        :param task_id: 非 None 即写任务号（入队时写、收尾时清）
        :param scope: 随附的进度写哪一档（图谱构建失败不能把向量化进度打成 100）
        :param fields: 其它列的附加更新（如 vectorized/chunk_count/graph_state）
        :return: True=状态已落库，False=非法流转被拒绝（或文档行不存在）

        非法流转为什么只拒绝不抛错：worker 里一次非法流转（上一跑崩在半路留下的中间态
        又收到一个终态）不该把整条流水线打成失败，日志里那条 error 足够定位；
        而文档状态保持原样比被瞎改一顿更安全。
        """
        if to_status not in RC.DOC_STATUS_ALL:
            raise ValueError(f"未登记的文档状态: {to_status}")
        async with maybe_session(session) as s:
            current = (await s.execute(
                select(Document.status).where(Document.doc_id == int(doc_id))
            )).scalar_one_or_none()
            if current is None:
                log.warning(f"文档不存在，状态流转跳过: doc_id={doc_id} -> {to_status}")
                return False
            if not RagTaskService.can_transition(current, to_status):
                log.error(f"非法状态流转被拒绝: doc_id={doc_id} {current} -> {to_status}"
                          f"（合法目标 {RC.DOC_STATUS_TRANSITIONS.get(current) or []}）")
                return False
            values: dict[str, Any] = {"status": to_status, **fields}
            if error_msg is not None:
                values["error_msg"] = (error_msg or "")[:ERROR_MSG_MAX]
            if task_id is not None:
                values["task_id"] = task_id
            await s.execute(update(Document).where(
                Document.doc_id == int(doc_id)).values(**values))
            await s.commit()
        if stage is not None or message is not None or percent is not None \
                or total is not None or done is not None or reset_progress:
            await RagTaskService.set_progress(
                doc_id, scope=scope, stage=stage, total=total, done=done, message=message,
                percent=percent, reset=reset_progress)
        return True

    @staticmethod
    async def fail(doc_id: int, error: Any, *, session=None,
                   scope: str = RC.PROGRESS_SCOPE_VECTOR, **fields: Any) -> bool:
        """标记失败：状态 FAILED + 错误原因进 error_msg + 进度里也说一句。

        错误文本原样给到页面（SPEC §13.4「异常捕获」的落点）：解析失败大多是
        「模型地址不通」「文件损坏」这类必须看得见原因才能自助解决的问题。
        """
        text = str(error or "处理失败")[:ERROR_MSG_MAX]
        return await RagTaskService.transition(
            doc_id, RC.DOC_STATUS_FAILED, error_msg=text,
            scope=scope, message=text, percent=100, session=session, **fields)

    @staticmethod
    async def mark_vectorized(doc_id: int, *, chunk_count: int, session=None) -> bool:
        """解析收尾：向量已写入 ES，切片数回写（PROCESSED 是唯一的成功终态）。"""
        return await RagTaskService.transition(
            doc_id, RC.DOC_STATUS_PROCESSED, vectorized=1, chunk_count=int(chunk_count),
            percent=100, stage="index", message="已完成", session=session)


# ==================== 内部小工具 ====================

def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _calc_percent(cfg: dict[str, Any], stage: str, done: int, total: int) -> int:
    """阶段序号定基准，阶段内按完成比填区间：整体进度单调、且最后一名阶段收满 100。"""
    codes: list[str] = cfg["codes"]
    span: float = cfg["span"]
    if stage not in codes:
        return 0 if total <= 0 else 100
    idx = codes.index(stage)
    base = idx * span
    if total and total > 0:
        ratio = max(0.0, min(1.0, done / total))
    else:
        # 没给总量的阶段（如解析一篇 PDF）：只在最后一个阶段收尾时算满，其余给基准值
        ratio = 1.0 if idx == len(codes) - 1 else 0.0
    return int(min(100.0, base + span * ratio))


__all__ = ["RagTaskService", "ERROR_MSG_MAX"]
