# -*- coding: utf-8 -*-
"""多模态检索服务：按知识库类型切换召回逻辑，ES 单索引一次混排返回（SPEC §8/§9/§10.2/§11.2-5）。

四条不可让的口径
----------------
1. **上层鉴权、底层裸查**（§13.4）：可见库白名单由 ``KnowledgeBaseService.visible_ids``
   （资源 ACL + DataScope）算好后传下来，common_es 里没有权限概念，白名单为空就是空结果。
   越权请求的表现是「这些库不在你的可见范围」，而不是一行别人的切片。
2. **同一个向量空间才合成一次查询**：ES 是单索引、维度固定 1024，但**维度相同不代表
   空间相同**——文本向量模型与多模态向量模型的输出不能交叉比距离。所以按
   ``(向量模型, 库类型, 生效检索参数)`` 分组：同组合成一条 ES 请求（省往返），
   不同组各查各的，最后统一按分数排序。分组键里绝不放 kb_id，否则 20 个库就是 20 次往返。
3. **三型都能走「关键词 + 向量」两路**（需求 2/3）：doc 型的关键词一路命中切片正文；
   image / audio_video 型每个媒体只有一条切片，它的 ``content`` 就是「文件名（+ 开了
   多模态描述增强时模型写的综合描述）」，所以 BM25 在媒体库上命中的正是用户要的
   「按文件名搜」。以图搜图只有 image 型支持（§8-3），且此时没有可匹配的关键词，
   强制只走向量一路。
4. **检索参数是这一次请求的事**（需求 5/7/8）：模式、两路权重、重排模型、图谱增强
   都从请求来，库上的 retrieve_config 只是「没传时的后台默认值」；
   重排不再是代码里写死的必经环节——没选重排模型就不发那次调用。

分数与阈值
----------
- 融合分在 common_es 里算好（向量 0~1、BM25 饱和归一后加权），本层不再动它；
- score_threshold 是**库级**配置：组内先用各库阈值的最小值让 ES 少召回，
  出组后按每个库自己的阈值和 top_k 各自裁剪，绝不用 A 库的阈值筛 B 库的结果；
- 重排在阈值之后：重排分只改变顺序与展示分，不回过头筛掉已经通过阈值的候选
  （否则同一份配置在开不开重排时会给出两套完全不同的召回集）。

media_url 的出口口径
--------------------
库里（MySQL 切片行 + ES）存的是**存储对象名**，出口统一换成匿名可访问地址：
预签名地址会过期，落库等于留下一批半年后打不开的图；本地后端更不该把部署时的域名
写进数据。签名只发生在返回前端之前（见 ``_sign_or_keep``），历史数据里已经是完整 URL
的（旧解析链路写过签名地址）原样透出，不再二次签名。
"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator, Optional, Sequence

from sqlalchemy import select

from common.common_constants import rag_constant as RC
from common.common_es import chunk_source, es_client
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_permission.resource_guard import ACTION_USE
from common.common_storage import get_storage
from service.service_rag.models.kb_entity import Document, KnowledgeBase
from service.service_rag.schemas.rag_schema import (
    RetrieveHit, RetrieveReq, RetrieveResp,
)
from service.service_rag.services import rag_settings as settings
from service.service_rag.services import kg_search
from service.service_rag.services.kb_service import KnowledgeBaseService
from service.service_rag.services.rag_model import RagModelService

# 检索模式别名：工作流画布上写的是 FULLTEXT，常量口径是 KEYWORD（同一个意思的两种叫法）。
# 不在这里做归一的话，节点选「全文检索」会得到一句"未知模式"，而画布上并没有第四个选项。
_MODE_ALIASES = {
    "": None,
    "VECTOR": RC.RETRIEVE_MODE_VECTOR,
    "VEC": RC.RETRIEVE_MODE_VECTOR,
    "KNN": RC.RETRIEVE_MODE_VECTOR,
    "EMBEDDING": RC.RETRIEVE_MODE_VECTOR,
    "SEMANTIC": RC.RETRIEVE_MODE_VECTOR,
    "KEYWORD": RC.RETRIEVE_MODE_KEYWORD,
    "FULLTEXT": RC.RETRIEVE_MODE_KEYWORD,
    "FULL_TEXT": RC.RETRIEVE_MODE_KEYWORD,
    "BM25": RC.RETRIEVE_MODE_KEYWORD,
    "HYBRID": RC.RETRIEVE_MODE_HYBRID,
    "MIX": RC.RETRIEVE_MODE_HYBRID,
}

# 元数据过滤的可用字段（工作流知识检索节点的 metadataFilters）。
# 键是「去掉下划线并小写」后的写法，所以 doc_id / docId 都能进来；值 = (ES 字段, 取值方式)。
# 只有 mapping 里真建了索引的字段能进这张表：source 是 index=False 的 JSON 串，
# 对它做 term 查询不会报错、只会静默命中 0 条，比直接拒掉难查得多。
_META_FIELDS = {
    "kb": (RC.ES_FIELD_KB, "long"),
    "kbid": (RC.ES_FIELD_KB, "long"),
    "org": (RC.ES_FIELD_ORG, "long"),
    "orgid": (RC.ES_FIELD_ORG, "long"),
    "doc": (RC.ES_FIELD_DOC, "long"),
    "docid": (RC.ES_FIELD_DOC, "long"),
    "chunk": (RC.ES_FIELD_CHUNK, "long"),
    "chunkid": (RC.ES_FIELD_CHUNK, "long"),
    "chunkindex": (RC.ES_FIELD_INDEX, "int"),
    "chunktype": (RC.ES_FIELD_TYPE, "kw"),
    "titlepath": (RC.ES_FIELD_TITLE_PATH, "kw"),
    "pagenum": (RC.ES_FIELD_PAGE, "int"),
    "sheetname": (RC.ES_FIELD_SHEET, "kw"),
    "available": (RC.ES_FIELD_AVAILABLE, "bool"),
    "createtime": (RC.ES_FIELD_CREATE_TIME, "date"),
    "content": (RC.ES_FIELD_CONTENT, "text"),
}

# 支持的过滤运算符（与前端 MetadataFilter.operator 同一套取值）
_META_OPS = ("EQUALS", "NOT_EQUALS", "CONTAINS", "IN", "NOT_IN",
             "GREATER_THAN", "GREATER_OR_EQUAL", "LESS_THAN", "LESS_OR_EQUAL")

# 图谱那一路叠加进结果的行数上限（与 scale 取小：上下文堆太多等于没堆，模型只用得上开头那段）
GRAPH_ROW_LIMIT = 10

# 召回来源标记（RetrieveHit.recall）：页面要能说清这一条是被哪一路拉上来的
RECALL_CHUNK = "CHUNK"
RECALL_GRAPH = "GRAPH"

# 丢给大模型的资料规模（需求 9：两路数据叠加后整体递进去）
#   只卡上下文长度，不卡召回条数：页面左栏该列多少条还是多少条，模型那一栏拿开头几篇
QA_MAX_HITS = 12
QA_CONTEXT_MAX_CHARS = 12000


# ==================== 入参归一（公共边界，越界一律报错而不是悄悄改值） ====================

def parse_mode(value: Optional[str]) -> Optional[str]:
    """检索模式归一：空 → None（按库类型自动定），非法写法直接报错并列出可用值。"""
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    if raw not in _MODE_ALIASES:
        raise ValueError(f"未知的检索模式：{value}（可用："
                         f"{'/'.join(RC.RETRIEVE_MODES_ALL)}，或工作流侧的 FULLTEXT 写法）")
    return _MODE_ALIASES[raw]


def parse_chunk_type(value: Optional[str]) -> Optional[str]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    if raw not in RC.CHUNK_TYPES_ALL:
        raise ValueError(f"未知的模态：{value}（可用：{'/'.join(RC.CHUNK_TYPES_ALL)}）")
    return raw


def build_meta_filters(raw: Optional[Sequence[dict]]) -> tuple[list[dict], list[str]]:
    """工作流元数据过滤 → ES filter 子句。

    认不出的字段/运算符一律跳过并留下一句说明：画布上的条件是人手填的，
    因为一个拼错的字段名让整次检索 500，比"这条条件没生效"更难排查。
    """
    conds: list[dict] = []
    notes: list[str] = []
    for one in raw or []:
        if not isinstance(one, dict):
            continue
        field = str(one.get("field") or "").strip()
        op = str(one.get("operator") or "EQUALS").strip().upper()
        value = one.get("value")
        if not field:
            continue
        spec = _META_FIELDS.get(field.lower().replace("_", ""))
        if spec is None:
            notes.append(f"元数据过滤字段 {field} 不在可检索字段内，已忽略该条件")
            continue
        if op not in _META_OPS:
            notes.append(f"元数据过滤运算符 {op} 不支持，已忽略 {field} 的这条条件")
            continue
        es_field, kind = spec
        clause = _meta_clause(es_field, kind, op, value)
        if clause is None:
            notes.append(f"元数据过滤 {field} {op} 的取值无法解析（{str(value)[:40]}），已忽略")
            continue
        conds.append(clause)
    return conds, notes


def _coerce(kind: str, value: Any) -> Optional[Any]:
    """按 ES 字段类型折算过滤值（数值字段收到字符串数字也要能用）。"""
    if value is None:
        return None
    if kind in ("long", "int"):
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    if kind == "bool":
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "y", "on")
    return str(value)


def _meta_clause(es_field: str, kind: str, op: str, value: Any) -> Optional[dict]:
    if op in ("IN", "NOT_IN"):
        items = value if isinstance(value, (list, tuple, set)) else str(value or "").split(",")
        vals = [v for v in (_coerce(kind, i) for i in items) if v is not None]
        if not vals:
            return None
        clause = {"terms": {es_field: vals}}
        return clause if op == "IN" else {"bool": {"must_not": [clause]}}
    if op == "CONTAINS":
        # text 字段走分词 match；keyword 字段没有分词，只能用通配（值短才有意义）
        if kind == "text":
            return {"match": {es_field: str(value or "")}}
        return {"wildcard": {es_field: f"*{str(value or '')}*"}}
    num = _coerce(kind, value)
    if kind in ("long", "int") and num is None:
        return None
    if op == "EQUALS":
        return {"term": {es_field: num if kind in ("long", "int", "bool") else str(value or "")}}
    if op == "NOT_EQUALS":
        return {"bool": {"must_not": [
            {"term": {es_field: num if kind in ("long", "int", "bool") else str(value or "")}}]}}
    bounds = {"GREATER_THAN": "gt", "GREATER_OR_EQUAL": "gte",
              "LESS_THAN": "lt", "LESS_OR_EQUAL": "lte"}
    low = value if kind in ("date", "kw", "text") else num
    if low is None:
        return None
    return {"range": {es_field: {bounds[op]: low}}}


# ==================== 单次检索的执行体 ====================

class _SearchRunner:
    """一次检索请求的全部中间状态（分组、查询向量缓存、过程说明都活在这一份对象里）。

    不写成一大堆静态方法互传字典：同一次请求里「一个模型的查询向量只算一次」是硬要求
    （向量模型一次往返几百毫秒，按组重算等于把延迟乘上分组数），有状态才写得干净。
    """

    def __init__(self, login_user: dict, *, kb_ids: Optional[Sequence[int]] = None,
                 query: Optional[str] = None, image_url: Optional[str] = None,
                 chunk_type: Optional[str] = None, mode: Optional[str] = None,
                 top_k: int = 0, score_threshold: Optional[float] = None,
                 rerank: Optional[bool] = None, vector_weight: Optional[float] = None,
                 with_graph: bool = False, metadata_filters: Optional[Sequence[dict]] = None,
                 total_limit: int = 0, rerank_model_id: int = 0, chat_model_id: int = 0,
                 graph_depth: Any = None, graph_scale: Any = None,
                 graph_source_chunks: bool = False):
        self.login_user = login_user or {}
        self.query = str(query or "").strip()
        self.image_url = str(image_url or "").strip()
        self.chunk_type = parse_chunk_type(chunk_type)
        self.mode = parse_mode(mode)
        self.top_k = int(top_k or 0)
        self.score_threshold = score_threshold
        self.rerank = rerank
        # 需求 8：重排模型由这一次请求选，选了就重排、没选就不重排（库配置只在未传时兜底）
        self.rerank_model_id = int(rerank_model_id or 0)
        self.vector_weight = vector_weight
        self.with_graph = bool(with_graph)
        # 需求 9：图谱那一路要对话模型抽词，深度/规模是页面可传参数（越界夹到区间内）
        self.chat_model_id = int(chat_model_id or 0)
        self.graph_depth = kg_search.clamp_depth(graph_depth)
        self.graph_scale = kg_search.clamp_scale(graph_scale)
        # 需求 9（选 A）：图谱那一路默认只出精炼的实体+关系，不反查原文 chunk
        # （反查会把上下文撑大、让模型失焦）；只有显式开了才顺 MENTIONED_IN 把切片叠成 GRAPH 行
        self.graph_source_chunks = bool(graph_source_chunks)
        self.total_limit = int(total_limit or 0)
        self.requested_ids = [int(i) for i in (kb_ids or []) if i]

        # 过程说明：与 hits 同等重要，用户看到"结果比预期少"时唯一能读到的解释
        self.warnings: list[str] = []
        self.vectors: dict[tuple, list[float]] = {}
        self.targets: list[dict] = []
        # 图谱那一路的过程信息（实体/三元组/抽词结果），没开图谱时是空 dict
        self.graph: dict = {}
        self.extra: list[dict] = []
        self.mode_label = RC.RETRIEVE_MODE_HYBRID
        self.took_ms = 0
        self._started = time.perf_counter()
        self.extra, notes = build_meta_filters(metadata_filters)
        self.warnings.extend(notes)

    # ---------- 主流程 ----------
    async def execute(self) -> list[dict]:
        """鉴权 → 分组 → 各组召回 → 库级裁剪 → 图谱叠加 → 重排 → 补全出口字段。"""
        if not es_client.ready:
            # 存储没连上时报一句可执行的提示，而不是返回一个空结果集让用户去猜：
            # 「搜不到」和「服务没配好」在页面上长得一模一样，但只有后者能修
            raise ValueError("检索服务未就绪：ES 未初始化（检查 Nacos 的 es 配置段与"
                             "服务启动时的 enable_es）")
        self.targets = await self._load_targets()
        if not self.targets:
            self.took_ms = self._elapsed()
            return []
        modes = {t["mode"] for t in self.targets}
        self.mode_label = next(iter(modes)) if len(modes) == 1 else "MIXED"

        rows: list[dict] = []
        for group in self._plan():
            rows.extend(await self._recall(group))
        rows = self._per_kb_cut(rows)
        # 需求 9：关键词+向量权重分配后的一路召回，与图谱检索的一路召回**叠加**后才是结果
        rows = await self._merge_graph(rows)
        rows = await self._rerank(rows)
        rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        if self.total_limit:
            rows = rows[:self.total_limit]
        rows = await self._attach(rows)
        self.took_ms = self._elapsed()
        log.info("检索完成: kb={} mode={} 图谱={} 候选={} 返回={} 耗时={}ms",
                 [t["kb_id"] for t in self.targets], self.mode_label,
                 bool(self.graph.get("applied")), len(rows), len(rows), self.took_ms)
        return rows

    def _elapsed(self) -> int:
        return int((time.perf_counter() - self._started) * 1000)

    # ---------- 一、可见库 → 检索目标 ----------
    async def _load_targets(self) -> list[dict]:
        """取「可见 ∩ 指定」的库，摊成带生效检索参数的目标列表。

        指定了 id 却不在可见集合里时必须说清楚是哪几个被跳过：静默少几个库，
        用户会以为知识库没内容，转而去做一次毫无必要的重新解析。
        """
        async with mysql_client.get_session() as session:
            visible = await KnowledgeBaseService.visible_ids(
                self.login_user, session=session, action=ACTION_USE)
            allowed = set(visible)
            ids = [i for i in visible if not self.requested_ids or i in set(self.requested_ids)]
            skipped = [i for i in self.requested_ids if i not in allowed]
            if skipped:
                self.warnings.append(f"这些知识库不存在或你没有使用权限，已跳过：{skipped}")
            if not ids:
                if not self.requested_ids:
                    self.warnings.append("没有可用的知识库（你还没有任何一个可见的知识库）")
                return []
            rows = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id.in_(ids)))).scalars().all()
            out: list[dict] = []
            for kb in rows:
                target = self._target_of(kb)
                if target is not None:
                    out.append(target)
        return out

    def _target_of(self, kb: KnowledgeBase) -> Optional[dict]:
        """库行 → 检索目标（含本次生效的检索参数）；本次查询够不着的库在这里剔除。"""
        cfg = KnowledgeBaseService.retrieve_of(kb)
        kb_type = str(kb.kb_type or RC.KB_TYPE_DOC)
        is_doc = kb_type == RC.KB_TYPE_DOC
        label = RC.KB_TYPE_LABELS.get(kb_type, kb_type)

        # 图搜图只有 image 型能做（§8-3）：doc 型的向量模型可能是纯文本的，
        # 拿图片去搜这两种库都是没有意义的召回。
        if self.image_url:
            if kb_type != RC.KB_TYPE_IMAGE:
                self.warnings.append(
                    f"{label}知识库不支持以图搜图，已跳过「{kb.kb_name}」")
                return None
        elif not self.query:
            self.warnings.append(f"知识库「{kb.kb_name}」没有可用的查询方式，已跳过")
            return None

        # 需求 2/3：三型都能走关键词+向量，不再拿库类型硬卡住一路。
        #   以图搜图没有可匹配的关键词，只能走向量一路；
        #   没查询文本时（图搜图）选了 KEYWORD 也无意义，回到向量一路。
        mode = self.mode or RC.RETRIEVE_MODE_HYBRID
        if self.image_url or not self.query:
            mode = RC.RETRIEVE_MODE_VECTOR

        top_k = int(self.top_k or cfg["top_k"])
        threshold = float(cfg["score_threshold"] if self.score_threshold is None
                          else self.score_threshold)
        # 需求 7：单选一路就是那一路 100%；只有同时选了两路才谈百分比分配
        #   （ES 层单路为空时还会自己重归一，这里先把语义定对，不让库里的 0.7 漏到一个
        #   「只要关键词」的请求里）
        if mode == RC.RETRIEVE_MODE_VECTOR:
            weight = 1.0
        elif mode == RC.RETRIEVE_MODE_KEYWORD:
            weight = 0.0
        else:
            weight = float(cfg["vector_similarity_weight"] if self.vector_weight is None
                           else self.vector_weight)

        # 需求 8：重排不写死。请求显式关了→不重排；请求选了模型→一定用这个模型重排；
        # 两个都没给才回到库配置（勾了重排才用库上的重排模型）。
        if self.rerank is False:
            rerank_model, do_rerank = 0, False
        elif self.rerank_model_id:
            rerank_model, do_rerank = self.rerank_model_id, True
        elif self.rerank is True or cfg["rerank"]:
            rerank_model = int(kb.rerank_model_id or 0)
            do_rerank = bool(rerank_model)
        else:
            rerank_model, do_rerank = 0, False

        return {
            "kb_id": int(kb.kb_id), "kb_name": kb.kb_name, "kb_type": kb_type,
            "embed_model_id": int(kb.embedding_model_id or 0),
            "mode": mode, "top_k": max(1, top_k), "threshold": threshold,
            "vector_weight": weight, "keyword_boost": float(cfg["keyword_boost"]),
            "recall_size": max(int(cfg["recall_size"]), max(1, top_k)),
            "rerank": do_rerank, "rerank_model_id": rerank_model,
            "chunk_types": [self.chunk_type] if self.chunk_type else None,
            # 图谱增强只对 doc 型且真建过图的库生效（非 doc 型的支持列恒为 0）
            "graph_enabled": bool(is_doc and kb.graph_enabled),
            # 查询向量走哪条路：与解析侧的 embed_* 严格对应，选错等于换了个向量空间
            "kind": ("image" if self.image_url else
                     ("text_doc" if is_doc else "text_media")),
        }

    # ---------- 二、分组 ----------
    def _plan(self) -> list[dict]:
        """同模型、同类型、同参数的库合成一组，一组一条 ES 请求。

        向量路权重/BM25 放大系数/召回深度都会改变 ES 查询本身，所以它们必须在键里；
        top_k 与阈值不在键里——它们靠组内取 max/min 后逐库裁剪来保证「各库各的账」。
        """
        groups: dict[tuple, dict] = {}
        for t in self.targets:
            key = (t["embed_model_id"], t["kb_type"], t["mode"],
                   round(t["vector_weight"], 6), round(t["keyword_boost"], 6),
                   t["recall_size"], tuple(t["chunk_types"] or ()))
            g = groups.setdefault(key, {
                "kb_ids": [], "targets": [], "mode": t["mode"], "kind": t["kind"],
                "embed_model_id": t["embed_model_id"], "kb_type": t["kb_type"],
                "vector_weight": t["vector_weight"], "keyword_boost": t["keyword_boost"],
                "recall_size": t["recall_size"], "chunk_types": list(t["chunk_types"] or []),
            })
            g["kb_ids"].append(t["kb_id"])
            g["targets"].append(t)
        return list(groups.values())

    # ---------- 三、召回 ----------
    async def _recall(self, group: dict) -> list[dict]:
        kb_ids = group["kb_ids"]
        targets = group["targets"]
        size = max(int(t["top_k"]) for t in targets)
        min_score = min(float(t["threshold"]) for t in targets)
        mode = group["mode"]

        vector: Optional[list[float]] = None
        if mode != RC.RETRIEVE_MODE_KEYWORD:
            vector = await self._vector(group)
            if not vector:
                # 向量算不出来时，KEYWORD/HYBRID 还能靠 BM25 出一批结果；
                # 纯向量模式则这一组直接空（空结果 + 一句原因，胜过返回一堆无关文本）
                if mode == RC.RETRIEVE_MODE_VECTOR:
                    self.warnings.append(
                        f"知识库 {kb_ids} 走向量召回但查询向量为空，本组没有结果")
                    return []
                self.warnings.append("查询向量生成失败，本组本次只按关键词召回")

        try:
            hits = await es_client.search_hybrid(
                kb_ids, query_text=self.query or None, query_vector=vector,
                size=size, recall_size=group["recall_size"],
                vector_weight=group["vector_weight"], keyword_boost=group["keyword_boost"],
                chunk_types=group["chunk_types"] or None, min_score=min_score,
                extra=self.extra, mode=mode)
        except Exception as e:  # noqa: BLE001  一组存储侧失败不该让整次检索 500
            log.exception(f"检索失败: kb={kb_ids} mode={mode}")
            self.warnings.append(f"知识库 {kb_ids} 检索失败：{str(e)[:150]}")
            return []
        return [self._row(h) for h in hits]

    async def _vector(self, group: dict) -> list[float]:
        """取本组的查询向量（同模型同输入只算一次）。"""
        kind = group["kind"]
        value = self.image_url if kind == "image" else self.query
        key = (group["embed_model_id"], kind, value)
        if key in self.vectors:
            return self.vectors[key]
        vec: list[float] = []
        try:
            cfg = await RagModelService.require_config(
                group["embed_model_id"], "向量模型")
            if kind == "image":
                got = await RagModelService.embed_images(cfg, [await self._media_input(value)])
                vec = list(got[0]) if got else []
            elif kind == "text_media":
                vec = await RagModelService.embed_media_text(cfg, value)
            else:
                vec = await RagModelService.embed_query(cfg, value)
        except Exception as e:  # noqa: BLE001  取不到向量按降级处理，说明写进 warnings
            log.warning(f"查询向量化失败 model={group['embed_model_id']} kind={kind}: {e}")
            self.warnings.append(f"查询向量化失败：{str(e)[:150]}")
        self.vectors[key] = vec
        return vec

    async def _media_input(self, value: str) -> str:
        """图搜图的输入：完整 URL 原样给模型，对象名先换成匿名可访问地址。

        上传接口返回的是对象名，模型端点只认能直接下载的 URL——少这一步，
        「上传图搜图」在 OSS 部署下永远是 0 命中。
        """
        if "://" in value:
            return value
        try:
            url, _expires = await get_storage().public_url(value)
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"查询图片无法访问（{value}）：{str(e)[:120]}") from e
        return url

    @staticmethod
    def _row(hit: dict) -> dict:
        """ES 命中 → 内部行（字段名与 RetrieveHit 对齐，media/extra 留着出口再处理）。"""
        src = chunk_source(hit)
        extra = src.get("source") or {}
        return {
            "kb_id": int(src.get(RC.ES_FIELD_KB) or 0),
            "doc_id": int(src.get(RC.ES_FIELD_DOC) or 0),
            "chunk_id": int(src.get(RC.ES_FIELD_CHUNK) or 0),
            "chunk_index": int(src.get(RC.ES_FIELD_INDEX) or 0),
            "chunk_type": str(src.get(RC.ES_FIELD_TYPE) or RC.CHUNK_TYPE_TEXT),
            "content": str(src.get(RC.ES_FIELD_CONTENT) or ""),
            "score": float(src.get("score") or 0.0),
            "vector_score": float(src.get("vector_score") or 0.0),
            "keyword_score": float(src.get("keyword_score") or 0.0),
            "title_path": src.get(RC.ES_FIELD_TITLE_PATH) or None,
            "page_num": int(src.get(RC.ES_FIELD_PAGE) or 0),
            "sheet_name": src.get(RC.ES_FIELD_SHEET) or None,
            "media_ref": str(src.get(RC.ES_FIELD_MEDIA_URL) or ""),
            "es_id": src.get("es_id"),
            "recall": RECALL_CHUNK,
            "graph_entity": None,
            "extra": extra if isinstance(extra, dict) else {},
        }

    # ---------- 四、库级裁剪与重排 ----------
    def _per_kb_cut(self, rows: list[dict]) -> list[dict]:
        """逐库套用自己的阈值与 top_k（组内混排只是省往返，不能拿别人的尺子量结果）。"""
        by_kb = {t["kb_id"]: t for t in self.targets}
        kept: dict[int, int] = {}
        out: list[dict] = []
        dropped = 0
        for row in sorted(rows, key=lambda r: float(r.get("score") or 0.0), reverse=True):
            target = by_kb.get(int(row.get("kb_id") or 0))
            if target is None:
                continue
            if float(row.get("score") or 0.0) < float(target["threshold"]):
                dropped += 1
                continue
            if kept.get(target["kb_id"], 0) >= int(target["top_k"]):
                continue
            kept[target["kb_id"]] = kept.get(target["kb_id"], 0) + 1
            out.append(row)
        if dropped:
            self.warnings.append(f"{dropped} 条候选低于所在知识库的相似度阈值，已被过滤")
        return out

    async def _rerank(self, rows: list[dict]) -> list[dict]:
        """按重排模型分组重排（不同模型的相关性分数量纲不同，绝不跨模型混排）。"""
        if len(rows) < 2:
            return rows
        by_kb = {t["kb_id"]: t for t in self.targets}
        plain: list[dict] = []
        buckets: dict[int, list[dict]] = {}
        missing_model = 0
        for row in rows:
            target = by_kb.get(int(row.get("kb_id") or 0))
            if target is None or not target["rerank"]:
                plain.append(row)
                continue
            model_id = int(target["rerank_model_id"] or 0)
            if not model_id:
                # 库配置勾了重排但没选模型：这批保持原序，别为了一个空配置整次请求报错
                plain.append(row)
                missing_model += 1
                continue
            buckets.setdefault(model_id, []).append(row)
        if missing_model:
            self.warnings.append(f"{missing_model} 条结果所在的库开启了重排但未配置重排模型，"
                                 f"按原始融合分数排序")

        out = list(plain)
        for model_id, subset in buckets.items():
            if len(subset) < 2:
                out.extend(subset)
                continue
            try:
                cfg = await RagModelService.require_config(model_id, "重排模型")
                scores = await RagModelService.rerank(
                    cfg, self.query, [r["content"] for r in subset], top_n=len(subset))
                if not scores:
                    raise ValueError("重排模型没有返回分数")
                ranking = {int(s.get("index")): float(s.get("relevance_score") or 0.0)
                           for s in scores if isinstance(s, dict) and s.get("index") is not None}
                for i, row in enumerate(subset):
                    if i in ranking:
                        row["score"] = round(ranking[i], 6)
                subset.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
                out.extend(subset)
            except Exception as e:  # noqa: BLE001  重排是增强，失败就退回融合分数序
                log.warning(f"重排失败 model={model_id}: {e}")
                self.warnings.append(f"重排失败（模型 {model_id}），本组按原始融合分数排序："
                                     f"{str(e)[:120]}")
                out.extend(subset)
        return out

    # ---------- 五、出口补全 ----------
    async def _attach(self, rows: list[dict]) -> list[dict]:
        """补库名/文档名/媒体地址（这些不在 ES 里，或存的是内部句柄不能直接给前端）。"""
        if not rows:
            return rows
        kb_names = {t["kb_id"]: t["kb_name"] for t in self.targets}
        doc_ids = {int(r["doc_id"]) for r in rows if int(r.get("doc_id") or 0)}
        docs: dict[int, Any] = {}
        if doc_ids:
            async with mysql_client.get_session() as session:
                for rec in (await session.execute(
                        select(Document.doc_id, Document.doc_name, Document.media_duration,
                               Document.file_path).where(Document.doc_id.in_(doc_ids)))).all():
                    docs[int(rec.doc_id)] = rec
        for row in rows:
            row["kb_name"] = kb_names.get(int(row.get("kb_id") or 0))
            rec = docs.get(int(row.get("doc_id") or 0))
            row["doc_name"] = str(rec.doc_name) if rec is not None else None
            row["media_duration"] = int(rec.media_duration or 0) if rec is not None else 0
            # 媒体型库的切片可能没写 media_url（老数据），原件路径就在文档行上
            row["media_url"] = await _sign_or_keep(
                row.get("media_ref") or (str(rec.file_path or "") if rec is not None else ""))
            block_ids = (row.get("extra") or {}).get("block_ids")
            row["block_id"] = _join_block_ids(block_ids)
        return rows

    async def _merge_graph(self, rows: list[dict]) -> list[dict]:
        """图谱检索那一路叠进结果（需求 9：两路召回结果叠加组成最终检索结果）。

        叠在库级阈值之后：图谱行的分数是被命中实体的向量分，跟切片那一路的融合分不是
        一把尺子，拿切片的阈值去筛它只会把整一路筛空。同一个切片两路都召回时保留
        切片那一行（它的 vector/keyword 分与来源信息更完整），不重复占坑。

        鉴权不重做：只拿本次已参与检索的库去查图谱投影，用户请求里写了但没授权的库
        在 ``_load_targets`` 就被剔掉了，这里自然拿不到它。
        """
        self.graph = {"applied": False, "depth": self.graph_depth, "scale": self.graph_scale,
                      "entities": [], "triples": [], "terms": {}, "truncated": False}
        targets = [t for t in self.targets if t["graph_enabled"]]
        if not self.with_graph:
            return rows
        if not targets:
            self.warnings.append("开启图谱增强但这些知识库都没建过图谱（只有文档型知识库"
                                 "支持构建），本次只有关键词+向量一路")
            return rows
        if not self.chat_model_id:
            self.warnings.append("开启图谱增强必须选对话模型，本次未做图谱召回")
            return rows
        if not self.query:
            self.warnings.append("图谱那一路要先从问句里抽实体和关系，以图搜图不做图谱增强")
            return rows

        # 按向量模型分组：实体/关系投影与切片共用知识库自己的向量空间
        buckets: dict[int, list[int]] = {}
        for t in targets:
            buckets.setdefault(int(t["embed_model_id"] or 0), []).append(int(t["kb_id"]))
        payload = [{"kb_ids": ids, "embed_model_id": mid} for mid, ids in buckets.items()]
        try:
            got = await kg_search.recall(payload, question=self.query,
                                         chat_model_id=self.chat_model_id,
                                         depth=self.graph_depth, scale=self.graph_scale,
                                         with_context=self.graph_source_chunks)
        except Exception as e:  # noqa: BLE001  图谱是叠加项，挂了不影响切片那一路
            log.warning(f"图谱增强检索失败: {e}")
            self.warnings.append(f"图谱增强检索失败：{str(e)[:150]}")
            return rows
        self.graph.update({"applied": True,
                           "entities": got.get("entities") or [],
                           "triples": got.get("triples") or [],
                           "terms": got.get("terms") or {},
                           "truncated": bool(got.get("truncated"))})
        self.warnings.extend(got.get("warnings") or [])
        if not self.graph["entities"]:
            self.warnings.append("图谱那一路没命中任何实体（问句里没出现图谱里存过的名词）")
        sources = got.get("sources") or []
        if not sources:
            return rows

        by_es = {str(s.get("es_id") or ""): s for s in sources if s.get("es_id")}
        try:
            hits = await es_client.get_chunks(list(by_es))
        except Exception as e:  # noqa: BLE001  原文取不到只是这一路不叠加，实体与三元组仍在
            log.warning(f"图谱原文切片读取失败: {e}")
            self.warnings.append(f"图谱原文切片读取失败：{str(e)[:120]}")
            return rows
        allowed = {int(t["kb_id"]) for t in self.targets if t["graph_enabled"]}
        seen = {str(r.get("es_id") or "") for r in rows}
        extra: list[dict] = []
        for hit in hits:
            es_id = str(hit.get("es_id") or "")
            if not es_id or es_id in seen:
                continue
            src = chunk_source(hit)
            if int(src.get(RC.ES_FIELD_KB) or 0) not in allowed:
                continue
            # 切片被停用/软删后图谱里的实体还在，不能靠图谱把它重新召回来
            if not bool(src.get(RC.ES_FIELD_AVAILABLE, True)):
                continue
            if int(src.get(RC.ES_FIELD_DELETED) or 0):
                continue
            one = by_es.get(es_id) or {}
            row = self._row(hit)
            row["recall"] = RECALL_GRAPH
            row["graph_entity"] = str(one.get("entity") or "") or None
            row["score"] = float(one.get("score") or 0.0)
            extra.append(row)
            seen.add(es_id)
        limit = max(1, min(GRAPH_ROW_LIMIT, self.graph_scale))
        return rows + extra[:limit]


async def _sign_or_keep(name: str) -> Optional[str]:
    """对象名 → 匿名可访问地址；已经是完整 URL 的原样透出，拿不到地址返回 None。"""
    value = str(name or "").strip()
    if not value:
        return None
    if "://" in value:
        return value
    try:
        url, _expires = await get_storage().public_url(value)
        return url
    except Exception as e:  # noqa: BLE001  媒体取不到只影响这一条的预览
        log.debug(f"媒体签名失败 {value}: {e}")
        return None


def _join_block_ids(value: Any) -> Optional[str]:
    """ES source 里的 block_ids 列表 → 展示串（切片管理页与检索结果用同一种写法）。"""
    if isinstance(value, (list, tuple)):
        joined = ",".join(str(v) for v in value if v)
        return joined[:64] or None
    if value:
        return str(value)[:64]
    return None


def qa_context(rows: Sequence[dict], graph: Optional[dict] = None) -> str:
    """检索行 + 图谱三元组 → 递进问答 prompt 的资料段（〔N〕序号与左栏展示同序）。

    带来源标识而不是只堆正文：模型要说「依据资料〔2〕」，页面才能把这句话链回具体切片；
    图谱那一路单独附一段三元组（它们不在 rows 里，是「谁与谁有什么关系」这一层信息，
    拿切片正文去回答「A 和 B 是什么关系」本就答不准）。
    总长度卡住：超了从末尾丢，丢的是「多一条参考」，而不是让一个长 prompt 直接报错。
    """
    parts: list[str] = []
    used = 0
    for i, row in enumerate(rows[:QA_MAX_HITS], start=1):
        head = " / ".join(x for x in (str(row.get("kb_name") or ""),
                                       str(row.get("doc_name") or ""),
                                       str(row.get("title_path") or "")) if x)
        body = str(row.get("content") or "").strip()
        if not body:
            continue
        where = f"〔{i}〕"
        if head:
            where = f"{where}（{head}）"
        if str(row.get("recall") or "") == RECALL_GRAPH:
            where = f"{where} 图谱命中实体：{row.get('graph_entity') or ''}"
        block = f"{where}\n{body}"
        if used + len(block) > QA_CONTEXT_MAX_CHARS:
            break
        parts.append(block)
        used += len(block)
    triples = [str(t) for t in ((graph or {}).get("triples") or []) if t]
    if triples and parts:
        # 三元组只补到剩下的那点空间里（上限四十条）：它是「谁与谁有什么关系」的索引，
        # 不该把切片的资料空间全占了
        room = max(0, QA_CONTEXT_MAX_CHARS - used)
        acc = 0
        picked: list[str] = []
        for one in triples[:40]:
            acc += len(one) + 1
            if acc > room:
                break
            picked.append(one)
        if picked:
            parts.append("〔图谱关系〕" + "；".join(picked))
    return "\n\n".join(parts)


def _sse_frame(frame_type: str, **payload: Any) -> str:
    """一帧 SSE（自定义 JSON 帧，不走 OpenAI 协议）。

    前端那个 SSE 封装只把解码后的**原始文本块**交给回调（见 request-client/sse.ts），
    不拆 event/data，所以每帧自带 type，由页面自己分发；ensure_ascii=False 否则
    中文会被转成一堆 unicode 转义序列，排查时谁也看不懂。
    """
    body = {"type": frame_type, **payload}
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


# ==================== 对外服务 ====================

class RagRetrievalService:
    """知识库检索门面（检索页 / 开放 API / 工作流知识检索节点共用这一套召回）"""

    @staticmethod
    async def search(login_user: dict, req: RetrieveReq) -> dict:
        """多模态检索接口（SPEC §11.2-5：按知识库类型自动切换召回逻辑）。

        返回体里的 took_ms 与 warnings 不是装饰：召回质量出问题（模型选了纯文本的、
        阈值太高把结果筛空、某个库压根没授权）时，这两处是唯一的线索来源。
        """
        if not str(req.query or "").strip() and not str(req.image_url or "").strip():
            raise ValueError("请输入查询文本，或上传图片以图搜图")
        RagRetrievalService._check_graph(req)
        runner = RagRetrievalService._runner(login_user, req)
        return RagRetrievalService._resp(runner, await runner.execute(), req)

    @staticmethod
    def _check_graph(req: RetrieveReq) -> None:
        """需求 9：开了图谱增强就必须选对话模型，不能空着（图谱那一路靠它抽实体和关系）。"""
        if req.with_graph and not int(req.chat_model_id or 0):
            raise ValueError("开启知识图谱增强必须选择对话模型："
                             "图谱那一路要先让大模型从问题里抽出实体和关系")

    @staticmethod
    def _runner(login_user: dict, req: RetrieveReq) -> _SearchRunner:
        """请求 → 执行体（检索页与流式问答共用，避免两处传参传漏一个字段）。"""
        return _SearchRunner(
            login_user, kb_ids=req.kb_ids, query=req.query, image_url=req.image_url,
            chunk_type=req.chunk_type, mode=req.mode, top_k=req.top_k or 0,
            score_threshold=req.score_threshold, rerank=req.rerank,
            rerank_model_id=req.rerank_model_id or 0, vector_weight=req.vector_weight,
            with_graph=req.with_graph, chat_model_id=req.chat_model_id or 0,
            graph_depth=req.graph_depth, graph_scale=req.graph_scale,
            graph_source_chunks=req.graph_source_chunks)

    @staticmethod
    async def search_rows(login_user: dict, *, kb_ids: Sequence[int], query: str = "",
                          image_url: str = "", top_k: int = 0,
                          score_threshold: Optional[float] = None,
                          retrieval_mode: Optional[str] = None,
                          metadata_filters: Optional[Sequence[dict]] = None,
                          chunk_type: Optional[str] = None,
                          rerank: Optional[bool] = None) -> list[dict]:
        """平铺召回结果（工作流知识检索节点的 kb_searcher 用这一颗）。

        与 ``search`` 的区别只在形态：节点要的是 ``[{content, score, ...}]``，
        并且它的 topK 语义是「总共返回几条」而不是「每个库返回几条」，
        所以这里带上 total_limit，其余鉴权、分组、融合逻辑与检索页完全一致
        ——两条路跑出不同的召回集，是知识库最难自证清白的问题。
        """
        if not str(query or "").strip() and not str(image_url or "").strip():
            raise ValueError("检索词为空：请先让上游节点产出要检索的内容")
        runner = _SearchRunner(
            login_user, kb_ids=kb_ids, query=query, image_url=image_url,
            chunk_type=chunk_type, mode=retrieval_mode, top_k=top_k or 0,
            score_threshold=score_threshold, rerank=rerank,
            metadata_filters=metadata_filters, total_limit=top_k or 0)
        rows = await runner.execute()
        return [RagRetrievalService._hit(row).dump() for row in rows]

    # ---------- 序列化 ----------
    @staticmethod
    def _hit(row: dict) -> RetrieveHit:
        """内部行 → 前端契约（未列出的键（es_id/extra/media_ref）都是过程字段，不外泄）。"""
        return RetrieveHit(
            kb_id=int(row.get("kb_id") or 0), kb_name=row.get("kb_name"),
            doc_id=int(row.get("doc_id") or 0), doc_name=row.get("doc_name"),
            chunk_id=int(row.get("chunk_id") or 0),
            chunk_index=int(row.get("chunk_index") or 0),
            chunk_type=row.get("chunk_type") or RC.CHUNK_TYPE_TEXT,
            content=row.get("content") or "",
            score=float(row.get("score") or 0.0),
            vector_score=float(row.get("vector_score") or 0.0),
            keyword_score=float(row.get("keyword_score") or 0.0),
            title_path=row.get("title_path"), page_num=int(row.get("page_num") or 0),
            sheet_name=row.get("sheet_name"), media_url=row.get("media_url"),
            media_duration=int(row.get("media_duration") or 0),
            block_id=row.get("block_id"),
            recall=str(row.get("recall") or RECALL_CHUNK),
            graph_entity=row.get("graph_entity"))

    @staticmethod
    def _resp(runner: _SearchRunner, rows: list[dict], req: RetrieveReq) -> dict:
        hits = [RagRetrievalService._hit(r) for r in rows]
        # 语义是「实际参与检索的库」：一条都没命中也要如实列出，
        # 否则页面会把「阈值得太高」显示成「这些库没在检索范围内」
        searched = sorted({t["kb_id"] for t in runner.targets})
        resp = RetrieveResp(
            query=runner.query or None, mode=runner.mode_label, total=len(hits),
            took_ms=runner.took_ms, kb_ids=searched, hits=hits,
            warnings=runner.warnings,
            graph=dict(runner.graph) if req.with_graph else {})
        return resp.dump()

    @staticmethod
    def ask_stream(login_user: dict, req: RetrieveReq) -> AsyncIterator[str]:
        """检索 + 大模型问答的流式输出（需求 9：两路数据丢给大模型，流式回）。

        一条连接到底：先出一帧 meta（左栏的检索结果），再逐字出 delta（右栏的回答）。
        分两次请求（先检索再问答）会跑两套召回集，左右两栏对不上时谁也说不清哪边错。
        不做记忆：每次都是一句带资料的独立提问，不读也不写任何会话历史。

        失败不往外抛：校验与检索都在首帧之前，而首帧一发状态码就定在 200 了，
        此时抛 ValueError 只会得到一个断流，前端只能显示「连接断开」——所以一律
        翻译成一帧 error，把原因写在页面上。
        """
        async def _stream() -> AsyncIterator[str]:
            try:
                if not str(req.query or "").strip():
                    raise ValueError("知识库问答需要输入问题文本")
                if not int(req.chat_model_id or 0):
                    raise ValueError("请先选择对话模型再问知识库")
                RagRetrievalService._check_graph(req)
                runner = RagRetrievalService._runner(login_user, req)
                rows = await runner.execute()
                yield _sse_frame("meta", **RagRetrievalService._resp(runner, rows, req))
                context = qa_context(rows, runner.graph)
                if not context:
                    # 没资料就不让模型开口：无依据的自由发挥在知识库页面上看起来像「答对了」，
                    # 但它与这个库没有任何关系，比拒答危险得多
                    raise ValueError("没有检索到可用资料，无法依据知识库回答"
                                     "（请换个说法、放宽阈值或确认开启的库已解析完成）")
                cfg = await RagModelService.require_config(req.chat_model_id, "对话模型")
                prompt = settings.build_rag_qa_prompt(runner.query, context)
                async for piece in RagModelService.chat_stream(cfg, prompt):
                    yield _sse_frame("delta", content=piece)
                yield _sse_frame("done", took_ms=runner.took_ms, citations=len(rows))
            except Exception as e:  # noqa: BLE001  流里只能有一帧 error，然后收场
                log.warning(f"知识库流式问答失败: {e}")
                yield _sse_frame("error", message=str(e)[:300])

        return _stream()

    @staticmethod
    async def health() -> dict:
        """检索侧存储就绪状态（开放 API 与配置面板的「存储未就绪」提示共用）。"""
        from common.common_es import es_health

        status = await es_health()
        status["vector_dim"] = RC.RAG_VECTOR_DIM
        status["defaults"] = dict(RC.RETRIEVE_DEFAULTS)
        status["recall_index"] = settings.chunk_index()
        return status


__all__ = ["RagRetrievalService", "build_meta_filters", "parse_mode", "parse_chunk_type",
           "qa_context", "RECALL_CHUNK", "RECALL_GRAPH"]
