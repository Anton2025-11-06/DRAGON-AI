# -*- coding: utf-8 -*-
"""common_model —— 12 种模型能力 × 多供应商 的公共调用层。

对外统一入口：
- ModelRegistry：动态注册表（providers_for / categories_for / matrix / instantiate）
- invoke / stream：按 (category, provider) 取实现并调用的便捷函数
- ModelResult / StreamChunk / ModelConfig：数据结构

导入本包会自动装载全部能力类型子包（触发各供应商子类 @register），
从而支持「不硬编码」的类型↔供应商动态发现。
"""
from __future__ import annotations

import importlib
import pkgutil

from common.common_model.base import (BaseModel, ChatMLMixin, ModelRegistry,
                                      ModelResult, StreamableMixin, register)
from common.common_model.model_types import (ChatMessage, InvokeResult,
                                            ModelConfig, ModelInvokeError,
                                            ModelNotFoundError, StreamChunk)

# ---- 自动装载所有能力类型子包（一级目录=类型），触发注册 ----
for _m in pkgutil.iter_modules(__path__):
    if _m.name in ("base", "model_types"):
        continue
    if _m.ispkg:
        importlib.import_module(f"{__name__}.{_m.name}")


def instantiate(category: str, config: ModelConfig) -> BaseModel:
    """按 (category, config.provider) 实例化实现类。"""
    return ModelRegistry.instantiate(category, config)


async def invoke(category: str, config: ModelConfig, **kwargs) -> ModelResult:
    """异步调用某能力类型（按 config.provider 选实现）。返回 ModelResult。"""
    return await ModelRegistry.instantiate(category, config).ainvoke(**kwargs)


def providers_for(category: str) -> list[str]:
    """动态：某类型支持的供应商列表。"""
    return ModelRegistry.providers_for(category)


def categories_for(provider: str) -> list[str]:
    """动态：某供应商支持的类型列表。"""
    return ModelRegistry.categories_for(provider)


def matrix() -> dict[str, list[str]]:
    """动态：完整支持矩阵 {category: [providers]}。"""
    return ModelRegistry.matrix()


__all__ = [
    "BaseModel", "ChatMLMixin", "StreamableMixin", "ModelRegistry", "register",
    "ModelResult", "StreamChunk", "ChatMessage", "InvokeResult", "ModelConfig",
    "ModelInvokeError", "ModelNotFoundError",
    "instantiate", "invoke", "providers_for", "categories_for", "matrix",
]
