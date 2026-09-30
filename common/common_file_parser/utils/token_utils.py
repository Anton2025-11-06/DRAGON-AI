# -*- coding: utf-8 -*-
"""token 计数（分块长度按 token 收口，而不是按字符）。

为什么要 token：向量模型有上下文窗口限制（中文常见 512/1024 token），按字符切看似均匀，
实际中文 1 字≈1 token、英文 1 token≈4 字符，同一份配置在中英文混排文档上会切出长度差数倍的块。

tiktoken 属可选依赖：未安装（或离线取不到 BPE 文件）时退化为「CJK 按字、其余按 4 字符」的
估算，估算只影响块长精度，不影响功能可用。
"""
from __future__ import annotations

import re
from typing import Optional

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

_encoding = None
_encoding_failed = False


def _get_encoding():
    """惰性加载 tiktoken 编码器（加载失败只尝试一次，之后长期走估算）。"""
    global _encoding, _encoding_failed
    if _encoding is not None or _encoding_failed:
        return _encoding
    try:
        import tiktoken
        _encoding = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001
        _encoding_failed = True
    return _encoding


def estimate_tokens(text: str) -> int:
    """无 tiktoken 时的估算：中文按字计，英文/数字按 4 字符≈1 token。"""
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + max(1, other // 4)


def count_tokens(text: str) -> int:
    """token 计数（优先真实 BPE，失败退估算）。"""
    if not text:
        return 0
    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001
            pass
    return estimate_tokens(text)


def truncate_by_tokens(text: str, max_tokens: int, *, suffix: str = "…") -> str:
    """按 token 上限截断，并在句子/空白边界收口（避免截出半句话）。"""
    text = text or ""
    if max_tokens <= 0 or count_tokens(text) <= max_tokens:
        return text
    enc: Optional[object] = _get_encoding()
    if enc is not None:
        try:
            tokens = enc.encode(text, disallowed_special=())  # type: ignore[attr-defined]
            cut = enc.decode(tokens[:max_tokens])             # type: ignore[attr-defined]
            return _snap_boundary(cut, max_tokens) + suffix
        except Exception:  # noqa: BLE001
            pass
    # 估算路径：按「token≈字符」的比例先粗切再收口
    ratio = max(1, len(text) // max(1, count_tokens(text)))
    cut = text[: max(1, max_tokens * ratio)]
    return _snap_boundary(cut, max_tokens) + suffix


def _snap_boundary(cut: str, max_tokens: int) -> str:
    """把截断点回退到最近的句子/段落边界，最多回退 20% 长度。"""
    for pattern in ("\n\n", "。", "！", "？", "；", ". ", "! ", "? ", "\n", " "):
        pos = cut.rfind(pattern)
        if pos > len(cut) * 0.6:
            return cut[: pos + len(pattern)].strip()
    return cut.strip()
