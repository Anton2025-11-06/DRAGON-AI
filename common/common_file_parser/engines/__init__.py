# -*- coding: utf-8 -*-
"""解析引擎层出口：`get_engine(名字)` 拿到可用引擎实例，`engine_available(名字)` 只做体检。

配置项与引擎名一一对应（rag_constant.PARSE_ENGINES_ALL）：native / docling / mineru / auto。
业务侧（service_rag 与 arq worker）只认这个名字集合，不认识具体类，加第 4 个引擎不用改调用方。
"""
from __future__ import annotations

from typing import Optional

from common.common_file_parser import constants as C
from common.common_file_parser.engines.auto import AutoEngine, probe_document
from common.common_file_parser.engines.base import (BaseEngine, EngineNotAvailable,
                                                    ParseError, build_engine,
                                                    engine_cls, normalize_ext)
from common.common_file_parser.engines.docling_engine import DoclingEngine
from common.common_file_parser.engines.mineru import MineruEngine
from common.common_file_parser.engines.native import NativeEngine

__all__ = [
    "BaseEngine", "NativeEngine", "DoclingEngine", "MineruEngine", "AutoEngine",
    "get_engine", "engine_available", "engine_formats", "probe_document",
    "ParseError", "EngineNotAvailable", "build_engine", "engine_cls", "normalize_ext",
]


def get_engine(name: Optional[str] = None, *, options: Optional[dict] = None) -> BaseEngine:
    """按配置取引擎实例，并在返回前完成依赖自检。

    引擎不可用（docling 没装、minerU 没配地址）在这里就抛 EngineNotAvailable，而不是等到
    解析到一半再失败：解析任务能把这句原话写进 error_msg，页面直接告诉用户该改哪一处配置。
    """
    engine = build_engine(name or C.ENGINE_NATIVE, options)
    engine.ensure_available()
    return engine


def engine_available(name: Optional[str] = None, *, options: Optional[dict] = None) -> bool:
    """引擎体检（配置面板据此把不可选的引擎置灰，而不是让用户点了才报错）。"""
    try:
        get_engine(name, options=options)
        return True
    except (EngineNotAvailable, ParseError):
        return False


def engine_formats(name: Optional[str] = None) -> list[str]:
    """该引擎声明支持的扩展名（前端「当前引擎不支持该格式」提示的数据源）。"""
    try:
        return sorted(engine_cls(name or C.ENGINE_NATIVE).formats)
    except ParseError:
        return []
