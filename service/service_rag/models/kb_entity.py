from datetime import datetime

from sqlalchemy import String, Text, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class KnowledgeBase(Base):
    """知识库表：业务元数据，向量存 Milvus 对应 collection kb_{kb_id}"""
    __tablename__ = "tb_knowledge_base"

    kb_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    owner_id: Mapped[int] = mapped_column(nullable=False, index=True)
    is_public: Mapped[int] = mapped_column(nullable=False, default=0)   # 0私有 1公开
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class Document(Base):
    """知识库文档表：仅业务元数据，内容落在 chunk 表、向量落在 Milvus"""
    __tablename__ = "tb_document"

    doc_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(nullable=False, index=True)
    doc_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(nullable=False, default=0)
    file_type: Mapped[str] = mapped_column(String(16), nullable=True)
    chunk_count: Mapped[int] = mapped_column(nullable=False, default=0)
    status: Mapped[int] = mapped_column(nullable=False, default=0)     # DOC_STATUS_*
    error_msg: Mapped[str] = mapped_column(String(500), nullable=True)
    uploader_id: Mapped[int] = mapped_column(nullable=False)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class DocumentChunk(Base):
    """文档分块表：正文存 MySQL，向量存 Milvus，通过 chunk_id 回查"""
    __tablename__ = "tb_document_chunk"

    chunk_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(nullable=False, index=True)
    kb_id: Mapped[int] = mapped_column(nullable=False, index=True)
    chunk_index: Mapped[int] = mapped_column(nullable=False, default=0)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class KbShare(Base):
    """知识库授权共享表：可授权给用户或角色，支持过期时间"""
    __tablename__ = "tb_kb_share"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(nullable=False, index=True)
    share_type: Mapped[int] = mapped_column(nullable=False, default=1)  # 1用户 2角色
    target_id: Mapped[int] = mapped_column(nullable=False, index=True)
    expire_time: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    create_by: Mapped[int] = mapped_column(nullable=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())