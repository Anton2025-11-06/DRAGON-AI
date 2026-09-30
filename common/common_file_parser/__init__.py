# -*- coding: utf-8 -*-
"""common_file_parser：文档解析 + 分块（RAG 知识库流水线的第 0 环）。

对外只暴露 entry 层的几个函数：

    doc = await parse_document(raw, name, parse_config=kb.parse_config, image_hook=hook)
    chunks = chunk_parsed_document(doc, kb.chunk_config, embed_fn=fn)

引擎（native/docling/minerU）与分块策略（8 种）都在内部按配置路由，产物结构统一为
ParsedBlock / Chunk，业务侧不需要感知具体格式。
"""
from __future__ import annotations

from common.common_file_parser.chunkers import (STRATEGIES_ALL, ChunkConfig,
                                                chunk_document,
                                                suggest_strategy)
from common.common_file_parser.constants import (BLOCK_FIGURE, BLOCK_IMAGE,
                                                 BLOCK_TABLE, BLOCK_TEXT,
                                                 BLOCK_TITLE, ENGINE_DOCLING,
                                                 ENGINE_MINERU, ENGINE_NATIVE,
                                                 MAX_CHUNK_CHARS)
from common.common_file_parser.engines import (BaseEngine, EngineNotAvailable,
                                               ParseError, engine_available,
                                               get_engine)
from common.common_file_parser.entry import (apply_image_hook, chunk_from_sidecar,
                                             chunk_parsed_document, config_summary,
                                             load_sidecar, parse_document,
                                             preprocess, render_sidecar)
from common.common_file_parser.models import (Chunk, ImageRef, ParsedBlock,
                                              ParsedDocument)

__all__ = [
    # 入口
    "parse_document", "chunk_parsed_document", "chunk_document", "chunk_from_sidecar",
    "preprocess", "apply_image_hook", "render_sidecar", "load_sidecar", "config_summary",
    "suggest_strategy",
    # 数据结构
    "ParsedBlock", "ParsedDocument", "Chunk", "ImageRef", "ChunkConfig",
    # 引擎
    "BaseEngine", "get_engine", "engine_available", "EngineNotAvailable", "ParseError",
    # 常量
    "ENGINE_NATIVE", "ENGINE_DOCLING", "ENGINE_MINERU",
    "BLOCK_TEXT", "BLOCK_TITLE", "BLOCK_TABLE", "BLOCK_IMAGE", "BLOCK_FIGURE",
    "STRATEGIES_ALL", "MAX_CHUNK_CHARS",
]
