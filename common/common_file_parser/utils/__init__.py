# -*- coding: utf-8 -*-
"""解析工具层（文本处理 / 标题层级 / token 计数）统一出口。"""
from __future__ import annotations

from common.common_file_parser.utils.heading_utils import (BULLET_PATTERNS,
                                                            infer_heading,
                                                            mark_title_blocks,
                                                            normalize_title,
                                                            title_paths)
from common.common_file_parser.utils.text_utils import (decode_bytes,
                                                        drop_headers_footers,
                                                        drop_toc, is_mostly_cjk,
                                                        is_page_noise,
                                                        normalize_space,
                                                        split_sentences,
                                                        strip_markdown_noise)
from common.common_file_parser.utils.token_utils import (count_tokens,
                                                         estimate_tokens,
                                                         truncate_by_tokens)

__all__ = [
    "decode_bytes", "normalize_space", "is_mostly_cjk", "split_sentences", "is_page_noise",
    "drop_headers_footers", "drop_toc", "strip_markdown_noise",
    "BULLET_PATTERNS", "infer_heading", "mark_title_blocks", "normalize_title", "title_paths",
    "count_tokens", "estimate_tokens", "truncate_by_tokens",
]
