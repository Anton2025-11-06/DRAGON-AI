# -*- coding: utf-8 -*-
"""
技能管理服务：SKILL.zip 上传解压存储 + 元信息 CRUD + 预览 / 下载 / 单文件编辑
- 存储根目录：SKILL_ROOT 环境变量（默认 /data/skills），不可写时回退 service/service_workflow/skills/（Windows 本地可跑）
- 压缩包要求：≤100MB、必须含 SKILL.md、逐成员校验防路径穿越；支持单顶层目录包裹自动剥离
"""
import io
import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from sqlalchemy import text

from common.common_log.log_init import log
from common.common_mysql.mysql import mysql_client

# 压缩包大小上限（100MB）
MAX_SKILL_SIZE = 100 * 1024 * 1024

# 可预览的文本扩展名（二进制文件在预览中仅列出路径）
TEXT_EXTS = {
    ".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html", ".htm",
    ".py", ".js", ".ts", ".tsx", ".vue", ".java", ".go", ".c", ".cpp", ".h",
    ".sh", ".bat", ".ps1", ".sql", ".log", ".ini", ".conf", ".toml",
}

# 存储根目录：优先 SKILL_ROOT 环境变量；启动时校验可写，否则回退服务本地目录
_ENV_ROOT = os.environ.get("SKILL_ROOT")
_FALLBACK_ROOT = Path(os.path.dirname(os.path.abspath(__file__))) / "skills"
try:
    SKILL_ROOT = Path(_ENV_ROOT) if _ENV_ROOT else _FALLBACK_ROOT
    SKILL_ROOT.mkdir(parents=True, exist_ok=True)
    _probe = SKILL_ROOT / ".write_probe"
    _probe.write_text("ok", encoding="utf-8")
    _probe.unlink()
except OSError:
    log.warning("SKILL_ROOT {} 不可写，回退本地 {}", SKILL_ROOT, _FALLBACK_ROOT)
    SKILL_ROOT = _FALLBACK_ROOT
    SKILL_ROOT.mkdir(parents=True, exist_ok=True)

# 目录名安全校验：禁止路径分隔符/控制字符，允许中文等 Unicode；≤128 位
_CODE_RE = re.compile(r"^[^/\\\x00-\x1f]{1,128}$")


