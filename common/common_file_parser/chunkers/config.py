# -*- coding: utf-8 -*-
"""ChunkConfig：把前端传来的 chunk_config（可能缺键、可能取值越界）归一成一份可靠的配置。

归一原则（SPEC §13.2「所有可配置项全部前端可视化配置，不写死参数」的另一面）
--------------------------------------------------------------------------
页面能填 = 用户能填错。缺键用 rag_constant.CHUNK_CONFIG_DEFAULTS 补齐；越界一律夹到区间而不是
报错——配置写错就让解析失败，用户看到的是「文档解析失败」，还得猜是哪一项错了。
真正需要"报错"的只有一件事：**策略与文件格式不匹配**（比如给 txt 选「每页分块」），这必须
回落并留下 warnings，因为不回落会静默产出一堆没有页码的切片。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from common.common_constants import rag_constant as RC
from common.common_file_parser import constants as C


def _clamp(value: Any, low: int, high: int, default: int) -> int:
    """取整数并夹到 [low, high]；非数字/越界都回落默认（页面填错不该阻断解析）。"""
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    if num < low or num > high:
        return default
    return num


@dataclass
class ChunkConfig:
    """分块配置（与 tb_knowledge_base.chunk_config 的键一一对应）。"""

    strategy: str = RC.CHUNK_FIXED
    chunk_size: int = RC.DEFAULT_CHUNK_SIZE          # 目标长度（字符）
    chunk_overlap: int = RC.DEFAULT_CHUNK_OVERLAP    # 重叠长度（字符）
    max_chars: int = RC.MAX_CHUNK_CHARS              # 单块硬上限
    separators: list[str] = field(default_factory=lambda: list(RC.DEFAULT_SEPARATORS))
    regex_pattern: str = ""
    title_level: int = 3
    keep_table_header: bool = True
    title_path: bool = True
    semantic_threshold: float = 0.5
    context_augment: dict = field(default_factory=lambda: {
        "enabled": False, "before": 120, "after": 120})
    ext: str = ""                                    # 归一时的文件格式（决定独占策略可用性）
    warnings: list[str] = field(default_factory=list)

    # ---------- 构造 ----------

    @classmethod
    def from_dict(cls, payload: Optional[dict], *, ext: str = "") -> "ChunkConfig":
        """配置字典 → 归一后的 ChunkConfig（缺键补默认、越界夹区间、非法策略回落 fixed）。"""
        data: dict[str, Any] = dict(RC.CHUNK_CONFIG_DEFAULTS)
        data.update({k: v for k, v in (payload or {}).items() if v is not None})
        cfg = cls()
        cfg.ext = (ext or "").lower().lstrip(".")
        cfg.strategy = str(data.get("strategy") or RC.CHUNK_FIXED).strip().lower()
        cfg.chunk_size = _clamp(data.get("chunk_size"), 50, RC.MAX_CHUNK_CHARS,
                                RC.DEFAULT_CHUNK_SIZE)
        cfg.chunk_overlap = _clamp(data.get("chunk_overlap"), 0, RC.MAX_CHUNK_CHARS,
                                   RC.DEFAULT_CHUNK_OVERLAP)
        cfg.max_chars = _clamp(data.get("max_chars"), 200, RC.MAX_CHUNK_CHARS,
                               RC.MAX_CHUNK_CHARS)
        cfg.title_level = _clamp(data.get("title_level"), 1, 6, 3)
        cfg.regex_pattern = str(data.get("regex_pattern") or "")[:500]
        cfg.keep_table_header = bool(data.get("keep_table_header", True))
        cfg.title_path = bool(data.get("title_path", True))
        cfg.separators = cls._separators(data.get("separators"))
        cfg.semantic_threshold = cls._threshold(data.get("semantic_threshold"))
        cfg.context_augment = cls._augment(data.get("context_augment"))
        cfg._check_strategy()
        return cfg

    @staticmethod
    def _separators(value: Any) -> list[str]:
        """分隔符列表：接受 list 或按行/逗号写的字符串，去掉空串（空分隔符会把文本切成单字）。"""
        if isinstance(value, str):
            items = [s for s in value.replace(",", "\n").split("\n")]
        elif isinstance(value, (list, tuple)):
            # 先丢 None 再转字符串：str(None) 得出字面量 "None"，它会变成一个真分隔符，
            # 把正文里恰好出现的 None 字样（模型名、空值导出）从中间切开
            items = [str(s) for s in value if s is not None]
        else:
            items = list(RC.DEFAULT_SEPARATORS)
        out = [s for s in items if s not in ("", None)]
        return out or list(RC.DEFAULT_SEPARATORS)

    @staticmethod
    def _threshold(value: Any) -> float:
        """相似度阈值：只接受 0~1，其它值回落默认（语义分块的断开判据，越界等于不分块）。"""
        try:
            num = float(value)
        except (TypeError, ValueError):
            return float(RC.CHUNK_CONFIG_DEFAULTS["semantic_threshold"])
        return num if 0.0 <= num <= 1.0 else float(RC.CHUNK_CONFIG_DEFAULTS["semantic_threshold"])

    @staticmethod
    def _augment(value: Any) -> dict:
        """上下文补齐配置：开关 + 前后各取多少字（上限 AUGMENT_MAX_CHARS，补太多会冲淡语义）。"""
        raw = dict(value) if isinstance(value, dict) else {}
        default = dict(RC.CHUNK_CONFIG_DEFAULTS["context_augment"])
        return {"enabled": bool(raw.get("enabled", default["enabled"])),
                "before": _clamp(raw.get("before"), 0, C.AUGMENT_MAX_CHARS,
                                 default["before"]),
                "after": _clamp(raw.get("after"), 0, C.AUGMENT_MAX_CHARS, default["after"])}

    def _check_strategy(self) -> None:
        """策略可用性校验：已下线策略映射到新名，不认识的名字回落 fixed，
        格式不匹配的独占策略降级并留警告。"""
        legacy = RC.CHUNK_STRATEGY_LEGACY.get(self.strategy)
        if legacy:
            # 存量库的 strategy 还是旧值（不改库）：映射过去并告诉用户，而不是把它当未知值
            # 降成固定长度——那等于用户什么都没动，切片却从按句子边界变成了按字数硬切。
            to, old_label = legacy
            self.warnings.append(
                f"「{old_label}」已与「{RC.CHUNK_STRATEGY_LABELS[to]}」合并，"
                f"本次按后者切分（两者断开点同源，只差的重叠长度已并入新策略）")
            self.strategy = to
        if self.strategy not in RC.CHUNK_STRATEGIES_ALL:
            self.warnings.append(f"未知分块策略 {self.strategy}，已按固定长度分块")
            self.strategy = RC.CHUNK_FIXED
            return
        exclusive = RC.CHUNK_STRATEGY_EXCLUSIVE_EXT.get(self.strategy)
        if exclusive and self.ext and self.ext not in exclusive:
            fallback = RC.CHUNK_TITLE if self.ext in ("md", "markdown", "docx", "html",
                                                      "htm", "txt") else RC.CHUNK_FIXED
            self.warnings.append(
                f"「{RC.CHUNK_STRATEGY_LABELS[self.strategy]}」只对 "
                f"{'/'.join(sorted(exclusive))} 有意义，当前是 .{self.ext}，"
                f"已改用「{RC.CHUNK_STRATEGY_LABELS[fallback]}」")
            self.strategy = fallback

    # ---------- 取值（分块代码只看这几个属性，不再各自夹区间） ----------

    @property
    def hard_limit(self) -> int:
        """单块硬上限：SPEC §7.5 全策略统一 20000，页面填更大也不生效。"""
        return min(int(self.max_chars or RC.MAX_CHUNK_CHARS), RC.MAX_CHUNK_CHARS)

    @property
    def size(self) -> int:
        """目标长度：夹在 [MIN_CHUNK_CHARS, hard_limit]，重叠再占掉一半余量。"""
        return max(C.MIN_CHUNK_CHARS, min(int(self.chunk_size or C.MIN_CHUNK_CHARS),
                                          self.hard_limit))

    @property
    def overlap(self) -> int:
        """重叠长度：不允许达到目标长度（100% 重叠会把文本无限复制进下一块）。"""
        return max(0, min(int(self.chunk_overlap or 0), self.size // 2))

    @property
    def min_chars(self) -> int:
        return C.MIN_CHUNK_CHARS

    @property
    def augment_enabled(self) -> bool:
        return bool((self.context_augment or {}).get("enabled"))

    def augment_window(self) -> tuple[int, int]:
        """(前补字数, 后补字数)。"""
        aug = self.context_augment or {}
        return (int(aug.get("before") or 0), int(aug.get("after") or 0))

    def to_dict(self) -> dict[str, Any]:
        """回写知识库配置时的形态（与 CHUNK_CONFIG_DEFAULTS 同键，页面直接回填表单）。"""
        return {"strategy": self.strategy, "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap, "max_chars": self.max_chars,
                "separators": list(self.separators), "regex_pattern": self.regex_pattern,
                "title_level": self.title_level,
                "keep_table_header": self.keep_table_header,
                "title_path": self.title_path,
                "semantic_threshold": self.semantic_threshold,
                "context_augment": dict(self.context_augment)}


def suggest_strategy(ext: str, *, has_title: bool = False) -> str:
    """按格式给前端一个默认策略建议（新建知识库时下拉的初值，SPEC §7.5）。

    有分页概念的（pdf/ppt）先按页；表格类先按 excel；其余按标题层级——但纯文本抽不出标题时
    标题分块等价于固定长度，所以 has_title=False 时直接建议 delimiter（句子边界兜底最稳）。
    """
    e = (ext or "").lower().lstrip(".")
    if e in RC.CHUNK_STRATEGY_EXCLUSIVE_EXT[RC.CHUNK_PAGE]:
        return RC.CHUNK_PAGE
    if e in RC.CHUNK_STRATEGY_EXCLUSIVE_EXT[RC.CHUNK_EXCEL]:
        return RC.CHUNK_EXCEL
    if has_title or e in ("md", "markdown", "html", "htm", "docx"):
        return RC.CHUNK_TITLE
    return RC.CHUNK_DELIMITER
