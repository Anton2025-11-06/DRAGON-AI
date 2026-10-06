# -*- coding: utf-8 -*-
"""多模态检索服务：按知识库类型切换召回逻辑，ES 单索引一次混排返回（SPEC §8/§9/§10.2/§11.2-5）。

三条不可让的口径
----------------
1. **上层鉴权、底层裸查**（§13.4）：可见库白名单由 ``KnowledgeBaseService.visible_ids``
   （资源 ACL + DataScope）算好后传下来，common_es 里没有权限概念，白名单为空就是空结果。
   越权请求的表现是「这些库不在你的可见范围」，而不是一行别人的切片。
2. **同一个向量空间才合成一次查询**：ES 是单索引、维度固定 1024，但**维度相同不代表
   空间相同**——文本向量模型与多模态向量模型的输出不能交叉比距离。所以按
   ``(向量模型, 库类型, 生效检索参数)`` 分组：同组合成一条 ES 请求（省往返），
   不同组各查各的，最后统一按分数排序。分组键里绝不放 kb_id，否则 20 个库就是 20 次往返。
3. **按库类型自动切召回路**（§11.2-5）：doc 走 BM25+kNN 混合；image/audio_video 只走 kNN
   ——跨模态空间里 BM25 命中的是文件名/摘要文本，和「以文搜图」不是一回事，
   混进融合分会让 score_threshold 失去可解释性。以图搜图只有 image 型支持（§8-3），
   音视频型只有文本查询（§9-3）。

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

import time
from typing import Any, Optional, Sequence

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
from service.service_rag.services.graph_service import RagGraphService
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

# 图谱附加的实体条数上限（检索页只做「顺带看一眼有哪些实体」，图谱页才有完整交互）
GRAPH_HIT_LIMIT = 5


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
                 total_limit: int = 0):
        self.login_user = login_user or {}
        self.query = str(query or "").strip()
        self.image_url = str(image_url or "").strip()
        self.chunk_type = parse_chunk_type(chunk_type)
        self.mode = parse_mode(mode)
        self.top_k = int(top_k or 0)
        self.score_threshold = score_threshold
        self.rerank = rerank
        self.vector_weight = vector_weight
        self.with_graph = bool(with_graph)
        self.total_limit = int(total_limit or 0)
        self.requested_ids = [int(i) for i in (kb_ids or []) if i]

        # 过程说明：与 hits 同等重要，用户看到"结果比预期少"时唯一能读到的解释
        self.warnings: list[str] = []
        self.vectors: dict[tuple, list[float]] = {}
        self.targets: list[dict] = []
        self.entities: list[dict] = []
        self.extra: list[dict] = []
        self.mode_label = RC.RETRIEVE_MODE_HYBRID
        self.took_ms = 0
        self._started = time.perf_counter()
        self.extra, notes = build_meta_filters(metadata_filters)
        self.warnings.extend(notes)

    # ---------- 主流程 ----------
    async def execute(self) -> list[dict]:
        """鉴权 → 分组 → 各组召回 → 库级裁剪 → 重排 → 补全出口字段。"""
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
        rows = await self._rerank(rows)
        rows.sort(key=lambda r: float(r.get("score") or 0.0), reverse=True)
        if self.total_limit:
            rows = rows[:self.total_limit]
        rows = await self._attach(rows)
        if self.with_graph:
            await self._graph(rows)
        self.took_ms = self._elapsed()
        log.info("检索完成: kb={} mode={} 候选={} 返回={} 耗时={}ms",
                 [t["kb_id"] for t in self.targets], self.mode_label, len(rows),
                 len(rows), self.took_ms)
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

        # 图搜图只有 image 型能做（§8-3）：doc 型的向量模型可能是纯文本的，
        # audio_video 型的 §9-3 只写了文本查询——拿图片去搜这两种库都是没有意义的召回。
        if self.image_url:
            if kb_type != RC.KB_TYPE_IMAGE:
                self.warnings.append(
                    f"{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}知识库不支持以图搜图，"
                    f"已跳过「{kb.kb_name}」")
                return None
        elif not self.query:
            self.warnings.append(f"知识库「{kb.kb_name}」没有可用的查询方式，已跳过")
            return None

        if is_doc:
            mode = self.mode or RC.RETRIEVE_MODE_HYBRID
            if mode == RC.RETRIEVE_MODE_KEYWORD and not self.query:
                mode = RC.RETRIEVE_MODE_VECTOR
        else:
            if self.mode in (RC.RETRIEVE_MODE_KEYWORD, RC.RETRIEVE_MODE_HYBRID):
                self.warnings.append(
                    f"{RC.KB_TYPE_LABELS.get(kb_type, kb_type)}知识库只走向量召回"
                    f"（BM25 命中的是文件名与摘要文本，不参与跨模态融合）")
            mode = RC.RETRIEVE_MODE_VECTOR

        top_k = int(self.top_k or cfg["top_k"])
        threshold = float(cfg["score_threshold"] if self.score_threshold is None
                          else self.score_threshold)
        weight = float(cfg["vector_similarity_weight"] if self.vector_weight is None
                       else self.vector_weight)
        return {
            "kb_id": int(kb.kb_id), "kb_name": kb.kb_name, "kb_type": kb_type,
            "embed_model_id": int(kb.embedding_model_id or 0),
            "rerank_model_id": int(kb.rerank_model_id or 0),
            "mode": mode, "top_k": max(1, top_k), "threshold": threshold,
            "vector_weight": weight, "keyword_boost": float(cfg["keyword_boost"]),
            "recall_size": max(int(cfg["recall_size"]), max(1, top_k)),
            "rerank": bool(cfg["rerank"]) if self.rerank is None else bool(self.rerank),
            "chunk_types": [self.chunk_type] if self.chunk_type else None,
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

    async def _graph(self, rows: list[dict]) -> None:
        """图谱附加信息（with_graph=true）：按查询词找相关实体，失败只记说明。

        只覆盖本次真正参与检索的 doc 型库——检索已经鉴过权，这里复用同一份白名单，
        不再按用户请求里的 kb_ids 去 Neo4j 里捞没授权的东西。
        """
        hit_ids = {int(r["kb_id"]) for r in rows if int(r.get("kb_id") or 0)}
        doc_ids = [t["kb_id"] for t in self.targets
                   if t["kb_type"] == RC.KB_TYPE_DOC
                   and (not hit_ids or t["kb_id"] in hit_ids)]
        if not doc_ids or not self.query:
            return
        try:
            self.entities = await RagGraphService.search(
                self.login_user, self.query, doc_ids, limit=GRAPH_HIT_LIMIT)
        except Exception as e:  # noqa: BLE001  图谱是附加信息，不该拖累主结果
            log.warning(f"图谱实体检索失败: {e}")
            self.warnings.append(f"图谱实体获取失败：{str(e)[:120]}")


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
        runner = _SearchRunner(
            login_user, kb_ids=req.kb_ids, query=req.query, image_url=req.image_url,
            chunk_type=req.chunk_type, mode=req.mode, top_k=req.top_k or 0,
            score_threshold=req.score_threshold, rerank=req.rerank,
            vector_weight=req.vector_weight, with_graph=req.with_graph)
        return RagRetrievalService._resp(runner, await runner.execute(), req)

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
            block_id=row.get("block_id"))

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
            graph={"entities": runner.entities} if req.with_graph else {})
        return resp.dump()

    @staticmethod
    async def health() -> dict:
        """检索侧存储就绪状态（开放 API 与配置面板的「存储未就绪」提示共用）。"""
        from common.common_es import es_health

        status = await es_health()
        status["vector_dim"] = RC.RAG_VECTOR_DIM
        status["defaults"] = dict(RC.RETRIEVE_DEFAULTS)
        status["recall_index"] = settings.chunk_index()
        return status


__all__ = ["RagRetrievalService", "build_meta_filters", "parse_mode", "parse_chunk_type"]
