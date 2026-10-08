# -*- coding: utf-8 -*-
"""分块策略层出口：策略注册表 + 统一入口 `chunk_document`（SPEC §7.5 七种策略）。

调用范式
--------
    from common.common_file_parser.chunkers import ChunkConfig, chunk_document

    cfg = ChunkConfig.from_dict(kb.chunk_config, ext=doc.ext)
    chunks = await chunk_document(doc, cfg, embed_fn=embed_batch)

七种策略的分工只有一句话：**决定在哪儿断开**。长度上限、碎块合并、面包屑前缀、
图片/表格的前后文补齐，全部在 pieces.assemble 里做一次——所以页面换策略时，
"切片长度都不一样"这种失控情况不会出现（唯一的差别就是断开点）。
"""
from __future__ import annotations

from typing import Optional, Sequence, Union

from common.common_constants import rag_constant as RC
from common.common_file_parser.chunkers.config import ChunkConfig, suggest_strategy
from common.common_file_parser.chunkers.pieces import (Piece, Unit, assemble,
                                                       build_units)
from common.common_file_parser.chunkers.structure_strategies import (chunk_excel,
                                                                     chunk_page,
                                                                     chunk_semantic,
                                                                     chunk_title)
from common.common_file_parser.chunkers.text_strategies import (chunk_delimiter,
                                                                chunk_fixed,
                                                                chunk_regex)
from common.common_file_parser.models import Chunk, ParsedBlock, ParsedDocument

# 策略名 → 实现（键集与 rag_constant.CHUNK_STRATEGIES_ALL 必须一致，前端下拉按那份渲染）
#   原 CHUNK_RECURSIVE（LangChain 递归分块）已并给 delimiter：两者断开点同源，只差 overlap，
#   而 overlap 已并给 delimiter。旧值由 ChunkConfig 映射过来，不再占一个注册表位。
STRATEGIES = {
    RC.CHUNK_FIXED: chunk_fixed,
    RC.CHUNK_DELIMITER: chunk_delimiter,
    RC.CHUNK_TITLE: chunk_title,
    RC.CHUNK_PAGE: chunk_page,
    RC.CHUNK_SEMANTIC: chunk_semantic,
    RC.CHUNK_EXCEL: chunk_excel,
    RC.CHUNK_REGEX: chunk_regex,
}
STRATEGIES_ALL = list(RC.CHUNK_STRATEGIES_ALL)

__all__ = ["STRATEGIES", "STRATEGIES_ALL", "ChunkConfig", "chunk_document",
           "suggest_strategy", "Piece", "Unit", "assemble", "build_units"]


def _blocks_of(source: Union[ParsedDocument, Sequence[ParsedBlock]]) -> tuple[list[ParsedBlock], str]:
    """入口兼容两种输入：整篇 ParsedDocument（带 ext）或裸的解析块列表（单测/重分块用）。"""
    if isinstance(source, ParsedDocument):
        return list(source.blocks or []), (source.ext or "")
    return list(source or []), ""


async def chunk_document(source: Union[ParsedDocument, Sequence[ParsedBlock]],
                         config: Union[ChunkConfig, dict, None] = None, *,
                         ext: str = "",
                         embed_fn: Optional[object] = None,
                         warnings: Optional[list[str]] = None) -> list[Chunk]:
    """按配置的分块策略把解析产物切成 Chunk 列表（chunk.index 从 0 连续编号）。

    :param source: ParsedDocument 或 ParsedBlock 列表
    :param config: ChunkConfig 或前端的 chunk_config 字典（缺键自动归一）
    :param ext: 文件格式（裸块列表输入时用于独占策略校验；doc 输入时以 doc.ext 为准）
    :param embed_fn: 语义分块需要的向量入口，``async (texts) -> vectors``
    :param warnings: 传入一个列表即可收集归一/降级说明（写进文档行的解析告警）
    """
    blocks, doc_ext = _blocks_of(source)
    cfg = config if isinstance(config, ChunkConfig) else ChunkConfig.from_dict(
        config, ext=ext or doc_ext)
    if not cfg.ext:
        cfg.ext = (ext or doc_ext or "").lower().lstrip(".")
        cfg._check_strategy()                 # 裸块列表输入时，扩展名是后补的，这里补做一次校验
    units = build_units(blocks)
    if not units:
        if warnings is not None:
            warnings.append("解析结果里没有可分块的正文")
        return []
    strategy = STRATEGIES.get(cfg.strategy, chunk_fixed)
    pieces = await strategy(units, cfg, embed_fn=embed_fn)     # type: ignore[arg-type]
    chunks = assemble(units, pieces, cfg, warnings=warnings)
    if not chunks and warnings is not None:
        warnings.append("分块后没有产出任何切片（请检查单块长度与策略是否匹配该文件）")
    return chunks
