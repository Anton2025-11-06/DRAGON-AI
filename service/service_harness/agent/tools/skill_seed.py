# -*- coding: utf-8 -*-
"""技能种入：挂载的技能 zip → 本地缓存目录 → OpenSandboxWorkspace skill_paths。

链路：SkillService 取回 zip 原件（共库共存储，进程内 import）→ 解压到
``{cache_root}/{user_id}/{skill_id}_{code}`` → 目录列表交给 workspace 构造
（OpenSandbox 在 initialize 时种入沙箱 skills/，reattach 场景沙箱已有副本）。

口径：
- 解压目录按用户分隔：同一技能多个用户同时挂载互不踩（目录覆盖写没有
  跨用户竞态；同一用户的轮次天然串行）；
- 每次挂载重新解压覆盖（技能 zip 可能已被替换；量级是 zip 内文件数，
  秒级以内）；
- zip slip 防护：成员路径含 ``..`` 或绝对路径一律拒绝（zip 来自平台
  normalize，防御性校验兜底）。

SkillService._load_zip / get_by_id 是跨服务借用的内部方法（同仓库共库的
进程内 import 模式，ragflow worker 同款口径）；带 ACL 的公共入口只有
download_zip（卡 ACTION_EXPORT，与挂载语义不符），挂载授权校验在
mount_loader 里按 use 口径做过了。

本模块只被 arq worker import（同 harness_agent 的口径）。
"""
from __future__ import annotations

import io
import re
import shutil
import zipfile
from pathlib import Path

from common.common_log.log_init import log


def _safe_extract(zip_bytes: bytes, target: Path) -> None:
    """zip 解压 + zip slip 防护（先整体校验再解压，不留半解压目录）。"""
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for info in zf.infolist():
            name = str(info.filename).replace("\\", "/")
            if name.startswith("/") or ".." in name.split("/"):
                raise ValueError(f"技能 zip 含非法路径成员: {name}")
        target.mkdir(parents=True, exist_ok=True)
        zf.extractall(target)


async def seed_skills(login_user: dict, skill_ids: list[int]) -> tuple[list[str], list[str]]:
    """挂载的技能 → (skill_paths 本地目录列表, 生效的技能名列表)。

    单个技能取回/解压失败只跳过该技能（记 warning），不阻断其它挂载能力。
    """
    from service.service_harness.services.harness_settings import skill_cache_dir
    from service.service_workflow.services.skill_service import SkillService

    user_id = int(login_user.get("user_id") or 0)
    root = Path(skill_cache_dir()) / str(user_id)
    paths: list[str] = []
    names: list[str] = []
    for skill_id in [int(i) for i in skill_ids or []]:
        try:
            row = await SkillService.get_by_id(skill_id)
            if row is None or not row.status:
                log.warning("harness skill mount skipped: id={} 不存在或已停用", skill_id)
                continue
            zip_bytes = await SkillService._load_zip(row)  # noqa: SLF001  见模块 docstring
            # 目录名里的非法字符折叠成下划线（code 已过 _CODE_RE，这里兜底）
            code = re.sub(r"[^0-9A-Za-z_-]+", "_", str(row.code or f"skill{skill_id}"))
            target = root / f"{skill_id}_{code}"
            if target.exists():
                shutil.rmtree(target)
            _safe_extract(zip_bytes, target)
            paths.append(str(target))
            names.append(str(row.name or code))
        except Exception as e:  # noqa: BLE001  单技能失败不影响其它挂载
            log.warning("harness skill mount skipped: id={} {}", skill_id, e)
    return paths, names
