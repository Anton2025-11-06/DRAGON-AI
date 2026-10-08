# -*- coding: utf-8 -*-
"""解析与分块的常量：引擎名、块类型、长度上下限、扩展名分组。

取值一律转引 common_constants.rag_constant（SPEC 口径的唯一来源），
本模块只做「解析层内部更好记的名字」和解析层独有的一些阈值。
"""
from __future__ import annotations

from common.common_constants import rag_constant as RC

# ==================== 引擎名（与 kb.parser_engine 同值） ====================
ENGINE_NATIVE = RC.PARSE_ENGINE_NATIVE
ENGINE_MINERU = RC.PARSE_ENGINE_MINERU
ENGINE_AUTO = RC.PARSE_ENGINE_AUTO
ENGINES_ALL = list(RC.PARSE_ENGINES_ALL)

# ==================== 块类型（解析产物的结构类别，≠ 向量库的 chunk_type） ====================
BLOCK_TEXT = "text"        # 正文段落
BLOCK_TITLE = "title"      # 标题（带 level）
BLOCK_TABLE = "table"      # 表格（已渲染成文本）
BLOCK_IMAGE = "image"      # 独立图片块（一律提取并传存储，只有描述文字才受 image_understand 控制）
BLOCK_FIGURE = "figure"    # 图注/示意图说明文字
BLOCK_CODE = "code"        # 代码/预格式化块（markdown、html 里的 pre/code）
BLOCK_TYPES_ALL = [BLOCK_TEXT, BLOCK_TITLE, BLOCK_TABLE, BLOCK_IMAGE, BLOCK_FIGURE, BLOCK_CODE]

# ==================== 长度阈值（SPEC §7.5：全策略统一单块上限） ====================
MAX_CHUNK_CHARS = RC.MAX_CHUNK_CHARS
MIN_CHUNK_CHARS = RC.MIN_CHUNK_CHARS
DEFAULT_CHUNK_SIZE = RC.DEFAULT_CHUNK_SIZE
DEFAULT_CHUNK_OVERLAP = RC.DEFAULT_CHUNK_OVERLAP
DEFAULT_SEPARATORS = list(RC.DEFAULT_SEPARATORS)
# 上下文补齐（孤立图表补前后文）默认与上限：超过上限的补齐会把语义冲淡
AUGMENT_MAX_CHARS = 2000
# 媒体/表格切片强制补前后文的兜底窗口：全局开关没开、或页面把窗口配成 0 时按它补，
# 保证「孤立的[图片]」至少带一段相邻正文（否则正文里没有可召回的字，向量也召不回）
MEDIA_AUGMENT_DEFAULT = 120

# ==================== 扩展名分组（决定走哪个解析分支） ====================
EXT_PDF = {"pdf"}
EXT_WORD = {"docx", "doc"}
EXT_PPT = {"pptx", "ppt"}
EXT_EXCEL = {"xlsx", "xls", "csv"}
EXT_MARKDOWN = {"md", "markdown"}
EXT_HTML = {"html", "htm"}
EXT_JSON = {"json"}
EXT_TEXT = {"txt"}
# 只有增强解析引擎才值得重试的格式（纯文本类 native 已经够好，不做二次解析）
EXT_NEED_ENHANCED = EXT_PDF | EXT_PPT | EXT_WORD | EXT_EXCEL

TEXT_FORMATS = EXT_MARKDOWN | EXT_HTML | EXT_JSON | EXT_TEXT
BINARY_FORMATS = EXT_PDF | EXT_WORD | EXT_PPT | EXT_EXCEL

# 老版 office 二进制格式（.doc/.ppt）：native 读不动，只能靠 minerU/外部转换
LEGACY_OFFICE = {"doc", "ppt"}

# ==================== sidecar（解析产物落存储，重试不再重解析） ====================
SIDECAR_VERSION = RC.SIDECAR_VERSION
SIDECAR_SEP = "\n"

# ==================== 文本处理阈值 ====================
# 页眉页脚判定：同一段落在多页重复出现的比例超过该值即认为它是页眉/页脚
HEADER_FOOTER_REPEAT_RATIO = 0.4
# 目录判定：命中"标题 + 引导点 + 页码"样式的行占比超过该值的那一段视为目录页
TOC_LINE_RATIO = 0.5
# 扫描件判定：首页可抽字符数低于该值（配合图片数）即倾向扫描件
SCANNED_MIN_CHARS_PER_PAGE = 30
# 单篇文档最多提取多少张内嵌图片：图片一律要先拿到内存再传存储，
# 不设上限的话一本几百页的扫描册子会把 worker 内存撑爆（超限只留图注文字）
MAX_EMBED_IMAGES = 200
# CJK 占比超过该值即认为是中文文档（影响分词与分隔符选择）
CJK_RATIO_THRESHOLD = 0.3

# auto 引擎判据（转引常量，避免两处定义）
AUTO_MIN_PAGES = RC.AUTO_ENGINE_MIN_PAGES
AUTO_MIN_IMAGES = RC.AUTO_ENGINE_MIN_IMAGES
AUTO_SCAN_TEXT_RATIO = RC.AUTO_ENGINE_SCAN_TEXT_RATIO

# ==================== minerU 私有化服务的调用参数 ====================
MINERU_DEFAULT_TIMEOUT = RC.PARSE_OPTION_DEFAULTS["mineru_timeout"]
MINERU_DEFAULT_POLL_INTERVAL = 3
MINERU_MAX_WAIT_NULL = 30
# 任务状态（不同版本拼写有差异，统一小写比较）
MINERU_STATE_DONE = {"done", "success", "completed", "succeed"}
MINERU_STATE_FAILED = {"failed", "error"}
MINERU_STATE_RUNNING = {"pending", "waiting", "processing", "running", "converting"}
