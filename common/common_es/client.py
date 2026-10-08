# -*- coding: utf-8 -*-
"""Elasticsearch 全局异步单例客户端：切片写入 + BM25/向量混合检索。

设计约束（改代码前先对齐）
--------------------------
1. **全局单例 + 纯异步**：AsyncElasticsearch 自带连接池，进程内只建一次
   （FastAPI lifespan / arq worker bootstrap 各自调 `init_es`），
   任何方法都是协程，绝不在请求线程里做同步等待（§13.5 高并发规范）。
2. **底层裸查**：本模块没有任何权限概念，只认调用方传下来的 `kb_ids` 白名单。
   白名单为空 = 一个都不给查（返回空结果而不是查全库），这是防越权的兜底。
3. **一索引一维度**：dense_vector 的 dims 在建索引时固定 1024，写进别的维度由 ES 直接报错，
   代码不再重复校验；建索引幂等（已存在即跳过并核对维度）。
4. **融合分数口径**：ES 的 knn 分是 (1+cos)/2 落在 0~1；BM25 是无上界量纲。
   两路各自归一后按权重融合，才能让知识库配置里的 score_threshold（0~1）在同一把尺子上。

分数归一（本文件唯一需要解释的算法）
------------------------------------
- vector_score = knn 原始分（0~1，余弦相似度）
- keyword_score = s / (s + KW_SATURATION)，s = BM25 原始分 × keyword_boost
  取饱和函数而不是「除以本路最高分」：后者会让纯关键词检索的最高命中恒等于 1.0，
  score_threshold 在这一模式下形同虚设；饱和函数逐条独立，单调且有界。
- score = (vector_weight × vector_score + keyword_weight × keyword_score) / 权重和
  只有一路有结果时按那一路的归一分给（不会因缺一路被腰斩）。
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Iterable, Optional, Sequence

from common.common_constants import rag_constant as RC
from common.common_es.index_mapping import (DEFAULT_DIM, DEFAULT_REPLICAS, DEFAULT_SHARDS,
                                           INDEX_NAME, build_mapping, check_dim)
from common.common_es.kg_index_mapping import KG_INDEX_NAME, build_kg_mapping
from common.common_log.log_init import log

# 客户端依赖可缺：workflow / login 等服务不需要 ES，没装包时本模块仍应可 import（
# 只在真要用时报一句「pip install elasticsearch」，而不是把整个 service_rag 拖垮）。
# 未安装时用一组永不命中的占位异常类顶上，下面的 except 子句照旧成立。
try:
    from elasticsearch import ApiError, AsyncElasticsearch, AuthenticationException
    from elasticsearch import ConnectionError as EsConnectionError
    from elasticsearch import NotFoundError

    HAS_ES = True
    ES_INSTALL_HINT = ""
except ImportError as e:  # noqa: BLE001
    AsyncElasticsearch = None

    class ApiError(Exception):
        """占位（未安装 elasticsearch 包）。"""

    class NotFoundError(ApiError):
        """占位（未安装 elasticsearch 包）。"""

    class AuthenticationException(ApiError):
        """占位（未安装 elasticsearch 包）。"""

    class EsConnectionError(Exception):
        """占位（未安装 elasticsearch 包）。"""

    HAS_ES = False
    ES_INSTALL_HINT = f'未安装 elasticsearch 客户端（{e}）：pip install "elasticsearch>=8.13"'

# BM25 归一饱和常数：原始分 8 附近映射到 0.5（中文分词后的短句命中普遍落在 5~20 区间）
KW_SATURATION = 8.0
# 单次 delete_by_query / update_by_query 的批次上限（一次干完会把请求挂到超时）
BULK_SLICE = 1000
DEFAULT_TIMEOUT = 60
DEFAULT_RECALL_SIZE = RC.RETRIEVE_DEFAULTS["recall_size"]


class CustomError(Exception):
    """ES 层的业务可读错误（未初始化/索引缺失/配置非法），交给统一异常处理器出 400/500。"""


# ==================== 序列化小工具（sidecar / source 字段用） ====================
def to_json(value: Any) -> Optional[str]:
    """dict/list → JSON 串；已经是字符串就当已序列化，原样存（避免二次转义）。"""
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        log.warning("ES source 字段序列化失败，按空值写入")
        return None


def from_json(value: Any) -> dict:
    """JSON 串 → dict；脏数据/非对象一律回空 dict（读侧永远不该因为一个扩展字段崩）。"""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, (bytes, bytearray)):
        value = value.decode("utf-8", "ignore")
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {RC.ES_FIELD_SOURCE: parsed}


# ==================== 过滤条件拼装 ====================
def kb_filter(kb_ids: Sequence[int]) -> dict:
    """知识库白名单（SPEC §10.2 强制前置条件）。"""
    return {"terms": {RC.ES_FIELD_KB: [int(i) for i in kb_ids]}}


def base_filters(kb_ids: Sequence[int], *, chunk_types: Optional[Sequence[str]] = None,
                 doc_ids: Optional[Sequence[int]] = None, org_id: Optional[int] = None,
                 include_invisible: bool = False,
                 extra: Optional[Sequence[dict]] = None) -> list[dict]:
    """检索/删除公用的 filter 集合：kb 白名单 ∪ 未删 ∪ 可用 ∪ 模态 ∪ 调用方附加条件。

    :param include_invisible: True 时不加 available/未删 过滤（管理侧统计、清理链路用）
    :param extra: 业务层的元数据过滤（工作流知识检索节点的 metadataFilters 原样传下来）
    """
    conds: list[dict] = [kb_filter(kb_ids)]
    if not include_invisible:
        conds.append({"term": {RC.ES_FIELD_DELETED: 0}})
        conds.append({"bool": {"should": [{"term": {RC.ES_FIELD_AVAILABLE: True}},
                                         {"bool": {"must_not": {
                                             "exists": {"field": RC.ES_FIELD_AVAILABLE}}}}],
                               "minimum_should_match": 1}})
    if chunk_types:
        conds.append({"terms": {RC.ES_FIELD_TYPE: [str(t) for t in chunk_types]}})
    if doc_ids:
        conds.append({"terms": {RC.ES_FIELD_DOC: [int(d) for d in doc_ids]}})
    if org_id is not None:
        conds.append({"term": {RC.ES_FIELD_ORG: int(org_id)}})
    for one in extra or []:
        if one:
            conds.append(one)
    return conds


def _hits_of(resp: dict) -> list[dict]:
    """ES 响应 → [{es_id, score, _source}]（上层只看这一种结构，不碰 ES 原生字段名）。"""
    out: list[dict] = []
    for h in ((resp or {}).get("hits") or {}).get("hits") or []:
        out.append({"es_id": h.get("_id"), "score": h.get("_score"),
                    "_source": h.get("_source") or {}})
    return out


# ==================== 配置读取（Nacos 的 es: 段） ====================
# 口径只有一套：url 必填，认证二选一（api_key 或 username/password），两个都不给就
# 不认证（内网未开 security 的集群就是这种）；剩下的键没填就走本模块与常量默认。
# YAML 里注释掉的键根本不存在、留空的键是 ""：两种情形都当「没填」，不再逐个判空串。

def _text(cfg: dict, *keys: str) -> str:
    """按顺序取第一个有值的字符串（已去首尾空白）；都没给返回空串。"""
    for key in keys:
        value = cfg.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _num(cfg: dict, key: str, default: int) -> int:
    """取整数；没填、填了非数字都落回默认（配置写错不该变成一个看不懂的启动异常）。"""
    try:
        return int(str(cfg.get(key)).strip())
    except (TypeError, ValueError):
        return default


def _bool(cfg: dict, key: str, default: bool) -> bool:
    """YAML 里可能是真布尔，也可能是 "false"/"0" 串（Nacos 上改配置经常整段贴字符串）。"""
    value = cfg.get(key)
    if value is None or str(value).strip() == "":
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


# ==================== 客户端 ====================
class AsyncEsClient:
    """ES 异步单例：进程内一个实例，连接池由 AsyncElasticsearch 自己管。"""

    def __init__(self) -> None:
        self._es: Optional[AsyncElasticsearch] = None
        self._cfg: dict[str, Any] = {}
        self._index: str = INDEX_NAME
        # 图谱向量投影索引（需求 9）：与切片索引共用一个客户端连接，两个索引名
        self._kg_index: str = KG_INDEX_NAME
        self._dim: int = DEFAULT_DIM
        self._loop_id: Optional[int] = None

    # ---------- 生命周期 ----------
    @property
    def ready(self) -> bool:
        return self._es is not None

    @property
    def index(self) -> str:
        return self._index

    @property
    def kg_index(self) -> str:
        return self._kg_index

    @property
    def dim(self) -> int:
        return self._dim

    async def init(self, cfg: Optional[dict] = None) -> None:
        """按 Nacos 的 `es:` 段建客户端（幂等：重复调用先关旧的再建）。

        只做三件事：配置翻成构造参数（``_client_kwargs``）、建连接、探一次连通性
        ——起服务时报错远好于第一篇文档解析到一半才报错；建索引归 ``ensure_index``。
        """
        if not HAS_ES:
            raise CustomError(ES_INSTALL_HINT)
        cfg = dict(cfg or {})
        kw = self._client_kwargs(cfg)
        await self.close()
        self._es = AsyncElasticsearch(**kw)
        self._cfg = cfg
        self._index = _text(cfg, "index") or INDEX_NAME
        self._kg_index = _text(cfg, "kg_index") or KG_INDEX_NAME
        self._loop_id = _running_loop_id()
        if self._index != INDEX_NAME:
            # 允许配置写别的名字，但常量口径要一致，否则写入与检索分两个索引
            log.warning(f"es.index={self._index} 与常量 {INDEX_NAME} 不一致：按配置值使用，"
                        f"请确认历史数据也在这个索引里")
        try:
            info = await self._es.info()
            log.info(f"ES 已连接: hosts={kw['hosts']} version="
                     f"{((info.get('version') or {}).get('number'))}")
        except (EsConnectionError, AuthenticationException, ApiError) as e:
            await self.close()
            raise CustomError(f"ES 连接失败（{kw['hosts']}）：{_reason(e)}") from e

    async def close(self) -> None:
        if self._es is not None:
            try:
                # 8.x 推荐 aclose()，close() 仍存在但已标废弃：两个名字都接，不锁小版本
                await (getattr(self._es, "aclose", None) or self._es.close)()
            except Exception as e:  # noqa: BLE001  退出路径上的失败不影响关停
                log.warning(f"ES 客户端关闭异常（忽略）: {e}")
            self._es = None
            self._loop_id = None

    def _client(self) -> AsyncElasticsearch:
        """取客户端；顺带处理「同一个单例被搬到另一个事件循环」这一类致命误用。"""
        if not HAS_ES:
            raise CustomError(ES_INSTALL_HINT)
        if self._es is None:
            raise CustomError("ES 未初始化：服务需在 bootstrap 里开 enable_es，"
                              "脚本需先 await common_es.init_es(cfg)")
        loop_id = _running_loop_id()
        if self._loop_id is not None and loop_id != self._loop_id:
            # 连接池属于创建它的那个 loop，跨 loop 复用会随机报 "attached to a different loop"
            log.error(f"ES 客户端被跨事件循环复用（loop {self._loop_id} → {loop_id}），"
                      f"重建连接池：请检查是否在多个 loop 里共用了同一进程实例")
            self._es = AsyncElasticsearch(**self._client_kwargs(self._cfg))
            self._loop_id = loop_id
        return self._es

    def _client_kwargs(self, cfg: dict) -> dict[str, Any]:
        """一份 es 配置 → AsyncElasticsearch 构造参数（init 与跨 loop 重建共用这一处）。

        地址支持逗号分隔多节点；认证按 api_key > username/password 的顺序取。
        """
        url = _text(cfg, "url", "hosts", "host")
        if not url:
            raise CustomError("es.url 未配置：Nacos 的 es 段至少要给出 url")
        hosts = [one.strip() for one in url.replace(";", ",").split(",") if one.strip()]
        kw: dict[str, Any] = {
            "hosts": hosts[0] if len(hosts) == 1 else hosts,
            "request_timeout": float(_num(cfg, "request_timeout", DEFAULT_TIMEOUT)),
            "max_retries": _num(cfg, "max_retries", 2),
            "retry_on_timeout": _bool(cfg, "retry_on_timeout", True),
        }
        api_key = _text(cfg, "api_key", "apikey")
        username = _text(cfg, "username", "user")
        if api_key:
            kw["api_key"] = api_key
        elif username:
            kw["basic_auth"] = (username, _text(cfg, "password", "pass"))
        if "verify_certs" in cfg:
            # 没写这个键就不递给客户端（用 elasticsearch 库自己的默认，不抢语义）
            kw["verify_certs"] = _bool(cfg, "verify_certs", True)
        ca_certs = _text(cfg, "ca_certs")
        if ca_certs:
            kw["ca_certs"] = ca_certs
        return kw

    # ---------- 索引 ----------
    async def ensure_index(self, dim: Optional[int] = None) -> bool:
        """幂等建索引；返回本次是否新建。已存在则核对向量维度，不一致直接报错。

        dim 只允许等于 1024（SPEC §10.1 固定维度），传别的值在 check_dim 里就被拒。
        """
        want = check_dim(dim or DEFAULT_DIM)
        es = self._client()
        if await es.indices.exists(index=self._index):
            got = await self._vector_dim(self._index)
            if got and got != want:
                raise CustomError(f"索引 {self._index} 的向量维度是 {got}，与要求的 {want} 不符："
                                  f"ES 不支持改 dims，需要迁移数据重建索引")
            self._dim = got or want
            return False
        body = build_mapping(want, shards=_num(self._cfg, "shards", DEFAULT_SHARDS),
                             replicas=_num(self._cfg, "replicas", DEFAULT_REPLICAS),
                             analyzer=_text(self._cfg, "analyzer") or None)
        await es.indices.create(index=self._index, body=body)
        self._dim = want
        log.info(f"ES 索引已创建: {self._index} dim={want}")
        return True

    async def ensure_kg_index(self, dim: Optional[int] = None) -> bool:
        """幂等建图谱向量投影索引；返回本次是否新建（维度必须与切片索引同空间）。

        与 ensure_index 一样只在启动钩子里调一次。投影索引可以空着（没建过图谱的库
        本来就没有向量），检索侧对「索引不存在」报的 CustomError 由上层按「未启用」降级。
        """
        want = check_dim(dim or DEFAULT_DIM)
        es = self._client()
        if await es.indices.exists(index=self._kg_index):
            got = await self._vector_dim(self._kg_index)
            if got and got != want:
                raise CustomError(f"索引 {self._kg_index} 的向量维度是 {got}，与要求的 {want} 不符："
                                  f"与切片不同维等于换向量空间，问句向量查不动实体")
            self._dim = got or want
            return False
        body = build_kg_mapping(want, shards=_num(self._cfg, "shards", DEFAULT_SHARDS),
                                replicas=_num(self._cfg, "replicas", DEFAULT_REPLICAS),
                                analyzer=_text(self._cfg, "analyzer") or None)
        await es.indices.create(index=self._kg_index, body=body)
        self._dim = want
        log.info(f"ES 图谱向量索引已创建: {self._kg_index} dim={want}")
        return True

    async def _vector_dim(self, index: Optional[str] = None) -> int:
        try:
            mapping = await self._client().indices.get_mapping(index=index or self._index)
        except (NotFoundError, ApiError):
            return 0
        props = _mapping_props(mapping)
        spec = props.get(RC.ES_FIELD_EMBED) or props.get(RC.KG_FIELD_EMBED) or {}
        return int(spec.get("dims") or 0)

    async def health(self) -> dict:
        """集群 + 索引状态（供 /health 聚合与页面「存储未就绪」提示）。"""
        out: dict[str, Any] = {"ok": False, "index": self._index, "dim": self._dim,
                               "kg_index": self._kg_index, "cluster": None, "docs": 0}
        if not self.ready:
            out["error"] = "ES 未初始化"
            return out
        es = self._client()
        try:
            info = await es.info()
            out["cluster"] = ((info.get("version") or {}).get("number"))
            hs = await es.cluster.health(index=self._index)
            out["status"] = hs.get("status")
        except (EsConnectionError, ApiError) as e:
            out["error"] = _reason(e)
            return out
        try:
            if await es.indices.exists(index=self._index):
                cnt = await es.count(index=self._index)
                out["docs"] = int(cnt.get("count") or 0)
                out["ok"] = True
            else:
                out["error"] = f"索引不存在: {self._index}"
        except ApiError as e:
            out["error"] = _reason(e)
        # 图谱投影是可选件：没建过就不报 error，只给 kg_docs=0（不影响切片检索的 ok）
        try:
            if await es.indices.exists(index=self._kg_index):
                out["kg_docs"] = int((await es.count(index=self._kg_index)).get("count") or 0)
            else:
                out["kg_docs"] = 0
        except ApiError:
            out["kg_docs"] = 0
        return out

    # ---------- 写入 ----------
    async def put_chunks(self, docs: Sequence[dict], *, refresh: str = "wait_for") -> int:
        """按 `_id` 覆盖写入切片：`[{"_id": ..., **fields}]`（幂等重跑的唯一保证）。

        每个 dict 里的 `_id` 由调用方用 index_mapping.es_doc_id 拼好；
        非白名单字段（dynamic=strict）会让整批失败并抛 CustomError，附带第一条原因。
        """
        if not docs:
            return 0
        async_bulk = _helpers_bulk()

        actions = []
        for d in docs:
            doc = dict(d)
            es_id = str(doc.pop("_id", "") or doc.pop("es_id", "") or "")
            if not es_id:
                raise CustomError("写入切片缺少 _id（用 index_mapping.es_doc_id 生成）")
            actions.append({"_op_type": "index", "_index": self._index, "_id": es_id,
                            "_source": doc})
        try:
            await async_bulk(self._client(), actions, raise_on_error=True,
                             chunk_size=BULK_SLICE, refresh=refresh)
        except ApiError as e:
            raise CustomError(f"切片写入失败: {_reason(e)}") from e
        except Exception as e:  # noqa: BLE001  helpers 的 BulkIndexError 等按文本透出
            raise CustomError(f"切片写入失败: {e}") from e
        return len(actions)

    async def update_chunk(self, es_id: str, partial: dict, *, refresh: str = "wait_for") -> bool:
        """改一条切片的几个字段（如人工编辑正文后只更新 content）；不存在返回 False。"""
        try:
            await self._client().update(index=self._index, id=str(es_id),
                                        doc=partial, refresh=refresh)
            return True
        except NotFoundError:
            return False

    async def refresh_index(self) -> None:
        """手动刷一次索引，让刚写入但还没进搜索段的文档立刻可搜。

        配合 ``put_chunks(refresh="false")`` 用：大批量写入逐批不刷盘（每批都等一次刷盘
        等于把 refresh_interval 的活干了 N 遍），只在**写完的那一刻**刷这一次，
        「任务已完成」与「搜得到」就对齐了——否则中间有一个 refresh_interval（默认 1s）
        的窗口：文档状态已是解析完成，检索与配对却数到 0 条。
        """
        await self._client().indices.refresh(index=self._index)

    # ---------- 删除（底层物理清理，调用方已完成权限判定） ----------
    async def delete_by_ids(self, es_ids: Iterable[str], *, refresh: bool = True) -> int:
        ids = [str(i) for i in es_ids or [] if i]
        if not ids:
            return 0
        return await self._delete_by_query({"ids": {"values": ids}}, refresh=refresh)

    async def delete_by_kb(self, kb_id: int, *, batched: bool = True) -> int:
        return await self._delete_by_query(
            {"term": {RC.ES_FIELD_KB: int(kb_id)}}, batched=batched)

    async def delete_by_doc(self, doc_id: int, kb_id: Optional[int] = None) -> int:
        conds = [{"term": {RC.ES_FIELD_DOC: int(doc_id)}}]
        if kb_id is not None:
            conds.append({"term": {RC.ES_FIELD_KB: int(kb_id)}})
        return await self._delete_by_query({"bool": {"filter": conds}})

    async def set_available_by_doc(self, doc_id: int, available: bool,
                                  kb_id: Optional[int] = None) -> int:
        """文档停用/恢复时批量翻转切片的 available（不删数据，随时可放开）。"""
        conds = [{"term": {RC.ES_FIELD_DOC: int(doc_id)}}]
        if kb_id is not None:
            conds.append({"term": {RC.ES_FIELD_KB: int(kb_id)}})
        try:
            # 直接走客户端的 update_by_query：helpers 里的 async_update_by_query 在 9.x 已移除
            resp = await self._client().update_by_query(
                index=self._index,
                query={"bool": {"filter": conds}},
                script={"source": f"ctx._source.{RC.ES_FIELD_AVAILABLE} = params.avail",
                        "lang": "painless", "params": {"avail": bool(available)}},
                conflicts="proceed", refresh=True)
        except NotFoundError:
            return 0
        except ApiError as e:
            raise CustomError(f"切片可用性更新失败: {_reason(e)}") from e
        return int(resp.get("updated") or 0)

    async def _delete_by_query(self, query: dict, *, refresh: bool = True,
                               batched: bool = True, index: Optional[str] = None) -> int:
        """delete_by_query 统一入口（index 缺省为切片索引，图谱投影传 self._kg_index）。

        refresh 默认开：写入用 ``wait_for``、可用性翻转用 ``refresh=True``，删除却是慢一拍，
        会留下一个「文档已删、检索还能命中」的窗口（refresh_interval 到点前），
        命中的还是一条已删的切片。删除只在删库/删文档/重分块里发生，属冷路径，
        多一次 refresh 换语义一致，值得。
        """
        es = self._client()
        target = index or self._index
        kwargs: dict[str, Any] = {"conflicts": "proceed", "refresh": refresh}
        if batched:
            kwargs["scroll_size"] = BULK_SLICE
        try:
            resp = await es.delete_by_query(index=target, query=query, **kwargs)
        except NotFoundError:
            return 0
        except ApiError as e:
            raise CustomError(f"切片删除失败: {_reason(e)}") from e
        return int(resp.get("deleted") or 0)

    # ---------- 读取 ----------
    async def count(self, kb_ids: Sequence[int], *, chunk_types: Optional[Sequence[str]] = None,
                    include_invisible: bool = False, extra: Optional[Sequence[dict]] = None) -> int:
        if not kb_ids:
            return 0
        try:
            resp = await self._client().count(
                index=self._index,
                query={"bool": {"filter": base_filters(
                    kb_ids, chunk_types=chunk_types, include_invisible=include_invisible,
                    extra=extra)}})
        except NotFoundError:
            return 0
        return int(resp.get("count") or 0)

    async def get_chunks(self, es_ids: Iterable[str]) -> list[dict]:
        """按 _id 批量取切片（图谱构建要把命中的 chunk 原文捞回来）。"""
        ids = [str(i) for i in es_ids or [] if i]
        if not ids:
            return []
        resp = await self._client().mget(index=self._index, ids=ids)
        return [{"es_id": d.get("_id"), "score": None, "_source": d.get("_source") or {}}
                for d in resp.get("docs") or [] if d.get("found")]

    async def list_by_doc(self, doc_id: int, kb_id: Optional[int] = None, *,
                          size: int = 2000) -> list[dict]:
        """列出一个文档的全部切片（按 chunk_index 升序），供图谱抽取与 sidecar 重建。

        这里不卡 available：图谱要基于文档的全部切片抽实体，屏蔽掉的块也属于这篇文档的语义。
        """
        conds: list[dict] = [{"term": {RC.ES_FIELD_DOC: int(doc_id)}}]
        if kb_id is not None:
            conds.append(kb_filter([kb_id]))
        resp = await self._search({
            "index": self._index, "size": min(max(1, int(size)), 10000),
            "query": {"bool": {"filter": conds}},
            "sort": [{RC.ES_FIELD_INDEX: {"order": "asc"}}], "_source": True})
        return _hits_of(resp)

    # ---------- 检索 ----------
    async def keyword_search(self, kb_ids: Sequence[int], query_text: str, *, size: int = 20,
                             chunk_types: Optional[Sequence[str]] = None,
                             extra: Optional[Sequence[dict]] = None) -> list[dict]:
        """BM25 关键词检索：原样返回 ES 的 `_score`，饱和映射与关键词权重都在融合时算一次。

        这里不接受 boost 参数：往 `match.boost` 上乘系数不会改变本路内部的排序（所有命中
        同乘一个常数），只把 `_score` 变成一个解释不了的数；而乘两下（ES + 融合）实测
        会把 2 倍变成 4 倍，权重滑杆与 score_threshold 全失真。

        也不下发 highlight：SPEC 没承诺高亮，检索出口的 _row 也不取这个字段，留着只会让人
        以为前端能渲染它。真要加时注意片数上限的键名是 number_of_fragments——写成
        number_of_snippets 会被 ES 直接拒（实测 9.5.3），整条关键词路报 500。
        """
        if not kb_ids or not (query_text or "").strip():
            return []
        body = {"bool": {
            "must": [{"match": {RC.ES_FIELD_CONTENT: {
                "query": query_text,
                "operator": "or", "zero_terms_query": "none"}}}],
            "filter": base_filters(kb_ids, chunk_types=chunk_types, extra=extra)}}
        resp = await self._search({"index": self._index, "query": body,
                                   "size": max(1, int(size)), "_source": True})
        return _hits_of(resp)

    async def vector_search(self, kb_ids: Sequence[int], query_vector: Sequence[float], *,
                           size: int = 20, chunk_types: Optional[Sequence[str]] = None,
                           num_candidates: Optional[int] = None,
                           extra: Optional[Sequence[dict]] = None) -> list[dict]:
        """kNN 向量召回：kb_id 过滤在 knn.filter 里（先过滤再算距离，SPEC §10.2）。"""
        if not kb_ids or not (list(query_vector or [])):
            return []
        k = max(1, int(size))
        knn = {"field": RC.ES_FIELD_EMBED, "query_vector": [float(x) for x in query_vector],
               "k": k, "num_candidates": max(k, int(num_candidates or DEFAULT_RECALL_SIZE)),
               "filter": base_filters(kb_ids, chunk_types=chunk_types, extra=extra)}
        resp = await self._search({"index": self._index, "knn": knn, "size": k,
                                   "_source": True})
        return _hits_of(resp)

    async def _search(self, kwargs: dict) -> dict:
        index = str(kwargs.get("index") or self._index)
        try:
            return await self._client().search(**kwargs)
        except NotFoundError as e:
            raise CustomError(f"索引不存在（{index}），先执行 ensure_index") from e
        except ApiError as e:
            raise CustomError(f"检索失败: {_reason(e)}") from e

    async def search_hybrid(self, kb_ids: Sequence[int], *, query_text: Optional[str] = None,
                            query_vector: Optional[Sequence[float]] = None, size: int = 5,
                            recall_size: int = DEFAULT_RECALL_SIZE,
                            vector_weight: Optional[float] = None, keyword_boost: float = 1.0,
                            chunk_types: Optional[Sequence[str]] = None,
                            min_score: Optional[float] = None,
                            extra: Optional[Sequence[dict]] = None,
                            mode: Optional[str] = None) -> list[dict]:
        """BM25 + kNN 两路召回后在应用层加权融合（返回结构与 common_es.chunk_source 对齐）。

        为什么不用 ES 原生 knn+query 一次请求：那样两路分数直接相加，量纲不同（BM25 无上界、
        kNN 归一到 0~1），权重形同虚设，score_threshold 也没法解释。

        :param mode: VECTOR/KEYWORD/HYBRID（工作流节点与检索页透传的三档）；不传则按
            给了哪些输入自动决定。KEYWORD 模式即使传了向量也只走关键词路。
        """
        if not kb_ids:
            # 白名单为空 = 这个用户一个库都看不到：直接空结果，绝不退化成全库检索
            return []
        size = max(1, int(size))
        recall = max(size, int(recall_size or DEFAULT_RECALL_SIZE))
        w_vec = _weight(vector_weight)
        # 0 是个合法值（只要向量路），不能用 `or` 兜底：`0.0 or 1.0` 会把它改回 1
        try:
            boost = float(keyword_boost if keyword_boost is not None else 1.0)
        except (TypeError, ValueError):
            boost = 1.0
        boost = max(0.0, boost)
        want_vec = mode in (None, RC.RETRIEVE_MODE_VECTOR, RC.RETRIEVE_MODE_HYBRID)
        want_kw = mode in (None, RC.RETRIEVE_MODE_KEYWORD, RC.RETRIEVE_MODE_HYBRID)
        if mode == RC.RETRIEVE_MODE_VECTOR and not query_vector:
            return []
        if mode == RC.RETRIEVE_MODE_KEYWORD and not (query_text or "").strip():
            return []

        vec_hits: list[dict] = []
        kw_hits: list[dict] = []
        if want_vec and query_vector:
            vec_hits = await self.vector_search(kb_ids, query_vector, size=recall,
                                                chunk_types=chunk_types, extra=extra)
        if want_kw and (query_text or "").strip():
            kw_hits = await self.keyword_search(kb_ids, query_text, size=recall,
                                                chunk_types=chunk_types, extra=extra)
        if not vec_hits and not kw_hits:
            return []

        merged: dict[str, dict] = {}
        for h in vec_hits:
            item = merged.setdefault(h["es_id"], _blank_hit(h))
            item["vector_score"] = _clamp(h.get("score"))
            item.setdefault("_source", h["_source"])
        for h in kw_hits:
            item = merged.setdefault(h["es_id"], _blank_hit(h))
            item["keyword_score"] = _saturate((h.get("score") or 0.0) * boost)
            if not item.get("_source"):
                item["_source"] = h["_source"]

        has_vec = bool(vec_hits)
        has_kw = bool(kw_hits)
        if has_vec and has_kw:
            w_kw = 1.0 - w_vec
            denom = w_vec + w_kw
        elif has_vec:
            w_vec, w_kw, denom = 1.0, 0.0, 1.0
        else:
            w_vec, w_kw, denom = 0.0, 1.0, 1.0
        out: list[dict] = []
        for item in merged.values():
            item["score"] = round((w_vec * item["vector_score"] + w_kw * item["keyword_score"])
                                  / denom, 6)
            item["vector_score"] = round(item["vector_score"], 6)
            item["keyword_score"] = round(item["keyword_score"], 6)
            if min_score is not None and item["score"] < float(min_score):
                continue
            out.append(item)
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[:size]

    # ---------- 图谱向量投影（rag_kg_vector，需求 9 的实体/关系两路 kNN） ----------
    # 与切片侧同一套口径：只认调用方传下来的 kb_ids 白名单，空名单就是空结果。
    # 没有 available/deleted 位：实体/关系的生死由 Neo4j 说了算，投影跟它走（prune 链路）。

    async def kg_put(self, docs: Sequence[dict], *, refresh: str = "wait_for") -> int:
        """按 `_id` 覆盖写入实体/关系向量（`[{"_id": ..., **kg_fields}]`）。

        _id 由 kg_index_mapping.kg_vector_id 生成，所以同一实体重建 N 次也只有一条向量。
        写入失败报 CustomError：投影写不进去不能静默跳过，否则页面「已建图谱」而检索
        一路永远是空，那种不一致比直接失败难查得多。
        """
        if not docs:
            return 0
        async_bulk = _helpers_bulk()

        actions = []
        for d in docs:
            doc = dict(d)
            kg_id = str(doc.pop("_id", "") or doc.pop("kg_id", "") or "")
            if not kg_id:
                raise CustomError("写入图谱向量缺少 _id（用 kg_index_mapping.kg_vector_id 生成）")
            actions.append({"_op_type": "index", "_index": self._kg_index, "_id": kg_id,
                            "_source": doc})
        try:
            await async_bulk(self._client(), actions, raise_on_error=True,
                             chunk_size=BULK_SLICE, refresh=refresh)
        except ApiError as e:
            raise CustomError(f"图谱向量写入失败: {_reason(e)}") from e
        except Exception as e:  # noqa: BLE001  helpers 的 BulkIndexError 等按文本透出
            raise CustomError(f"图谱向量写入失败: {e}") from e
        return len(actions)

    async def kg_search_vector(self, kb_ids: Sequence[int], query_vector: Sequence[float], *,
                               kind: Optional[str] = None, size: int = 10,
                               min_score: Optional[float] = None) -> list[dict]:
        """实体或关系一路的 kNN：返回 [{es_id, score, _source}]（score 是余弦归一分）。

        :param kind: entity / relation；不给就是两路混查（同一索引里 kind 只是分类位）。
        :param min_score: 实体名匹配阀值，由上层给（图谱召回不跟着切片的 score_threshold 走）。
        """
        if not kb_ids or not (list(query_vector or [])):
            return []
        k = max(1, int(size))
        conds: list[dict] = [kb_filter(kb_ids)]
        if kind:
            conds.append({"term": {RC.KG_FIELD_KIND: str(kind)}})
        knn = {"field": RC.KG_FIELD_EMBED, "query_vector": [float(x) for x in query_vector],
               "k": k, "num_candidates": max(k, int(RC.KG_RETRIEVE_MAX_SCALE)), "filter": conds}
        resp = await self._search({"index": self._kg_index, "knn": knn, "size": k,
                                   "_source": True})
        hits = _hits_of(resp)
        if min_score is not None:
            hits = [h for h in hits if _clamp(h.get("score")) >= float(min_score)]
        return hits

    async def kg_scan_texts(self, kb_id: int, *, size: int = 10000) -> dict[str, str]:
        """列出本库已有的全部投影 `{_id: 向量文本}`（增量重建与 prune 算差集用）。

        只拉 text 不拉 embedding：一份干条实体的向量拉回来是几十 MB，而判定「文本变没变」
        只需要文本。冷路径（仅在图谱构建完成后跑一次）。
        """
        resp = await self._search({
            "index": self._kg_index, "size": min(max(1, int(size)), 10000),
            "query": {"term": {RC.KG_FIELD_KB: int(kb_id)}},
            "_source": [RC.KG_FIELD_TEXT]})
        return {str(h.get("es_id") or ""): str((h.get("_source") or {}).get(RC.KG_FIELD_TEXT) or "")
                for h in _hits_of(resp) if h.get("es_id")}

    async def kg_get(self, kg_ids: Iterable[str]) -> list[dict]:
        """按 _id 批量取回投影文档（关系命中后要拿两端的实体文本拼上下文）。"""
        ids = [str(i) for i in kg_ids or [] if i]
        if not ids:
            return []
        resp = await self._client().mget(index=self._kg_index, ids=ids)
        return [{"es_id": d.get("_id"), "score": None, "_source": d.get("_source") or {}}
                for d in resp.get("docs") or [] if d.get("found")]

    async def kg_count(self, kb_ids: Sequence[int]) -> int:
        if not kb_ids:
            return 0
        try:
            resp = await self._client().count(index=self._kg_index,
                                              query={"bool": {"filter": [kb_filter(kb_ids)]}})
        except (NotFoundError, ApiError):
            return 0
        return int(resp.get("count") or 0)

    async def kg_delete_by_kb(self, kb_id: int, *, batched: bool = True) -> int:
        return await self._delete_by_query(
            {"term": {RC.KG_FIELD_KB: int(kb_id)}}, batched=batched, index=self._kg_index)

    async def kg_delete_by_ids(self, kg_ids: Iterable[str]) -> int:
        ids = [str(i) for i in kg_ids or [] if i]
        if not ids:
            return 0
        return await self._delete_by_query({"ids": {"values": ids}}, index=self._kg_index)


# ==================== 分数与结构小工具 ====================
def _blank_hit(hit: dict) -> dict:
    return {"es_id": hit["es_id"], "score": 0.0, "vector_score": 0.0,
            "keyword_score": 0.0, "_source": hit.get("_source") or {}}


def _clamp(value: Any) -> float:
    """kNN 分归一到 0~1（ES 对 cosine 已给 (1+c)/2，越界值按边界截断）。"""
    v = float(value or 0.0)
    return 0.0 if v < 0 else (1.0 if v > 1 else v)


def _saturate(raw: float) -> float:
    """BM25 → 0~1 的饱和映射（见模块头「分数归一」）。"""
    v = float(raw or 0.0)
    if v <= 0:
        return 0.0
    return v / (v + KW_SATURATION)


def _weight(value: Optional[float]) -> float:
    """向量路权重：越界/缺失都回落到配置默认值（0.7），保证融合仍在 0~1。"""
    default = float(RC.RETRIEVE_DEFAULTS["vector_similarity_weight"])
    try:
        w = float(value if value is not None else default)
    except (TypeError, ValueError):
        return default
    return 0.0 if w < 0 else (1.0 if w > 1 else w)


def _running_loop_id() -> Optional[int]:
    try:
        return id(asyncio.get_running_loop())
    except RuntimeError:
        return None


def _mapping_props(mapping: Any) -> dict:
    """get_mapping 响应形如 {索引名: {"mappings": {"properties": ...}}}，摊平一层。"""
    if not isinstance(mapping, dict):
        return {}
    for body in mapping.values():
        props = (((body or {}).get("mappings") or {}).get("properties"))
        if props:
            return dict(props)
    return {}


def _reason(e: Exception) -> str:
    """ApiError → 一行可读原因（异常类名 + message），避免把整个 body 喷进日志。"""
    info = getattr(e, "info", None) or {}
    body = info.get("body") if isinstance(info, dict) else None
    reason = None
    if isinstance(body, dict):
        reason = (body.get("error") or {}).get("reason") if isinstance(
            body.get("error"), dict) else body.get("error")
    return str(reason or e)[:400]


def _helpers_bulk():
    """取 helpers.async_bulk（依赖缺失时给一句可执行的提示，而不是裸 ImportError）。"""
    if not HAS_ES:
        raise CustomError(ES_INSTALL_HINT)
    from elasticsearch.helpers import async_bulk

    return async_bulk


# 进程内唯一实例（模块级单例：FastAPI 与 arq worker 各自 import 到自己那份）
es_client = AsyncEsClient()

__all__ = ["AsyncEsClient", "CustomError", "es_client", "to_json", "from_json",
           "kb_filter", "base_filters", "KW_SATURATION", "DEFAULT_RECALL_SIZE"]
