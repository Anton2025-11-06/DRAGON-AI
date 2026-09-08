import os
from datetime import datetime

import aiofiles
from fastapi import UploadFile
from sqlalchemy import delete, func, or_, select, update

from arq_tasks.tasks.vectorize.tasks import vectorize_document
from common.common_constants.constant import (DOC_STATUS_PENDING, MAX_DOC_SIZE, UPLOAD_TMP_DIR)
from common.common_entity.rbac_entity import Role, UserRole
from common.common_log.log_init import log
from common.common_middleware.exception_handler import UnauthorizedException
from common.common_milvus.milvus import milvus_client
from common.common_mysql.mysql import mysql_client
from service.service_rag.models.kb_entity import Document, DocumentChunk, KbShare, KnowledgeBase
from service.service_rag.schemas.kb_schema import KbCreateRequest, KbUpdateRequest


class KbService:

    # ==================== 权限 ====================
    @staticmethod
    async def _user_role_ids(session, user_id: int) -> list:
        rows = await session.execute(
            select(Role.role_id)
            .join(UserRole, UserRole.role_id == Role.role_id)
            .where(UserRole.user_id == user_id, Role.is_deleted == 0, Role.status == 1))
        return [r[0] for r in rows.all()]

    @staticmethod
    async def has_access(session, kb_id: int, user_id: int) -> bool:
        """所有者 / 公开 / 授权共享（用户或角色、未过期）"""
        kb = (await session.execute(
            select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                        KnowledgeBase.is_deleted == 0))).scalar_one_or_none()
        if not kb:
            return False
        if kb.owner_id == user_id or kb.is_public == 1:
            return True
        role_ids = await KbService._user_role_ids(session, user_id)
        now = datetime.now()
        shares = (await session.execute(
            select(KbShare).where(KbShare.kb_id == kb_id))).scalars().all()
        for share in shares:
            if share.expire_time and share.expire_time < now:
                continue
            if share.share_type == 1 and share.target_id == user_id:
                return True
            if share.share_type == 2 and share.target_id in role_ids:
                return True
        return False

    @staticmethod
    async def visible_kb_ids(session, user_id: int) -> list:
        """当前用户可见知识库 ID：我的 + 公开 + 授权共享"""
        my_ids = (await session.execute(
            select(KnowledgeBase.kb_id).where(KnowledgeBase.owner_id == user_id,
                                              KnowledgeBase.is_deleted == 0))).scalars().all()
        public_ids = (await session.execute(
            select(KnowledgeBase.kb_id).where(KnowledgeBase.is_public == 1,
                                              KnowledgeBase.is_deleted == 0))).scalars().all()
        role_ids = await KbService._user_role_ids(session, user_id)
        now = datetime.now()
        shares = (await session.execute(select(KbShare))).scalars().all()
        shared_ids = [s.kb_id for s in shares
                      if (not s.expire_time or s.expire_time >= now)
                      and ((s.share_type == 1 and s.target_id == user_id)
                           or (s.share_type == 2 and s.target_id in role_ids))]
        return list(dict.fromkeys([*my_ids, *public_ids, *shared_ids]))

    @staticmethod
    async def _require_owner(session, kb_id: int, user_id: int):
        if not await KbService.has_access(session, kb_id, user_id):
            raise UnauthorizedException("无权操作该知识库")

    # ==================== 知识库 ====================
    @staticmethod
    async def create_kb(request: KbCreateRequest, user_id: int):
        async with mysql_client.get_session() as session:
            kb = KnowledgeBase(kb_name=request.kb_name, description=request.description,
                               owner_id=user_id)
            session.add(kb)
            await session.commit()
            await session.refresh(kb)
        await milvus_client.ensure_collection(kb.kb_id)
        return kb

    @staticmethod
    async def list_kbs(page: int, page_size: int, user_id: int):
        async with mysql_client.get_session() as session:
            visible = await KbService.visible_kb_ids(session, user_id)
            if not visible:
                return {"total": 0, "items": []}
            query = select(KnowledgeBase).where(
                KnowledgeBase.kb_id.in_(visible), KnowledgeBase.is_deleted == 0)
            total = (await session.execute(
                select(func.count()).select_from(query.subquery()))).scalar()
            rows = (await session.execute(
                query.order_by(KnowledgeBase.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()
            return {"total": total, "items": rows}

    @staticmethod
    async def get_kb(kb_id: int, user_id: int):
        async with mysql_client.get_session() as session:
            if not await KbService.has_access(session, kb_id, user_id):
                raise UnauthorizedException("无权访问该知识库")
            return (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                            KnowledgeBase.is_deleted == 0))).scalar_one_or_none()

    @staticmethod
    async def update_kb(kb_id: int, request: KbUpdateRequest, user_id: int):
        async with mysql_client.get_session() as session:
            kb = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                            KnowledgeBase.is_deleted == 0))).scalar_one_or_none()
            if not kb:
                raise ValueError("知识库不存在")
            if kb.owner_id != user_id:
                raise UnauthorizedException("仅所有者可修改知识库")
            data = {k: v for k, v in request.model_dump().items() if v is not None}
            if data:
                await session.execute(update(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id).values(**data))
                await session.commit()
            return True

    @staticmethod
    async def delete_kb(kb_id: int, user_id: int):
        async with mysql_client.get_session() as session:
            kb = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                            KnowledgeBase.is_deleted == 0))).scalar_one_or_none()
            if not kb:
                raise ValueError("知识库不存在")
            if kb.owner_id != user_id:
                raise UnauthorizedException("仅所有者可删除知识库")
            await session.execute(delete(KbShare).where(KbShare.kb_id == kb_id))
            await session.execute(delete(DocumentChunk).where(DocumentChunk.kb_id == kb_id))
            rows = (await session.execute(
                select(Document).where(Document.kb_id == kb_id))).scalars().all()
            await session.execute(update(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id).values(is_deleted=1))
            await session.commit()
        await milvus_client.drop_collection(kb_id)
        for doc in rows:
            if doc.file_path and os.path.exists(doc.file_path):
                try:
                    os.remove(doc.file_path)
                except OSError as e:
                    log.warning(f"Remove file failed: {str(e)}")
        return True

    # ==================== 文档 ====================
    @staticmethod
    async def list_docs(kb_id: int, page: int, page_size: int, user_id: int):
        async with mysql_client.get_session() as session:
            if not await KbService.has_access(session, kb_id, user_id):
                raise UnauthorizedException("无权访问该知识库")
            query = select(Document).where(Document.kb_id == kb_id, Document.is_deleted == 0)
            total = (await session.execute(
                select(func.count()).select_from(query.subquery()))).scalar()
            rows = (await session.execute(
                query.order_by(Document.create_time.desc())
                .offset((page - 1) * page_size).limit(page_size))).scalars().all()
            return {"total": total, "items": rows}

    @staticmethod
    async def upload_document(kb_id: int, user_id: int, file: UploadFile):
        """保存文档并投递 Celery 向量化任务（前后台解耦）"""
        filename = file.filename or "unnamed"
        ext = os.path.splitext(filename)[1].lower()
        async with mysql_client.get_session() as session:
            if not await KbService.has_access(session, kb_id, user_id):
                raise UnauthorizedException("无权访问该知识库")
            doc = Document(kb_id=kb_id, doc_name=filename, file_type=ext,
                           status=DOC_STATUS_PENDING, uploader_id=user_id, file_path="")
            session.add(doc)
            await session.flush()
            doc_id = doc.doc_id

        file_dir = os.path.join(os.environ.get("upload_tmp_dir", UPLOAD_TMP_DIR), str(kb_id))
        os.makedirs(file_dir, exist_ok=True)
        safe_name = f"{doc_id}_{filename.replace(os.sep, '_')}"
        file_path = os.path.join(file_dir, safe_name)
        size = 0
        try:
            async with aiofiles.open(file_path, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > MAX_DOC_SIZE:
                        raise ValueError("文件超过大小限制(50MB)")
                    await out.write(chunk)
        except Exception:
            if os.path.exists(file_path):
                os.remove(file_path)
            raise

        async with mysql_client.get_session() as session:
            await session.execute(
                update(Document).where(Document.doc_id == doc_id)
                .values(file_path=file_path, file_size=size))
            await session.commit()

        vectorize_document.delay(doc_id)
        log.info(f"Document {doc_id} uploaded, vectorize task dispatched")
        return doc_id

    @staticmethod
    async def delete_document(kb_id: int, doc_id: int, user_id: int):
        async with mysql_client.get_session() as session:
            if not await KbService.has_access(session, kb_id, user_id):
                raise UnauthorizedException("无权访问该知识库")
            doc = (await session.execute(
                select(Document).where(Document.doc_id == doc_id,
                                       Document.kb_id == kb_id))).scalar_one_or_none()
            if not doc:
                raise ValueError("文档不存在")
            await session.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == doc_id))
            await session.execute(update(Document).where(Document.doc_id == doc_id).values(is_deleted=1))
            await session.commit()
        await milvus_client.delete_by_doc(kb_id, doc_id)
        if doc.file_path and os.path.exists(doc.file_path):
            try:
                os.remove(doc.file_path)
            except OSError as e:
                log.warning(f"Remove file failed: {str(e)}")
        return True

    # ==================== 授权共享 ====================
    @staticmethod
    async def share_kb(kb_id: int, user_id: int, share_type: int, target_id: int,
                       expire_time: datetime = None):
        async with mysql_client.get_session() as session:
            kb = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                            KnowledgeBase.is_deleted == 0))).scalar_one_or_none()
            if not kb:
                raise ValueError("知识库不存在")
            if kb.owner_id != user_id:
                raise UnauthorizedException("仅所有者可授权共享")
            session.add(KbShare(kb_id=kb_id, share_type=share_type, target_id=target_id,
                                expire_time=expire_time, create_by=user_id))
            await session.commit()
            return True

    @staticmethod
    async def revoke_share(kb_id: int, share_id: int, user_id: int):
        async with mysql_client.get_session() as session:
            kb = (await session.execute(
                select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id,
                                            KnowledgeBase.is_deleted == 0))).scalar_one_or_none()
            if not kb or kb.owner_id != user_id:
                raise UnauthorizedException("仅所有者可撤销授权")
            await session.execute(delete(KbShare).where(KbShare.id == share_id, KbShare.kb_id == kb_id))
            await session.commit()
            return True

    @staticmethod
    async def list_shares(kb_id: int, user_id: int):
        async with mysql_client.get_session() as session:
            if not await KbService.has_access(session, kb_id, user_id):
                raise UnauthorizedException("无权访问该知识库")
            rows = (await session.execute(
                select(KbShare).where(KbShare.kb_id == kb_id))).scalars().all()
            return rows