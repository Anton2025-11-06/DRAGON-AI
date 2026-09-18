# -*- coding: utf-8 -*-
"""
技能管理服务：SKILL.zip 上传（原件存储）+ 元信息 CRUD + 预览 / 下载 / 单文件编辑
- 存储：压缩包原件经公共存储 common_storage 落盘（本地后端永久保存 / OSS 对象），
  统一存在存储后端的 skills/ 子目录下（存储名为 skills/{uuid}_{code}.zip），
  数据表记录 zip 包文件名 + 存储句柄（含子目录的相对地址）+ 创建人/时间等
- 不再把 zip 解压成常驻目录：预览 / 下载 / 单文件编辑都按需从存储取回 zip 在内存处理
- zipfile 为同步 CPU 密集操作（最大 100MB），统一经 asyncio.to_thread 卸载到工作线程，避免阻塞事件循环
- 压缩包要求：≤100MB、必须含 SKILL.md、逐成员校验防路径穿越；支持单顶层目录包裹自动剥离
"""
import asyncio
import io
import json
import re
import zipfile
from typing import Optional

from sqlalchemy import delete, func, or_, select, update

from common import common_storage
from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client
from common.common_storage.base import new_file_name
from service.service_workflow.models.agent_entity import Skill
from common.common_threadpool.pool import thread_pool

# 压缩包大小上限（100MB）
MAX_SKILL_SIZE = 100 * 1024 * 1024

# 可预览的文本扩展名（二进制文件在预览中仅列出路径，内容留空）
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".vue", ".java", ".go", ".c", ".cpp", ".h",
    ".sh", ".bat", ".ps1", ".sql", ".log", ".ini", ".conf", ".toml",
}

# 目录名安全校验：禁止路径分隔符/控制字符，允许中文等 Unicode；≤128 位
_CODE_RE = re.compile(r"^[^/\\\x00-\x1f]{1,128}$")

# 技能 zip 在存储后端统一归入的子目录（本地=local_dir/skills，OSS=path_prefix/skills）
SKILL_STORAGE_DIR = "skills"


def _skill_storage_name(code: str) -> str:
    """技能 zip 的存储名：{子目录}/{uuid}_{code}.zip（安全相对路径，后端各自补 root/前缀）。"""
    return f"{SKILL_STORAGE_DIR}/{new_file_name(f'{code}.zip')}"


