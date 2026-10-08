# -*- coding: utf-8 -*-
"""图谱抽取断点的**格式层**：批次划分签名、批号字段名、单批结果的编解码。

只依赖标准库与 rag_constant（不碰 Redis、不碰大模型、不碰 Neo4j），所以「断点什么时候
算作废」这类判定只有一处答案，也能被静态用例直接跑（本项目的 service_rag 包在 import
期就会装配 FastAPI，格式层单独成模块才测得动）。

断点存什么（一份 Redis hash，键拼法见 ``rag_constant.kg_ckpt_key``）
--------------------------------------------------------------------
``meta``   本轮批次划分的签名 = 抽取模型 + 每批片数 + 全部 chunk_index 序列。
           三者任一变化都会让批次边界挪位——旧断点里的「第 3 批」已经不再是同一批切片，
           所以整份断点直接作废、从第一批重抽（增删切片是这个结果，换抽取模型也是）。
``b1..bN`` 该批**归一化后**的抽取结果（``graph_service._normalize`` 的返回三元组）的 JSON。
           存成品而不是模型原文：原文还得再过一次归一化，而实体挂的切片编号依赖当次的
           ``es_ids`` 溯源表，只有归一化后的结果是二义性为零的那一份。

签名管不到的那一类变化怎么防
----------------------------
切片正文被人在切片管理里改过时，chunk_index 序列没变、签名也没变，但那一批的 prompt
已经不是同一句话了。所以每个批的值里额外带一份 prompt 指纹，对不上就当没命中，
只重抽这一批，其余批照旧复用。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Optional, Sequence

from common.common_constants import rag_constant as RC

# 单批结果的字节上限：超过就不存这一批（断点是省钱手段，不该把 Redis 当存储用）。
# 一批 20 个切片的正常抽取量级在几十 KB，撞上上限说明模型吐了重复内容，重抽也不亏。
MAX_BATCH_BYTES = 2 * 1024 * 1024


def sig(*, model: str, batch_chunks: int, indexes: Sequence[int]) -> str:
    """批次划分签名：抽取模型 + 每批片数 + 切片编号序列，三者共同决定「第 N 批是哪些片」。

    只取编号不取正文：正文由每个批自己的 prompt 指纹管（见 ``prompt_stamp``），
    改一个字就让整篇重抽属于过度作废。
    """
    body = f"{str(model or '').strip()}|{int(batch_chunks)}|{','.join(str(i) for i in indexes)}"
    return hashlib.sha1(body.encode("utf-8")).hexdigest()[:32]


def batch_field(batch_no: int) -> str:
    """第 N 批在 hash 里的字段名（批号从 1 起，与进度里的「第 x/y 批」同一个口径）。"""
    return f"{RC.KG_CKPT_BATCH_PREFIX}{int(batch_no)}"


def prompt_stamp(prompt: str) -> str:
    """这一批 prompt 的指纹：切片内容一改就换号，旧结果不能再算命中。"""
    return hashlib.sha1(str(prompt or "").encode("utf-8")).hexdigest()[:16]


def pack(entities: Sequence[dict], relations: Sequence[dict],
         notes: Sequence[str], *, stamp: str) -> str:
    """一批的归一化结果 → 存进 Redis 的字符串；超上限时回空串（调用方据此跳过这一批）。"""
    text = json.dumps(
        {"stamp": stamp, "entities": list(entities),
         "relations": list(relations), "notes": list(notes)},
        ensure_ascii=False, default=str)
    if len(text.encode("utf-8")) > MAX_BATCH_BYTES:
        return ""
    return text


def unpack(raw: Any, *, stamp: str) -> Optional[tuple[list[dict], list[dict], list[str]]]:
    """Redis 里的字符串 → 归一化结果三元组；结构不对或指纹不匹配一律回 ``None``（当没命中）。

    绝不抛错：一份被手改过/半截写坏的断点最坏就该退化成「这一批重抽一次」，
    而不是把整篇文档的图谱构建打成失败。
    """
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "ignore")
    if not raw or not isinstance(raw, str):
        return None
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or str(data.get("stamp") or "") != stamp:
        return None
    ents, rels, notes = data.get("entities"), data.get("relations"), data.get("notes")
    if not isinstance(ents, list) or not isinstance(rels, list):
        return None
    if not all(_entity_ok(e) for e in ents):
        return None
    if not all(_relation_ok(r) for r in rels):
        return None
    return (ents, rels, [str(n) for n in notes if isinstance(n, (str, int, float))])


def _entity_ok(item: Any) -> bool:
    """实体行的必需键（与 ``_normalize`` 的产物一致，缺一个就不能直接进 ``_merge_batches``）。"""
    return (isinstance(item, dict) and bool(str(item.get("name") or ""))
            and bool(str(item.get("type") or "")) and isinstance(item.get("chunk_ids"), list))


def _relation_ok(item: Any) -> bool:
    return (isinstance(item, dict)
            and all(str(item.get(k) or "") for k in
                    ("head", "tail", "relation", "head_type", "tail_type")))


__all__ = ["MAX_BATCH_BYTES", "sig", "batch_field", "prompt_stamp", "pack", "unpack"]
