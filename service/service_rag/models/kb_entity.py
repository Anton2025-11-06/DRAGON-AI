# -*- coding: utf-8 -*-
"""知识库 RAG ORM 实体（逐列对齐 sql/v2_init.sql PART 2 的三张表）。

口径与约束：
- **建表以 v2_init.sql 为权威**，本文件只做查询与写入映射，不跑 create_all；
  改列必须同时改 DDL，否则线上库和实体分叉（列名、默认值、可空性都按 DDL）。
- 主键用 Integer：与 workflow/skill 等实体一致（BIGINT UNSIGNED 主键在 SQLite 变体下无法自增）。
- 状态列是**大写字符串**（PENDING/PARSING/ANALYZING/PROCESSING/PROCESSED/FAILED），
  合法取值与流转只在 common_constants.rag_constant 定义，业务代码禁止出现字面量。
- 知识库的扩展元数据列在 DDL 里叫 metadata，但 SQLAlchemy 的声明基类已把
  ``Base.metadata`` 用于 MetaData 对象，属性名必须另起（kb_metadata），只保留列名一致。
- 切片表（tb_document_chunk）是「可再生产物」：没有 is_deleted 列，重新分块按 doc_id
  整批物理替换（软删会让 uk_doc_chunk 撞车，也会让 ES 与 MySQL 的切片数长期对不上）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (JSON, DateTime, Integer, String, Text,
                        UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column

from common.common_constants import rag_constant as RC
from common.common_entity.base_entity import Base


class KnowledgeBase(Base):
    """知识库表：doc/image/audio_video 三种类型共用一张表。

    永久锁定列（创建后不可改，service 层强制校验）：
    - kb_type：存储链路与向量空间按类型分叉
    - embedding_model_id / embedding_dim：换模型等于换向量空间，历史切片全部作废
    """
    __tablename__ = "tb_knowledge_base"

    kb_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="知识库名称")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    kb_type: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RC.KB_TYPE_DOC, index=True,
        comment="doc/image/audio_video，创建后锁定")
    # 组织隔离标识：随切片写入 ES、随实体写入 Neo4j（上层鉴权、底层裸查，隔离维度靠它兜底）
    # 建库时取创建人的归属部门 id（没部门直接拒绝建库，不读任何配置），创建后不可改
    org_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    embedding_model_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                    comment="向量模型 id（tb_model.id），锁定")
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False,
                                               default=RC.RAG_VECTOR_DIM)
    rerank_model_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                                 comment="0=不启用重排")
    chat_model_id: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                               comment="音视频型的媒体理解/摘要模型 id（doc 型已拆到下面两列）")
    # doc 型把「图谱抽取」与「图片理解」从 chat_model_id 里拆出来各自一位：
    # 一个模型同时背两个职责时，用户换了问答模型会连带把图谱抽取换成另一个厂商的
    # 输出风格，已建图谱的实体口径跟着漂；旧库两列为 0 时按 chat_model_id 回退。
    extract_model_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="知识图谱实体抽取模型 id（仅 doc 型且开了图谱时必填，0=沿用 chat_model_id）")
    image_model_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="图片理解模型 id（仅 doc 型且解析开关 image_understand 打开时必填）")
    parser_engine: Mapped[str] = mapped_column(
        String(16), nullable=False, default=RC.PARSE_ENGINE_NATIVE,
        comment="native/docling/mineru（仅 doc 型生效）")
    parse_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="预处理开关 + 引擎专有参数")
    chunk_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="分块策略与参数（8 种策略全前端可配）")
    retrieve_config: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="top_k/score_threshold/向量权重/rerank/keyword_boost")
    graph_enabled: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="图谱库级开关：开了还要按文档手动构建")
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1,
                                        comment="保留列（启用/停用功能已取消，代码不再读写，默认恒为 1）")
    owner_dept_id: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="归属部门（展示/统计）；数据权限按 created_by 现算")
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1,
        comment="配置版本号，每次修改 +1；解析任务带着它跑，文档的 parse_version 与它比对即知是否需要重解析")
    # 列名 metadata 与 SQLAlchemy 声明基类的 Base.metadata 冲突，故属性名换成 kb_metadata
    kb_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSON, nullable=True, comment="扩展元数据（前端透传，不参与解析与检索）")
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            comment="创建人用户ID（ACL 归属人判定列）")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class Document(Base):
    """知识库文档表：原件在公共存储（rag/ 前缀 + kb_id 目录），本表存元数据与状态机。

    三个彼此独立的状态维度：
    - status：解析流水线状态机（rag_constant.DOC_STATUS_*），进度明细在 Redis
    - vectorized：向量是否已写 ES（重切分/清向量可单独回退这一位）
    - graph_state：图谱构建态（0/1/2/3，文档处理完成后可独立重跑的后置任务）
    """
    __tablename__ = "tb_document"

    doc_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True,
                                       comment="所属知识库（ES/Neo4j 的隔离维度）")
    doc_name: Mapped[str] = mapped_column(String(255), nullable=False,
                                          comment="文档标题，列表页可改（接口 DTO 里叫 title）")
    file_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="上传时原始文件名")
    file_path: Mapped[str] = mapped_column(
        String(500), nullable=False,
        comment="原件在公共存储中的句柄（raw/{kb_id}/{doc_id}.{ext}，相对 storage.path_prefix=rag/）")
    file_ext: Mapped[Optional[str]] = mapped_column(String(16), nullable=True,
                                                     comment="小写、不含点，决定解析链路与分块默认策略")
    file_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    content_type: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="MIME 类型")
    media_type: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="text/image/audio_video，与库 kb_type 同族")
    parser_engine: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, comment="本次实际使用的引擎（自动选择也回填结果，便于排查）")
    parse_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="产出该解析结果时的知识库 version，与库当前 version 不等即代表需要重解析")
    sidecar_path: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True, comment="解析产物 blocks.jsonl 的存储句柄（重跑分块不必重新解析）")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RC.DOC_STATUS_PENDING,
                                        index=True)
    vectorized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    graph_state: Mapped[int] = mapped_column(Integer, nullable=False, default=RC.KG_STATE_NONE,
                                             comment="0-未构建 1-构建中 2-已构建 3-失败")
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="PDF/PPT 页数")
    media_duration: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="音视频时长（秒）")
    media_summary: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="音视频理解摘要（audio_video 型被检索命中的正文）")
    task_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True,
                                                   comment="最近一次 ARQ 任务号（进度轮询回查用）")
    error_msg: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            comment="创建人用户ID（ACL 归属人判定列）")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())


class DocumentChunk(Base):
    """文档切片表：切片的**完整正文**只在这里（ES 的 content 是为检索准备的副本）。

    与 ES 文档的关系：ES ``_id = {kb_id}_{doc_id}_{chunk_index}``，本表的
    uk_doc_chunk(doc_id, chunk_index) 与之对应，重跑幂等覆盖、不断点错位。
    """
    __tablename__ = "tb_document_chunk"
    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uk_doc_chunk"),
    )

    chunk_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True,
                                       comment="冗余存储：按库统计/清理不必回连文档表")
    doc_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    chunk_type: Mapped[str] = mapped_column(String(16), nullable=False, default=RC.CHUNK_TYPE_TEXT,
                                            comment="text/image/audio_video（ES 侧同值）")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="切片正文（页面预览看到的才是原文）")
    embed_text: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="实际送向量模型的文本（面包屑 + 正文 + 前后文补齐）")
    title_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="标题层级面包屑")
    sheet_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="Excel 工作表名")
    page_num: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="起始页码，0=非分页文档")
    block_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True,
                                                    comment="来源解析块 id，可对回 sidecar")
    media_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True,
                                                     comment="图片/音视频在公共存储中的句柄")
    media_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available: Mapped[int] = mapped_column(Integer, nullable=False, default=1,
                                           comment="0-人工停用（不删数据，检索直接排除）1-可用")
    vectorized: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            comment="本切片向量已写入 ES（批量重向量化的断点续跑依据）")
    extra: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True,
                                                  comment="表头与行区间、图片位置框等，页面按需展开")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0,
                                            comment="创建人用户ID（继承所属文档的归属人）")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