class SkillService:
    """技能目录：zip 原件存储 + 元信息/内容管理（预览/编辑按需从存储取回解压）"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10, keyword: str = None,
                   category: str = None, status: int = None) -> dict:
        async with mysql_client.get_session() as session:
            conds = []
            if keyword:
                kw = f"%{keyword}%"
                conds.append(or_(Skill.name.like(kw), Skill.code.like(kw)))
            if category:
                conds.append(Skill.category == category)
            if status is not None:
                conds.append(Skill.status == status)
            total = (await session.execute(
                select(func.count()).select_from(Skill).where(*conds))).scalar()
            rows = (await session.execute(
                select(Skill).where(*conds)
                .order_by(Skill.id.desc())
                .limit(page_size).offset((page - 1) * page_size))).scalars().all()
            return {"total": total, "items": [SkillService._row_to_dict(r) for r in rows]}

    @staticmethod
    async def get_by_id(id_: int) -> Optional[Skill]:
        async with mysql_client.get_session() as session:
            return (await session.execute(
                select(Skill).where(Skill.id == id_))).scalar_one_or_none()

    @staticmethod
    def _fmt_time(value) -> Optional[str]:
        """DATETIME → 前端展示字符串（对齐旧 SQL 的 DATE_FORMAT 口径）。"""
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else None

    @staticmethod
    def _row_to_dict(row: Skill) -> dict:
        code = row.code
        return {
            "id": row.id, "name": row.name, "code": code,
            "description": row.description, "category": row.category, "icon": row.icon,
            "tags": SkillService._parse_tags(row.tags),
            "status": bool(row.status),
            # 展示用目录路径：技能 zip 统一落在存储后端 skills/ 子目录，逻辑归入 skills/{code}
            "skillPath": row.skill_path or f"skills/{code}",
            "resourceCount": row.resource_count, "created_by": row.created_by,
            "create_time": SkillService._fmt_time(row.create_time),
            "update_time": SkillService._fmt_time(row.update_time),
            "skillFile": "SKILL.md",
            "zipFileName": row.zip_file_name or f"{code}.zip",
        }

    # ==================== 元信息 / 预览 ====================
    @staticmethod
    async def detail(id_: int) -> dict:
        """技能元信息"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        return SkillService._row_to_dict(row)

    @staticmethod
    async def preview(id_: int) -> dict:
        """SKILL.md 内容 + 资源树 + 文本文件内容（按需取回 zip 内存解压，对齐前端 SkillDetailResp）"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        data = SkillService._row_to_dict(row)
        zip_bytes = await SkillService._load_zip(row)
        entries = await asyncio.get_running_loop().run_in_executor(thread_pool, SkillService._zip_entries, zip_bytes)
        resources = sorted(entries)
        skill_content = ""
        contents: dict = {}
        for rel, raw in entries.items():
            if not SkillService._is_text_file(rel):
                continue
            content = SkillService._decode_text(raw)
            if rel == "SKILL.md" or rel.endswith("/SKILL.md"):
                skill_content = content
            contents[rel] = content
        data["skillContent"] = skill_content
        data["resources"] = resources
        data["resourceContents"] = contents
        return data

    @staticmethod
    async def download_zip(id_: int) -> tuple[bytes, str]:
        """取回 zip 原件字节 + 下载文件名（{code}.zip）"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        return await SkillService._load_zip(row), f"{row.code}.zip"

    # ==================== 上传 / 替换 ====================
    @staticmethod
    async def upload(name: str, code: str, data: bytes, description: str = None,
                     category: str = None, icon: str = None, tags: list = None,
                     created_by: int = 0, original_name: str = None) -> int:
        name = (name or "").strip()
        code = SkillService._normalize_code(code)
        if not name or not code:
            raise ValueError("技能名称和技能标识不能为空")
        zip_bytes, count = await asyncio.get_running_loop().run_in_executor(thread_pool, SkillService._normalize_zip,
                                                                            data)
        storage_name = _skill_storage_name(code)
        display_name = SkillService._safe_file_name(original_name) or f"{code}.zip"
        await SkillService._store_zip(storage_name, zip_bytes)
        try:
            async with mysql_client.get_session() as session:
                if (await session.execute(
                        select(Skill.id).where(Skill.code == code))).first():
                    raise ValueError(f"技能标识已存在: {code}")
                skill = Skill(
                    name=name, code=code, description=description, category=category,
                    icon=icon, tags=SkillService._dump_tags(tags),
                    skill_path=f"skills/{code}", resource_count=count,
                    created_by=created_by, zip_file_name=display_name,
                    zip_storage_name=storage_name)
                session.add(skill)
                await session.flush()
                new_id = skill.id
                await session.commit()
        except Exception:
            # 落库失败（含 code 冲突）时回收刚存入的孤儿对象
            await common_storage.delete(storage_name)
            raise
        log.info(f"Skill uploaded: {code} (files={count})")
        return new_id

    @staticmethod
    async def replace(id_: int, data: bytes, name: str = None, description: str = None,
                      category: str = None, icon: str = None, tags: list = None,
                      original_name: str = None) -> None:
        """zip 替换：规范化后存入新对象，落库成功后再删旧对象（失败保留原 zip）"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        code = row.code
        old_storage = row.zip_storage_name
        zip_bytes, count = await asyncio.get_running_loop().run_in_executor(thread_pool, SkillService._normalize_zip,
                                                                            data)
        storage_name = _skill_storage_name(code)
        display_name = SkillService._safe_file_name(original_name) or f"{code}.zip"
        await SkillService._store_zip(storage_name, zip_bytes)
        try:
            async with mysql_client.get_session() as session:
                values: dict = {"resource_count": count, "zip_file_name": display_name,
                                "zip_storage_name": storage_name}
                if name is not None:
                    values["name"] = (name or "").strip()
                if description is not None:
                    values["description"] = description
                if category is not None:
                    values["category"] = category
                if icon is not None:
                    values["icon"] = icon
                if tags is not None:
                    values["tags"] = SkillService._dump_tags(tags)
                await session.execute(
                    update(Skill).where(Skill.id == id_).values(**values))
                await session.commit()
        except Exception:
            await common_storage.delete(storage_name)
            raise
        if old_storage and old_storage != storage_name:
            await common_storage.delete(old_storage)
        log.info(f"Skill replaced: {code}")

    # ==================== 重命名 / 状态 / 删除 ====================
    @staticmethod
    async def rename(id_: int, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("技能名称不能为空")
        async with mysql_client.get_session() as session:
            result = await session.execute(
                update(Skill).where(Skill.id == id_).values(name=name))
            await session.commit()
            if result.rowcount == 0:
                raise ValueError("技能不存在")

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> None:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                update(Skill).where(Skill.id == id_)
                .values(status=1 if status else 0))
            await session.commit()
            if result.rowcount == 0:
                raise ValueError("技能不存在")

    @staticmethod
    async def delete(id_: int) -> None:
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        async with mysql_client.get_session() as session:
            await session.execute(delete(Skill).where(Skill.id == id_))
            await session.commit()
        if row.zip_storage_name:
            await common_storage.delete(row.zip_storage_name)
        log.info(f"Skill deleted: id={id_}")

    # ==================== 单文件编辑 ====================
    @staticmethod
    async def update_file(id_: int, rel_path: str, content: str) -> None:
        """改 zip 内某个文本文件：取回→改成员→重新打包存新对象→落库后删旧对象"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        code = row.code
        old_storage = row.zip_storage_name
        rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            raise ValueError("文件路径不能为空")
        if any(p in ("..", ".") for p in rel.split("/")):
            raise ValueError("非法文件路径")
        if not SkillService._is_text_file(rel):
            raise ValueError("仅支持编辑文本文件")
        zip_bytes = await SkillService._load_zip(row)
        entries = await asyncio.get_running_loop().run_in_executor(thread_pool, SkillService._zip_entries, zip_bytes)
        if rel not in entries:
            raise ValueError("目标文件不存在")
        entries[rel] = (content or "").encode("utf-8")
        zip_bytes = await asyncio.get_running_loop().run_in_executor(thread_pool, SkillService._build_zip, entries)
        storage_name = _skill_storage_name(code)
        await SkillService._store_zip(storage_name, zip_bytes)
        try:
            async with mysql_client.get_session() as session:
                await session.execute(
                    update(Skill).where(Skill.id == id_)
                    .values(zip_storage_name=storage_name))
                await session.commit()
        except Exception:
            await common_storage.delete(storage_name)
            raise
        if old_storage and old_storage != storage_name:
            await common_storage.delete(old_storage)
        log.info(f"Skill file updated: {code}/{rel}")

    # ==================== 存储读写（公共存储后端，永久保存） ====================
    @staticmethod
    async def _store_zip(storage_name: str, zip_bytes: bytes) -> None:
        await common_storage.get_storage().save(storage_name, zip_bytes, "application/zip")

    @staticmethod
    async def _load_zip(row: Skill) -> bytes:
        storage_name = row.zip_storage_name
        if not storage_name:
            raise ValueError("技能 zip 记录缺失，请重新上传")
        return await common_storage.download(storage_name)

    # ==================== zip 校验与规范化 ====================
    @staticmethod
    def _normalize_zip(data: bytes) -> tuple[bytes, int]:
        """校验并规范化 zip：大小≤100MB、防路径穿越、必须含 SKILL.md、剥离单顶层目录、
        过滤 __MACOSX/隐藏项；返回 (规范化后的 zip 字节, 资源文件数)。"""
        if len(data) > MAX_SKILL_SIZE:
            raise ValueError(f"压缩包超过 {MAX_SKILL_SIZE // 1024 // 1024}MB 限制")
        try:
            zf = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            raise ValueError("不是有效的 zip 压缩包")
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if not infos:
            raise ValueError("压缩包为空")
        names = [i.filename for i in infos]
        # 单层目录包裹检测：所有文件同在一个顶层目录下且根下无直接文件 → 剥离第一层
        top_levels = {n.split("/")[0] for n in names}
        strip = not any("/" not in n for n in names) and len(top_levels) == 1
        total = 0
        has_skill_md = False
        entries: dict = {}
        for info in infos:
            parts = info.filename.split("/")
            # 路径穿越成分（.. / .）显式拒绝——先于隐藏文件过滤，避免被静默跳过
            if any(p in ("..", ".") for p in parts):
                raise ValueError(f"压缩包包含非法路径: {info.filename}")
            if strip:
                parts = parts[1:]
            # 过滤 __MACOSX / 隐藏文件 / 隐藏目录
            if not parts or any(p == "__MACOSX" or p.startswith(".") for p in parts):
                continue
            rel = "/".join(parts)
            if not rel:
                continue
            total += info.file_size
            if total > MAX_SKILL_SIZE:
                raise ValueError(f"压缩包超过 {MAX_SKILL_SIZE // 1024 // 1024}MB 限制")
            if rel == "SKILL.md" or rel.endswith("/SKILL.md"):
                has_skill_md = True
            entries[rel] = zf.read(info)
        if not entries:
            raise ValueError("压缩包内没有有效文件")
        if not has_skill_md:
            raise ValueError("压缩包内必须包含 SKILL.md")
        return SkillService._build_zip(entries), len(entries)

    @staticmethod
    def _build_zip(entries: dict) -> bytes:
        """把 {相对路径: 字节} 打成 zip（内存）"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for rel, raw in entries.items():
                zf.writestr(rel, raw)
        return buf.getvalue()

    @staticmethod
    def _zip_entries(zip_bytes: bytes) -> dict:
        """zip 字节 → {相对路径: 字节}（仅文件成员）"""
        try:
            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
        except zipfile.BadZipFile:
            raise ValueError("技能 zip 已损坏，请重新上传")
        return {i.filename: zf.read(i) for i in zf.infolist() if not i.is_dir()}

    # ==================== 工具方法 ====================
    @staticmethod
    def _normalize_code(code: str) -> str:
        """规范化技能标识：取文件名（去 .zip/目录），非法字符转 '-'，并做目录安全校验"""
        code = (code or "").strip().replace("\\", "/").split("/")[-1]
        if code.lower().endswith(".zip"):
            code = code[:-4]
        # 除字母/数字/下划线/点/中文外统一转 '-'；首尾去除 - 与 .
        code = re.sub(r"[^\w.-]", "-", code, flags=re.UNICODE)
        code = re.sub(r"-+", "-", code).strip("-.")[:128]
        if not code or code in (".", "..") or not _CODE_RE.match(code):
            raise ValueError("技能标识需为字母/数字/中划线/下划线（≤128位）且不含路径分隔符")
        return code

    @staticmethod
    def _safe_file_name(name: Optional[str]) -> str:
        """展示用原始 zip 文件名：去目录、截断，仅记录不进文件系统"""
        name = (name or "").replace("\\", "/").split("/")[-1].strip()
        return name[:255]

    @staticmethod
    def _parse_tags(raw) -> list:
        if isinstance(raw, list):
            return [str(t) for t in raw if str(t).strip()]
        if not raw:
            return []
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                return [str(t) for t in parsed if str(t).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
        return [t.strip() for t in str(raw).split(",") if t.strip()]

    @staticmethod
    def _dump_tags(tags: list) -> str:
        tags = SkillService._parse_tags(tags)
        return json.dumps(tags, ensure_ascii=False) if tags else None

    @staticmethod
    def _is_text_file(rel: str) -> bool:
        name = rel.rsplit("/", 1)[-1].lower()
        if name in ("skill.md", "dockerfile", "makefile"):
            return True
        return ("." + name.rsplit(".", 1)[-1]).lower() in TEXT_EXTS if "." in name else False

    @staticmethod
    def _decode_text(raw: bytes) -> str:
        try:
            text_str = raw.decode("utf-8")
        except UnicodeDecodeError:
            return ""
        if len(text_str) > 200 * 1024:
            text_str = text_str[: 200 * 1024] + "\n...(内容过长已截断)"
        return text_str