class SkillService:
    """技能目录：zip 解压存储 + 元信息/内容管理"""

    # ==================== 查询 ====================
    @staticmethod
    async def page(page: int = 1, page_size: int = 10, keyword: str = None,
                   category: str = None, status: int = None) -> dict:
        async with mysql_client.get_session() as session:
            where = "WHERE 1=1"
            params = {}
            if keyword:
                where += " AND (name LIKE :kw OR code LIKE :kw)"
                params["kw"] = f"%{keyword}%"
            if category:
                where += " AND category = :category"
                params["category"] = category
            if status is not None:
                where += " AND status = :status"
                params["status"] = status
            total = (await session.execute(
                text(f"SELECT COUNT(*) FROM tb_skill {where}"), params)).scalar()
            rows = (await session.execute(
                text(f"""SELECT id, name, code, description, category, icon, tags, status,
                               skill_path, resource_count, created_by,
                               DATE_FORMAT(create_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS create_time,
                               DATE_FORMAT(update_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS update_time
                        FROM tb_skill {where}
                        ORDER BY id DESC LIMIT :limit OFFSET :offset"""),
                {**params, "limit": page_size, "offset": (page - 1) * page_size})).all()
            return {"total": total, "items": [SkillService._row_to_dict(r) for r in rows]}

    @staticmethod
    async def get_by_id(id_: int):
        async with mysql_client.get_session() as session:
            row = (await session.execute(
                text("""SELECT id, name, code, description, category, icon, tags, status,
                               skill_path, resource_count, created_by,
                               DATE_FORMAT(create_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS create_time,
                               DATE_FORMAT(update_time, '%%Y-%%m-%%d %%H:%%i:%%s') AS update_time
                        FROM tb_skill WHERE id = :id"""), {"id": id_})).first()
            return row

    @staticmethod
    def _row_to_dict(row) -> dict:
        code = row[2]
        return {
            "id": row[0], "name": row[1], "code": code,
            "description": row[3], "category": row[4], "icon": row[5],
            "tags": SkillService._parse_tags(row[6]),
            "status": bool(row[7]),
            "skillPath": row[8] or f"skills/{code}",
            "resourceCount": row[9], "created_by": row[10],
            "create_time": row[11], "update_time": row[12],
            "skillFile": "SKILL.md",
        }

    @staticmethod
    def _dir_of(row) -> Path:
        """技能目录绝对路径（目录名 = code）"""
        return SKILL_ROOT / row[2]

    # ==================== 预览 / 下载 ====================
    @staticmethod
    async def detail(id_: int) -> dict:
        """技能元信息"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        return SkillService._row_to_dict(row)

    @staticmethod
    async def preview(id_: int) -> dict:
        """SKILL.md 内容 + 资源树 + 文本文件内容（对齐前端 SkillDetailResp）"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        data = SkillService._row_to_dict(row)
        resources, contents, skill_content = [], {}, ""
        skill_dir = SkillService._dir_of(row)
        if skill_dir.is_dir():
            for p in sorted(skill_dir.rglob("*")):
                if not p.is_file():
                    continue
                rel = p.relative_to(skill_dir).as_posix()
                resources.append(rel)
                if not SkillService._is_text_file(rel):
                    continue
                content = SkillService._read_text(p)
                if rel == "SKILL.md" or rel.endswith("/SKILL.md"):
                    skill_content = content
                contents[rel] = content
        data["skillContent"] = skill_content
        data["resources"] = resources
        data["resourceContents"] = contents
        return data

    @staticmethod
    def zip_skill(skill_dir: Path) -> io.BytesIO:
        """把技能目录打包为 zip（内存），文件名 UTF-8"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(skill_dir.rglob("*")):
                if p.is_file():
                    zf.write(p, p.relative_to(skill_dir).as_posix())
        buf.seek(0)
        return buf

    # ==================== 上传 / 替换 ====================
    @staticmethod
    async def upload(name: str, code: str, data: bytes, description: str = None,
                     category: str = None, icon: str = None, tags: list = None,
                     created_by: int = 0) -> int:
        name = (name or "").strip()
        code = SkillService._normalize_code(code)
        if not name or not code:
            raise ValueError("技能名称和技能标识不能为空")
        async with mysql_client.get_session() as session:
            if (await session.execute(
                    text("SELECT id FROM tb_skill WHERE code = :code"),
                    {"code": code})).first():
                raise ValueError(f"技能标识已存在: {code}")
            try:
                skill_dir = SkillService._extract_zip(code, data)
                result = await session.execute(
                    text("""INSERT INTO tb_skill
                            (name, code, description, category, icon, tags, skill_path, resource_count, created_by)
                            VALUES (:name, :code, :description, :category, :icon, :tags, :skill_path, :resource_count, :created_by)"""),
                    {"name": name, "code": code, "description": description, "category": category,
                     "icon": icon, "tags": SkillService._dump_tags(tags),
                     "skill_path": f"skills/{code}",
                     "resource_count": SkillService._count_files(skill_dir),
                     "created_by": created_by})
                await session.commit()
            except Exception:
                shutil.rmtree(SKILL_ROOT / code, ignore_errors=True)
                raise
            log.info(f"Skill uploaded: {code} (files={SkillService._count_files(skill_dir)})")
            return result.lastrowid

    @staticmethod
    async def replace(id_: int, data: bytes, name: str = None, description: str = None,
                      category: str = None, icon: str = None, tags: list = None) -> None:
        """zip 替换：先解压到临时目录，成功后原子切换，失败保留原目录"""
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        code = row[2]
        tmp_dir = SKILL_ROOT / f".{code}.tmp-{int(time.time() * 1000)}"
        try:
            SkillService._extract_zip(code, data, target_dir=tmp_dir)
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        old_dir = SkillService._dir_of(row)
        shutil.rmtree(old_dir, ignore_errors=True)
        tmp_dir.rename(old_dir)
        async with mysql_client.get_session() as session:
            fields, params = ["resource_count = :rc", "update_time = NOW()"], {"id": id_}
            if name is not None:
                fields.append("name = :name"); params["name"] = (name or "").strip()
            if description is not None:
                fields.append("description = :description"); params["description"] = description
            if category is not None:
                fields.append("category = :category"); params["category"] = category
            if icon is not None:
                fields.append("icon = :icon"); params["icon"] = icon
            if tags is not None:
                fields.append("tags = :tags"); params["tags"] = SkillService._dump_tags(tags)
            params["rc"] = SkillService._count_files(old_dir)
            await session.execute(
                text(f"UPDATE tb_skill SET {', '.join(fields)} WHERE id = :id"), params)
            await session.commit()
        log.info(f"Skill replaced: {code}")

    # ==================== 重命名 / 状态 / 删除 ====================
    @staticmethod
    async def rename(id_: int, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise ValueError("技能名称不能为空")
        async with mysql_client.get_session() as session:
            result = await session.execute(
                text("UPDATE tb_skill SET name = :name WHERE id = :id"),
                {"name": name, "id": id_})
            await session.commit()
            if result.rowcount == 0:
                raise ValueError("技能不存在")

    @staticmethod
    async def toggle_status(id_: int, status: bool) -> None:
        async with mysql_client.get_session() as session:
            result = await session.execute(
                text("UPDATE tb_skill SET status = :status WHERE id = :id"),
                {"id": id_, "status": 1 if status else 0})
            await session.commit()
            if result.rowcount == 0:
                raise ValueError("技能不存在")

    @staticmethod
    async def delete(id_: int) -> None:
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        shutil.rmtree(SkillService._dir_of(row), ignore_errors=True)
        async with mysql_client.get_session() as session:
            await session.execute(text("DELETE FROM tb_skill WHERE id = :id"), {"id": id_})
            await session.commit()
        log.info(f"Skill deleted: id={id_}")

    # ==================== 单文件编辑 ====================
    @staticmethod
    async def update_file(id_: int, rel_path: str, content: str) -> None:
        row = await SkillService.get_by_id(id_)
        if not row:
            raise ValueError("技能不存在")
        rel = (rel_path or "").strip().replace("\\", "/").lstrip("/")
        if not rel:
            raise ValueError("文件路径不能为空")
        root = SkillService._dir_of(row).resolve()
        target = (root / rel).resolve()
        if target != root and not str(target).startswith(str(root) + os.sep):
            raise ValueError("非法文件路径")
        if not target.is_file():
            raise ValueError("目标文件不存在")
        if not SkillService._is_text_file(rel):
            raise ValueError("仅支持编辑文本文件")
        target.write_text(content or "", encoding="utf-8")
        log.info(f"Skill file updated: {rel}")

    # ==================== zip 校验与解压 ====================
    @staticmethod
    def _extract_zip(code: str, data: bytes, target_dir: Path = None) -> Path:
        """校验并解压 zip：大小≤100MB、防路径穿越、必须含 SKILL.md；支持单顶层目录包裹剥离"""
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
        entries = []
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
            clean = Path(*parts)
            if clean.is_absolute():
                raise ValueError(f"压缩包包含非法路径: {info.filename}")
            total += info.file_size
            if total > MAX_SKILL_SIZE:
                raise ValueError(f"压缩包超过 {MAX_SKILL_SIZE // 1024 // 1024}MB 限制")
            rel = clean.as_posix()
            if rel == "SKILL.md" or rel.endswith("/SKILL.md"):
                has_skill_md = True
            entries.append((info, rel))
        if not has_skill_md:
            raise ValueError("压缩包内必须包含 SKILL.md")
        dest = target_dir or (SKILL_ROOT / code)
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        dest.mkdir(parents=True, exist_ok=True)
        root_str = str(dest.resolve()) + os.sep
        for info, rel in entries:
            out = (dest / rel).resolve()
            if not str(out).startswith(root_str):
                raise ValueError(f"压缩包包含非法路径: {info.filename}")
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
        return dest

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
        return Path(rel).suffix.lower() in TEXT_EXTS

    @staticmethod
    def _read_text(path: Path) -> str:
        data = path.read_bytes()
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return ""
        if len(text) > 200 * 1024:
            text = text[: 200 * 1024] + "\n...(内容过长已截断)"
        return text

    @staticmethod
    def _count_files(skill_dir: Path) -> int:
        return sum(1 for p in skill_dir.rglob("*") if p.is_file())