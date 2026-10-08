# -*- coding: utf-8 -*-
"""资源实例数据权限（ACL + DataScope）求值层：全项目唯一的「某个人对某一条资源能不能做某个动作」判定。

架构位置（SPEC §3.2 / §13.4：上层鉴权、底层裸查）
--------------------------------------------------
本模块是**上层**，被各业务 service 调用；ES / Neo4j / 向量库 / 文件存储里没有任何权限概念，
它们只接受本层算好的 kb_id / 资源 id 白名单。四类授权主体（用户/角色/部门/用户组）+ 全员
全部落在 tb_resource_acl 一张表，隐式规则（我创建的、部门内的）不落库、按归属列现算。

三档并集口径（一次实现，三处消费，绝不允许各处再手写一份）
--------------------------------------------------------
1. **归属人档**：`created_by == 当前用户` → 该资源的全部动作（含 share，才能把授权转出去）；
2. **数据范围档**：归属人落在当前用户 data_scope 的部门集合内 → 只给 spec["scope"] 那一小组
   动作（一般是 view/use），这是「同事的东西我能用但不能改」的来源；
3. **显式授权档**：tb_resource_acl 里命中（主体 = 用户/角色/部门(含下级)/用户组/全员、动作匹配、
   未过期）的那条动作。
ADMIN 角色跳出三档：不做任何过滤（返回 None / 给全集），与 permission.is_admin 同一判定。

消费方式
--------
- 列表下推：`build_visible_cond(login_user, "workflow", Workflow.id, Workflow.created_by)`
  返回一个 SQL 条件（ADMIN 返回 None 表示不限），调用方 `.where()` 组合即可；
- 行内按钮：`decorate_actions(...)` 给每行写 `actions: [动作码]`，前端按钮显隐的唯一来源；
- 单点判定：`ensure_action(...)` / `ensure_any(...)` 不通过直接抛 403（统一文案带归属人）；
- 授权读写：见 service_system 的 acl_router，它按本模块的 spec 校验动作合法性。

⚠️ 部门「含下级」的方向
--------------------
授权授给部门 D（dept_include_sub=1）时，D 的全部子孙都命中；判定侧不展开 D 的子孙集，
而是拿登录载荷里预算好的 `dept_ancestor_ids`（自己 + 全部祖先）去匹配 grantee_id：
「我的祖先里有 D」等价于「我在 D 的子孙里」，一次 IN 判断，零递归。
include_sub=0 的行只匹配本人部门，因此判定额外要求 grantee_id == 本人 dept_id。
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from common.common_entity.rbac_entity import ResourceAcl
from common.common_exception.custom_exception import UnauthorizedException
from common.common_mysql.mysql import mysql_client
from common.common_permission.permission import get_login_scope, is_admin

# ==================== 动作码（与 sql/v2_init.sql 的 acl 列注释一一对应） ====================
# 通用四档
ACTION_VIEW = "view"        # 看详情/列表
ACTION_USE = "use"          # 引用它跑起来（工作流被调用、知识库被检索、工具被引用）
ACTION_EDIT = "edit"        # 改配置/内容
ACTION_DELETE = "delete"    # 删除（软删）
# 管理类（只有归属人或被显式授了才能做）
ACTION_SHARE = "share"      # 对这条资源做授权/撤权
# 工作流族
ACTION_CHAT = "chat"        # 会话对话
ACTION_COPY = "copy"        # 复制副本
ACTION_APIKEY = "apikey"    # 维护 API Key
ACTION_TEMPLATE = "template"  # 存为模板
ACTION_HISTORY = "history"  # 看执行记录
ACTION_EXPORT = "export"    # 下载（导出配置 / 取原件，页面按钮文案统一叫「下载」）
# 工具与技能族
ACTION_TEST = "test"        # 试跑
ACTION_TOOLS = "tools"      # 列 MCP 工具
ACTION_RENAME = "rename"    # 重命名
ACTION_REPLACE = "replace"  # 替换 zip
# 技能启停语义上就是改这一行，与「编辑」共用一个动作码（不另开 toggle 码，避免两码一义）；
# 它不单列在 spec["actions"] 里（与 edit 同码，列了会让下发的 actions 出现重复项）
ACTION_TOGGLE = ACTION_EDIT
# 知识库族
ACTION_UPLOAD = "upload"    # 往库里传文档
# 「构建向量」：删掉该文档已有的向量数据后重新向量化。动作码沿用 reparse，
# 已有授权记录不会因为改名失效（产品口径从「重新分块」演进而来，只有 LABELS 变）
ACTION_REPARSE = "reparse"
ACTION_GRAPH = "graph"      # 构建知识图谱（先清该文档旧图谱数据再重建）
ACTION_PREVIEW = "preview"  # 预览原文件（把原件签个只读 URL 给前端）
ACTION_CHUNK = "chunk"      # 切片管理（看/改/删单条切片，比「编辑文档」粒度更细）
# 库内文件族的两码：只在「知识库」的授权弹窗里出现，勾一次对该库当前与以后的所有文档生效。
# 另开码而不复用 view/delete：同一行 ACL 上 view 已经是「看得见这个库」、delete 是「删掉这个库」，
# 复用就会让「删除知识库」和「删库里一篇文档」变成同一个勾选项，那是越权而不是省事。
ACTION_CONTENT = "content"        # 查看解析后内容（解析产物与切片正文，不是原件）
ACTION_DOC_DELETE = "doc_delete"  # 删除知识（删库内单篇文档，不动知识库本身）
# 知识评测族：在「知识库-知识评测」页看到这个库并对它跑 RAGAS 评测。
# 只进 actions 与独立 group，绝不进 scope——同部门数据范围不自动放开评测可见性，
# 只有归属人/ADMIN/被显式授了 eval 才可见（需求：授权了评测 ACL 才能看到非本人创建的库）。
ACTION_EVAL = "eval"      # 知识评测（≤16 字符，符合 action 列约束）

# 授权主体类型（tb_resource_acl.grantee_type）
GRANTEE_USER = 1
GRANTEE_ROLE = 2
GRANTEE_DEPT = 3
GRANTEE_GROUP = 4
GRANTEE_ALL = 5
GRANTEE_LABELS = {
    GRANTEE_USER: "用户",
    GRANTEE_ROLE: "角色",
    GRANTEE_DEPT: "部门",
    GRANTEE_GROUP: "用户组",
    GRANTEE_ALL: "全员",
}

# =====================================================================================
# 资源清单：动作集合 + 数据范围档 + 对应 ORM（延迟导入，common 层不硬依赖 service 层）
# =====================================================================================
# 每项：
#   name        中文名（报错文案与授权弹窗标题）
#   id_col      主键属性名（前端传 id、后端按这列查归属）
#   owner_col   归属人属性名（ACL 隐式档判定列）
#   actions     全部动作码（归属人/ADMIN 得到全集，也是授权弹窗的可选动作）
#   scope       数据范围档动作（同部门可见 ≠ 可改）
#   groups      「操作范围」的分组：每个资源自己写组名、组说明与组内动作码
#               （授权弹窗按这一份渲染，前端不再猜分类；一个动作只能属于一组，
#               否则同一行里会出现两个勾选框绑同一个码，勾一个另一个也亮）
# =====================================================================================
# LABELS / groups 与页面按钮的对应约定（改这里必须同时看这条）
# 授权弹窗的「操作范围」按 spec["groups"] 分组渲染（每个资源各写各的组），所以：
#   - 一个动作只能属于一个组：同一个码出现在两组，弹窗里就是两个勾选框绑同一个动作，
#     勾一个另一个也亮（groups 必须恰好覆盖 actions，不多不少，静态用例当场卡这一点）；
#   - 组里有同名按钮的动作（如知识库的 编辑/上传/删除/授权），LABELS 必须与按钮文案逐字
#     相同 —— 不一致就会出现「勾了上传，页面上找不到上传按钮」；
#   - 页面上没按钮的真鉴权动作（view 决定能不能看见、use 决定能不能被检索/被引用）单独
#     归一组并写清 hint，不能因为没按钮就删码，删了跨部门就永远授不进来看库、参与检索。
# 页面没有入口也没有鉴权点的死码不要留在这里（会多出一个授了也没用的勾选项）。
# =====================================================================================
RESOURCE_SPECS: dict[str, dict[str, Any]] = {
    "knowledge_base": {
        "name": "知识库",
        "model": "service.service_rag.models.kb_entity.KnowledgeBase",
        "id_col": "kb_id", "owner_col": "created_by",
        # 三组：知识库本身的操作（页面上那四个同名按钮）/ 库内文件 / 引用
        "actions": [ACTION_VIEW, ACTION_EDIT, ACTION_UPLOAD, ACTION_DELETE, ACTION_SHARE,
                    ACTION_PREVIEW, ACTION_CONTENT, ACTION_CHUNK, ACTION_REPARSE,
                    ACTION_GRAPH, ACTION_DOC_DELETE, ACTION_USE, ACTION_EVAL],
        "scope": [ACTION_VIEW, ACTION_USE],
        "groups": [
            {"key": "kb", "name": "知识库操作",
             "hint": "只影响这一个知识库本身：看得见、改得动、能往里传文件、能删能转授",
             "actions": [ACTION_VIEW, ACTION_EDIT, ACTION_UPLOAD,
                         ACTION_DELETE, ACTION_SHARE]},
            {"key": "file", "name": "库内文件",
             "hint": "勾一次对该库当前与以后所有文档生效（文档行的操作按钮按这一组动态显隐）；"
                     "图片/音视频库没有解析产物，那几项不适用",
             "actions": [ACTION_PREVIEW, ACTION_CONTENT, ACTION_CHUNK,
                         ACTION_REPARSE, ACTION_GRAPH, ACTION_DOC_DELETE]},
            {"key": "ref", "name": "知识库引用",
             "hint": "页面上没有按钮：决定这个库能不能被检索命中、被智能体挂上引用",
             "actions": [ACTION_USE]},
            {"key": "eval", "name": "知识评测",
             "hint": "决定是否能在「知识库-知识评测」页看到这个库并对它跑评测"
                     "（同部门不自动放开，必须显式授权）",
             "actions": [ACTION_EVAL]},
        ],
    },
    "document": {
        "name": "文档",
        "model": "service.service_rag.models.kb_entity.Document",
        "id_col": "doc_id", "owner_col": "created_by",
        # 文档页面按钮五项：预览原文件(preview)、切片管理(chunk)、构建向量(reparse)、
        # 构建图谱(graph)、删除文档(delete)，view 属范围组（查看解析后文档就是看详情）。
        # 不放 export：文档没有「下载」入口，原件对外可读走 preview 签地址。
        # chunk/preview/reparse/graph 都是解析链路上的后置动作，只有能改库配置（edit）
        # 或归属人才该拿到，故都不放 scope（数据范围档只给 view）
        # 整库放行通常走知识库那一行的「库内文件」组，这一份清单是逐篇例外授权用的
        "actions": [ACTION_VIEW, ACTION_EDIT, ACTION_PREVIEW, ACTION_CHUNK,
                    ACTION_REPARSE, ACTION_GRAPH, ACTION_DELETE, ACTION_SHARE],
        "scope": [ACTION_VIEW],
        "groups": [
            {"key": "read", "name": "看这一篇",
             "hint": "只读：看解析出来的内容、看原件，不含任何修改",
             "actions": [ACTION_VIEW, ACTION_PREVIEW]},
            {"key": "maintain", "name": "解析与维护",
             "hint": "改切片、重跑向量与图谱都属于「动这篇的内容」",
             "actions": [ACTION_CHUNK, ACTION_REPARSE, ACTION_GRAPH, ACTION_EDIT]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
    "workflow": {
        "name": "工作流",
        "model": "service.service_workflow.models.workflow_entity.Workflow",
        "id_col": "id", "owner_col": "created_by",
        "actions": [ACTION_VIEW, ACTION_USE, ACTION_EDIT, ACTION_CHAT, ACTION_COPY,
                    ACTION_APIKEY, ACTION_TEMPLATE, ACTION_HISTORY, ACTION_SHARE,
                    ACTION_DELETE],
        "scope": [ACTION_VIEW, ACTION_USE],
        "groups": [
            {"key": "read", "name": "查看与引用",
             "hint": "看得到这条工作流、能让节点/接口真的调起它",
             "actions": [ACTION_VIEW, ACTION_USE]},
            {"key": "design", "name": "编排与复制",
             "hint": "改画布、拿它做副本、存成模板，动的都是定义不是运行结果",
             "actions": [ACTION_EDIT, ACTION_COPY, ACTION_TEMPLATE]},
            {"key": "run", "name": "运行与对外调用",
             "hint": "会话、看执行记录、维护对外 API Key，烧的是真实模型调用",
             "actions": [ACTION_CHAT, ACTION_HISTORY, ACTION_APIKEY]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
    "workflow_template": {
        "name": "工作流模板",
        "model": "service.service_workflow.models.workflow_entity.WorkflowTemplate",
        "id_col": "id", "owner_col": "created_by",
        "actions": [ACTION_VIEW, ACTION_USE, ACTION_COPY, ACTION_EXPORT, ACTION_EDIT,
                    ACTION_DELETE, ACTION_SHARE],
        "scope": [ACTION_VIEW, ACTION_USE],
        "groups": [
            {"key": "read", "name": "查看与取用",
             "hint": "看得见模板、能拿它起一条自己的工作流（复制）",
             "actions": [ACTION_VIEW, ACTION_USE, ACTION_COPY]},
            {"key": "maintain", "name": "模板维护",
             "hint": "改模板内容、导出定义文件到别的环境",
             "actions": [ACTION_EDIT, ACTION_EXPORT]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
    "tool": {
        "name": "工具",
        "model": "service.service_workflow.models.agent_entity.Tool",
        "id_col": "id", "owner_col": "created_by",
        "actions": [ACTION_VIEW, ACTION_USE, ACTION_TEST, ACTION_EDIT, ACTION_DELETE,
                    ACTION_SHARE],
        "scope": [ACTION_VIEW, ACTION_USE, ACTION_TEST],
        "groups": [
            {"key": "read", "name": "查看与引用",
             "hint": "看得到这个工具、能让智能体节点挂上它（测试运行同部门自动可用）",
             "actions": [ACTION_VIEW, ACTION_USE, ACTION_TEST]},
            {"key": "maintain", "name": "工具维护",
             "hint": "改参数定义与调用配置，改完所有引用它的节点都跟着变",
             "actions": [ACTION_EDIT]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
    "skill": {
        "name": "技能",
        "model": "service.service_workflow.models.agent_entity.Skill",
        "id_col": "id", "owner_col": "created_by",
        # 启停/重命名/替换 zip 都是改这一条技能，各自一档动作码（前端按钮按码显隐）
        "actions": [ACTION_VIEW, ACTION_USE, ACTION_EDIT, ACTION_RENAME, ACTION_REPLACE,
                    ACTION_EXPORT, ACTION_DELETE, ACTION_SHARE],
        "scope": [ACTION_VIEW, ACTION_USE],
        "groups": [
            {"key": "read", "name": "查看与引用",
             "hint": "看得到这条技能、能让智能体挂上它",
             "actions": [ACTION_VIEW, ACTION_USE]},
            {"key": "maintain", "name": "技能内容",
             "hint": "启停与编辑同码、改名、整包替换 zip、下载定义",
             "actions": [ACTION_EDIT, ACTION_RENAME, ACTION_REPLACE, ACTION_EXPORT]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
    "mcp": {
        "name": "MCP 连接",
        "model": "service.service_workflow.models.agent_entity.McpServer",
        "id_col": "id", "owner_col": "created_by",
        "actions": [ACTION_VIEW, ACTION_USE, ACTION_TEST, ACTION_TOOLS, ACTION_EDIT,
                    ACTION_DELETE, ACTION_SHARE],
        "scope": [ACTION_VIEW, ACTION_USE, ACTION_TEST],
        "groups": [
            {"key": "read", "name": "查看与引用",
             "hint": "看得到这个连接、能让节点挂上它（测试与列工具清单同部门自动可用）",
             "actions": [ACTION_VIEW, ACTION_USE, ACTION_TEST, ACTION_TOOLS]},
            {"key": "maintain", "name": "连接维护",
             "hint": "改地址/鉴权这类连接配置，改完正在用它的所有节点都受影响",
             "actions": [ACTION_EDIT]},
            {"key": "manage", "name": "销毁与转授",
             "hint": "删除不可逆；「授权」只有归属人或管理员能转授出去",
             "actions": [ACTION_DELETE, ACTION_SHARE]},
        ],
    },
}

# 资源编码 → 归属表名（授权弹窗、审计与排查用；ACL 表只有 resource_code，不落表名）
RESOURCE_TABLES: dict[str, str] = {
    code: str(spec["model"].rsplit(".", 1)[-1]) for code, spec in RESOURCE_SPECS.items()
}

# 前端展示的动作名（授权弹窗勾选框、403 文案里的动作）
ACTION_LABELS = {
    ACTION_VIEW: "查看", ACTION_USE: "使用", ACTION_EDIT: "编辑", ACTION_DELETE: "删除",
    ACTION_SHARE: "授权", ACTION_CHAT: "对话", ACTION_COPY: "复制", ACTION_APIKEY: "API Key",
    ACTION_TEMPLATE: "存为模板", ACTION_HISTORY: "执行记录", ACTION_EXPORT: "下载",
    ACTION_TEST: "测试运行", ACTION_TOOLS: "工具列表", ACTION_RENAME: "重命名",
    ACTION_REPLACE: "替换文件", ACTION_UPLOAD: "上传", ACTION_REPARSE: "构建向量",
    ACTION_GRAPH: "构建图谱", ACTION_PREVIEW: "预览原文件", ACTION_CHUNK: "切片管理",
    ACTION_CONTENT: "查看解析后内容", ACTION_DOC_DELETE: "删除知识",
    ACTION_EVAL: "评测",
}

# 知识库「库内文件」组的码 → 文档侧的等价码。文档级鉴权在自身 ACL 判不过时按这一份
# 回看父库那一行（见 ensure_action 的 parent 参数），列表侧的可见集合走同一个口径。
# 只有这六个动作能被父库授权支撑：知识库的 view/edit/upload/delete/share/use
# 绝不能顺手兜到文档上，否则「能改库配置」就等于「能删别人在库里的每一篇文档」。
KB_FILE_TO_DOC: dict[str, str] = {
    ACTION_PREVIEW: ACTION_PREVIEW,
    ACTION_CONTENT: ACTION_VIEW,
    ACTION_CHUNK: ACTION_CHUNK,
    ACTION_REPARSE: ACTION_REPARSE,
    ACTION_GRAPH: ACTION_GRAPH,
    ACTION_DOC_DELETE: ACTION_DELETE,
}
# 反查表：文档侧动作 → 该看父库的哪个动作码
DOC_TO_KB_FILE: dict[str, str] = {v: k for k, v in KB_FILE_TO_DOC.items()}


# ==================== 内部工具 ====================
def spec_of(resource_code: str) -> dict[str, Any]:
    """取资源规格；未登记的 resource_code 直接报错（写错一个字符 = 整条资源永不鉴权）。"""
    spec = RESOURCE_SPECS.get(resource_code)
    if spec is None:
        raise ValueError(f"未登记的资源类型: {resource_code}（已支持 {list(RESOURCE_SPECS)}）")
    return spec


def check_actions(resource_code: str, actions: Sequence[str]) -> list[str]:
    """校验动作码合法性并去重保序（授权写入前的唯一入口，防 tb_resource_acl 长出野动作）。"""
    allowed = spec_of(resource_code)["actions"]
    out: list[str] = []
    for a in actions or []:
        act = str(a or "").strip()
        if not act:
            continue
        if act not in allowed:
            raise ValueError(f"资源 {resource_code} 没有动作 {act}（可选：{allowed}）")
        if act not in out:
            out.append(act)
    if not out:
        raise ValueError(f"资源 {resource_code} 至少需要一个动作")
    return out


def _model_of(resource_code: str):
    """按点路径延迟导入 ORM 类：common 层不能反向硬依赖 service 层。"""
    path = spec_of(resource_code)["model"]
    module_name, cls_name = path.rsplit(".", 1)
    import importlib

    module = importlib.import_module(module_name)
    return getattr(module, cls_name)


def _int_list(values: Any) -> list[int]:
    """载荷里的 id 集合 → 干净 int 列表（脏值逐项丢掉，不让一个 None 打断整条鉴权）。"""
    out: list[int] = []
    for v in values or []:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv not in out:
            out.append(iv)
    return out


def grantee_conds(login_user: dict) -> list:
    """把当前用户展开成「可能命中 ACL 行的主体条件」集合。

    登录载荷里 role_ids / group_ids / dept_ancestor_ids 都是登录时预算好的，
    这里零查询；载荷缺字段（旧登录态）时对应一档自然为空，不影响其它档命中。

    部门一档的两个分支不能合并：
    - dept_include_sub=1 授权给上级部门时我在它的子孙里 → 拿祖先链（含自己）命中；
    - dept_include_sub=0 只授给某一个部门本身 → 必须等于我的直属部门。
    载荷没有祖先链时，含下级那一支退化成「只命中我的直属部门」（宁可少放行不可多放行）。
    """
    user_id = int(login_user.get("user_id") or 0)
    dept_id = int(login_user.get("dept_id") or 0)
    ancestors = _int_list(login_user.get("dept_ancestor_ids"))
    # 载荷没预算祖先链（旧登录态）时，含下级那一支只能按本人部门命中
    sub_chain = ancestors if ancestors else ([dept_id] if dept_id else [])
    conds = [
        # 1-用户本人
        (ResourceAcl.grantee_type == GRANTEE_USER) & (ResourceAcl.grantee_id == user_id),
        # 2-所持角色
        (ResourceAcl.grantee_type == GRANTEE_ROLE) & (
            ResourceAcl.grantee_id.in_(_int_list(login_user.get("role_ids")))),
        # 4-所在用户组
        (ResourceAcl.grantee_type == GRANTEE_GROUP) & (
            ResourceAcl.grantee_id.in_(_int_list(login_user.get("group_ids")))),
        # 5-全员
        (ResourceAcl.grantee_type == GRANTEE_ALL),
    ]
    if sub_chain:
        # 3-部门：含下级的授权按祖先链命中，不含下级的只按本人部门精确命中
        conds.append((ResourceAcl.grantee_type == GRANTEE_DEPT) & (
            ((ResourceAcl.dept_include_sub == 1)
             & (ResourceAcl.grantee_id.in_(sub_chain)))
            | ((ResourceAcl.dept_include_sub == 0)
               & (ResourceAcl.grantee_id == (dept_id or -1)))))
    return conds


def _acl_base(login_user: dict, resource_code: str, action: str) -> Select:
    """某资源类型 + 某动作 + 当前用户主体 + 未过期 的授权行查询骨架（不含 id 过滤）。"""
    now = datetime.now()
    return (select(ResourceAcl.resource_id)
            .where(ResourceAcl.resource_code == resource_code,
                   ResourceAcl.is_deleted == 0,
                   ResourceAcl.action == action,
                   or_(ResourceAcl.expire_time.is_(None), ResourceAcl.expire_time > now),
                   or_(*grantee_conds(login_user))))


# =====================================================================================
# 一、列表下推
# =====================================================================================
def build_visible_cond(login_user: dict, resource_code: str, id_col, owner_col, *,
                       action: str = ACTION_VIEW, parent: Optional[tuple] = None):
    """生成「当前用户对该资源类型可见（且持有该动作）」的 SQL 条件；返回 None 表示不限。

    :param id_col: 资源主键列（如 KnowledgeBase.kb_id）
    :param owner_col: 归属人列（如 KnowledgeBase.created_by）
    :param action: 需要持有的动作，列表默认 view；下拉/可执行清单传 use
    :param parent: 父实例兜底 (父资源码, 本表上的父 id 列, 父侧动作码)，文档用它：
                   在知识库上勾了「库内文件」那组的人，整库的文档行都得出现在列表里，
                   否则按钮判得过、行却查不出来，等于授了个寂寞。
    口径 = 归属人 ∪ 数据范围内的他人创建 ∪ 持有该动作的显式授权（∪ 父实例的对应授权，ADMIN 不限）。
    """
    if is_admin(login_user):
        return None
    spec = spec_of(resource_code)
    if action not in spec["actions"]:
        # 传了该资源根本不存在的动作：列表应当空掉而不是放行，否则会看见不该看见的行
        return id_col == -1
    user_id = int(login_user.get("user_id") or 0)
    conds = [owner_col == user_id, id_col.in_(_acl_base(login_user, resource_code, action))]
    if parent is not None:
        p_code, p_id_col, p_action = parent
        if p_action in spec_of(p_code)["actions"]:
            conds.append(p_id_col.in_(_acl_base(login_user, p_code, p_action)))
    scope_dept_ids = get_login_scope(login_user)
    if scope_dept_ids:
        from common.common_entity.user_entity import User  # 延迟导入避免循环

        conds.append(owner_col.in_(
            select(User.user_id).where(User.dept_id.in_(scope_dept_ids))))
    return or_(*conds)


# =====================================================================================
# 二、行内按钮下发
# =====================================================================================
async def decorate_actions(login_user: dict, resource_code: str, items: list[dict], *,
                           id_key: str = "id", owner_key: str = "created_by",
                           target_key: str = "actions",
                           session: Optional[AsyncSession] = None) -> None:
    """给每行写 `actions: [动作码]`，前端按钮显隐的唯一来源（判定规则不在前端复制一份）。

    两次查询搞定一整页：一次取本页 id 命中的授权行，一次取本页归属人里落在我数据范围内的。
    逐行查库会让 100 行列表打 200 个查询，页一卡就没人愿意加权限。
    """
    if not items:
        return
    spec = spec_of(resource_code)
    all_actions = list(spec["actions"])
    if is_admin(login_user):
        for it in items:
            it[target_key] = all_actions
        return
    user_id = int(login_user.get("user_id") or 0)
    ids = []
    for it in items:
        try:
            rid = int(it.get(id_key))
        except (TypeError, ValueError):
            continue
        if rid not in ids:
            ids.append(rid)

    async with _session_scope(session) as s:
        granted = _granted_map(await _load_acl(login_user, resource_code, ids, s))
        scope_owners = await _scope_owners(login_user, items, owner_key, s)

    scope_actions = list(spec["scope"])
    for it in items:
        try:
            rid = int(it.get(id_key))
        except (TypeError, ValueError):
            it[target_key] = [ACTION_VIEW]
            continue
        owner_id = _as_int(it.get(owner_key))
        if owner_id and owner_id == user_id:
            # 归属人档：自己建的东西全权（含 share，否则授权转不出去，闭环就断了）
            it[target_key] = all_actions
            continue
        acts = set(granted.get(rid) or ())
        if owner_id in scope_owners:
            acts |= set(scope_actions)
        # 一条动作都没命中时至少留 view：列表既然下发了这一行，就该能点开看是谁的
        it[target_key] = [a for a in all_actions if a in acts] or [ACTION_VIEW]


def _granted_map(rows: list[tuple[int, str]]) -> dict[int, set[str]]:
    """(resource_id, action) 行 → {resource_id: {动作集合}}。"""
    out: dict[int, set[str]] = {}
    for rid, act in rows:
        out.setdefault(int(rid), set()).add(act)
    return out


async def _load_acl(login_user: dict, resource_code: str, ids: list[int],
                    session: AsyncSession) -> list[tuple[int, str]]:
    """取本页资源 id 上、当前用户主体命中、未过期的授权行。"""
    if not ids:
        return []
    now = datetime.now()
    rows = await session.execute(
        select(ResourceAcl.resource_id, ResourceAcl.action).where(
            ResourceAcl.resource_code == resource_code,
            ResourceAcl.is_deleted == 0,
            ResourceAcl.resource_id.in_(ids),
            or_(ResourceAcl.expire_time.is_(None), ResourceAcl.expire_time > now),
            or_(*grantee_conds(login_user))))
    return [(int(r[0]), r[1]) for r in rows.all()]


async def _scope_owners(login_user: dict, items: list[dict], owner_key: str,
                        session: AsyncSession) -> set[int]:
    """本页归属人里落在我数据范围部门集合内的那批（一次批量查，不按行查）。"""
    scope_dept_ids = get_login_scope(login_user)
    if not scope_dept_ids:
        return set()
    owners = {_as_int(it.get(owner_key)) for it in items} - {0}
    if not owners:
        return set()
    from common.common_entity.user_entity import User  # 延迟导入避免循环

    rows = await session.execute(
        select(User.user_id).where(User.user_id.in_(sorted(owners)),
                                   User.dept_id.in_(scope_dept_ids)))
    # 单列查询走 scalars() 拿到的已经是 int（不能再取 [0]）；要多列才用 rows.all()
    return {int(r) for r in rows.scalars().all()}


# =====================================================================================
# 三、单点判定
# =====================================================================================
async def ensure_action(login_user: dict, resource_code: str, resource_id: Any,
                        action: str, *, owner_id: Optional[int] = None,
                        parent: Optional[tuple[str, Any]] = None,
                        session: Optional[AsyncSession] = None) -> None:
    """卡一个动作：不通过就抛 403（文案带上归属人，让用户知道该找谁申请）。

    三档并集在这里逐档短路：ADMIN → 归属人 → 显式授权（含父实例兜底）→ 数据范围。
    把 403 说成 404 会让用户去翻回收站，所以调用方自己确认过资源存在的，都把 owner_id 传进来
    （传了省一次回表；不传则按 spec 的表回查一次归属，杜绝「前端不传就绕过」的口子）。
    :param parent: (父资源码, 父资源 id)；只在自身 ACL 判不过时多看一眼父实例上同一件事的
                   授权，且只认 KB_FILE_TO_DOC 里那几个库内文件动作（父库的 edit/use 不会兜过来）。
    """
    if is_admin(login_user):
        return
    spec = spec_of(resource_code)
    if action not in spec["actions"]:
        raise UnauthorizedException(_denied(action, spec, None))
    rid = _as_int(resource_id)
    uid = int(login_user.get("user_id") or 0)
    if owner_id is None:
        owner_id = await load_owner(resource_code, rid, session=session)
    if owner_id and owner_id == uid:
        return
    async with _session_scope(session) as s:
        granted = await _has_acl(login_user, resource_code, rid, action, s)
        if not granted and parent is not None:
            # 库内文件族可以由知识库那一行支撑（在库上勾一次，整库生效）
            kb_act = DOC_TO_KB_FILE.get(action)
            granted = bool(kb_act) and await _has_acl(login_user, str(parent[0]),
                                                      _as_int(parent[1]), kb_act, s)
        if granted:
            return
        in_scope = await _owner_in_scope(login_user, owner_id, s)
    if in_scope and action in spec["scope"]:
        return
    raise UnauthorizedException(_denied(action, spec, owner_id))


async def ensure_any(login_user: dict, resource_code: str, resource_id: Any,
                     actions: Iterable[str], *, owner_id: Optional[int] = None,
                     session: Optional[AsyncSession] = None) -> None:
    """卡「任一动作」：一件事由两档动作支撑时的入口（例：调 MCP 工具 = tools 或 use）。

    ADMIN/归属人依旧全权；否则只要命中给定动作集合里的任意一档即放行。
    """
    wanted = [a for a in (actions or []) if a]
    if not wanted:
        raise ValueError("ensure_any 需要至少一个动作")
    if is_admin(login_user):
        return
    spec = spec_of(resource_code)
    rid = _as_int(resource_id)
    uid = int(login_user.get("user_id") or 0)
    if owner_id is None:
        owner_id = await load_owner(resource_code, rid, session=session)
    if owner_id and owner_id == uid:
        return
    async with _session_scope(session) as s:
        hits = await _acl_actions(login_user, resource_code, rid, s)
        if any(a in hits for a in wanted):
            return
        if await _owner_in_scope(login_user, owner_id, s):
            if any(a in spec["scope"] for a in wanted):
                return
    raise UnauthorizedException(_denied("／".join(ACTION_LABELS.get(a, a) for a in wanted),
                                        spec, owner_id))


async def ensure_action_strict(login_user: dict, resource_code: str, resource_id: Any,
                               action: str, *, session: Optional[AsyncSession] = None) -> None:
    """只认归属人与显式授权（不看数据范围）：删除、授权这类不可由「同部门」推断的动作。

    与 ensure_action 的区别只在少了 scope 一档：部门内的人能看能用，但绝不能因为同部门
    就把别人的知识库删掉或把授权转出去。
    """
    if is_admin(login_user):
        return
    spec = spec_of(resource_code)
    rid = _as_int(resource_id)
    uid = int(login_user.get("user_id") or 0)
    owner_id = await load_owner(resource_code, rid, session=session)
    if owner_id and owner_id == uid:
        return
    async with _session_scope(session) as s:
        if await _has_acl(login_user, resource_code, rid, action, s):
            return
    raise UnauthorizedException(_denied(action, spec, owner_id))


async def load_owner(resource_code: str, resource_id: int, *,
                     session: Optional[AsyncSession] = None) -> int:
    """按资源主键回查归属人 user_id；资源不存在返回 0（调用方的存在性校验负责报 404/400）。"""
    spec = spec_of(resource_code)
    model = _model_of(resource_code)
    id_col = getattr(model, spec["id_col"])
    owner_col = getattr(model, spec["owner_col"])
    async with _session_scope(session) as s:
        return _as_int((await s.execute(
            select(owner_col).where(id_col == _as_int(resource_id)))).scalar_one_or_none())


def _brief_title(obj: Any) -> str:
    """从实体上抓一个能看的标题（授权弹窗、报错文案、日志都用它，各资源列名不一致）。"""
    for cand in ("kb_name", "doc_name", "workflow_name", "skill_name", "tool_name",
                 "server_name", "template_name", "name", "title"):
        value = getattr(obj, cand, None)
        if value:
            return str(value)[:255]
    return ""


async def load_resource_brief(resource_code: str, resource_id: Any, *,
                              session: Optional[AsyncSession] = None) -> tuple[dict[str, Any], int]:
    """回查一条资源的存在性与归属：返回 (brief, owner_id)；不存在或已软删抛 ValueError。

    与 load_owner 的分工：load_owner 查不到返回 0（鉴权链里「归属人未知」不该变成报错，
    后面还有 ACL 与数据范围两档要判），而本函数是给「先确认资源还在」的入口用的——
    API Key 指向的工作流被删了必须直接报错，不能因为 owner 取不到就默认放行。

    brief 给调用方回显与二次判定用：归属知识库（文档场景）、标题、id、归属人。
    """
    spec = spec_of(resource_code)
    model = _model_of(resource_code)
    id_col = getattr(model, spec["id_col"])
    rid = _as_int(resource_id)
    async with _session_scope(session) as s:
        obj = (await s.execute(select(model).where(id_col == rid))).scalar_one_or_none()
        if obj is None:
            raise ValueError(f"{spec['name']}不存在（id={rid}）")
        if _as_int(getattr(obj, "is_deleted", 0)):
            raise ValueError(f"{spec['name']}已被删除（id={rid}）")
        brief = {"resource_code": resource_code, "resource_name": spec["name"],
                 "resource_id": _as_int(getattr(obj, spec["id_col"], rid)),
                 "owner_id": _as_int(getattr(obj, spec["owner_col"], 0)),
                 "kb_id": _as_int(getattr(obj, "kb_id", 0)),
                 "title": _brief_title(obj)}
    return brief, int(brief["owner_id"])


async def _acl_actions(login_user: dict, resource_code: str, resource_id: int,
                       session: AsyncSession) -> set[str]:
    """该用户在这个资源实例上被显式授了哪些动作（未过期）。"""
    now = datetime.now()
    rows = await session.execute(
        select(ResourceAcl.action).where(
            ResourceAcl.resource_code == resource_code,
            ResourceAcl.resource_id == resource_id,
            ResourceAcl.is_deleted == 0,
            or_(ResourceAcl.expire_time.is_(None), ResourceAcl.expire_time > now),
            or_(*grantee_conds(login_user))))
    return {r for r in rows.scalars().all()}


async def granted_actions(login_user: dict, resource_code: str, resource_id: Any, *,
                          session: Optional[AsyncSession] = None) -> set[str]:
    """某一实例上当前用户被显式授到的动作码（列表页拼行内按钮时用，一次查整库而不是逐行）。

    只给显式授权那一档：归属人/数据范围/ADMIN 都由 decorate_actions 自己判，这里再算一遍
    就会出现两份规则（两处规则早晚分叉就是越权漏洞的起点）。
    """
    async with maybe_session(session) as s:
        return await _acl_actions(login_user, resource_code, _as_int(resource_id), s)


async def _has_acl(login_user: dict, resource_code: str, resource_id: int, action: str,
                   session: AsyncSession) -> bool:
    rows = await session.execute(
        select(ResourceAcl.acl_id).where(
            ResourceAcl.resource_code == resource_code,
            ResourceAcl.resource_id == resource_id,
            ResourceAcl.action == action,
            ResourceAcl.is_deleted == 0,
            or_(ResourceAcl.expire_time.is_(None),
                ResourceAcl.expire_time > datetime.now()),
            or_(*grantee_conds(login_user))).limit(1))
    return rows.first() is not None


async def _owner_in_scope(login_user: dict, owner_id: Optional[int],
                          session: AsyncSession) -> bool:
    """归属人是否落在我的数据范围部门集合内（scope 档的唯一依据）。"""
    owner_id = _as_int(owner_id)
    scope_dept_ids = get_login_scope(login_user)
    if not owner_id or not scope_dept_ids:
        return False
    from common.common_entity.user_entity import User  # 延迟导入避免循环

    rows = await session.execute(
        select(User.user_id).where(User.user_id == owner_id,
                                   User.dept_id.in_(scope_dept_ids)).limit(1))
    return rows.first() is not None


def _denied(action: str, spec: dict, owner_id: Optional[int]) -> str:
    """统一的 403 文案：说明是什么动作、什么资源、该找谁申请。"""
    label = ACTION_LABELS.get(action, action)
    who = f"（可联系归属人 user_id={owner_id} 申请授权）" if owner_id else "（可联系归属人申请授权）"
    return f"无该{spec['name']}的{label}权限{who}"


# =====================================================================================
# 四、会话借用
# =====================================================================================
@asynccontextmanager
async def maybe_session(session: Optional[AsyncSession] = None):
    """有 session 就用调用方的（不提交不关闭），没有就自己开一个短会话。

    业务 service 大多在一个 `async with mysql_client.get_session()` 里调本模块，
    自己再开一个连接会出现同一请求两个事务、可见性对不齐的问题。
    """
    if session is not None:
        yield session
        return
    async with mysql_client.get_session() as s:
        yield s


_session_scope = maybe_session


def _as_int(value: Any) -> int:
    """宽松取整：None/脏值一律 0（归属人 0 表示历史存量行，不当成当前用户）。"""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def accessible_ids(login_user: dict, resource_code: str, ids: Sequence[int], *,
                         action: str = ACTION_VIEW,
                         session: Optional[AsyncSession] = None) -> list[int]:
    """在给定 id 集合里筛出「该用户持有该动作」的子集（开放 API 批量入参的前置校验）。

    与 build_visible_cond 的差别是这里不查业务表：调用方已经知道 id 了（比如请求体里传来的
    kb_ids），只需按 ACL 收口，省一次 join。ADMIN 直接原样返回。
    """
    wanted = [i for i in (_as_int(x) for x in ids or []) if i]
    if not wanted:
        return []
    if is_admin(login_user):
        return wanted
    spec = spec_of(resource_code)
    if action not in spec["actions"]:
        return []
    out: list[int] = []
    async with maybe_session(session) as s:
        owner_map = await _owners(resource_code, wanted, s)
        for rid in wanted:
            owner_id = owner_map.get(rid, 0)
            if owner_id and owner_id == int(login_user.get("user_id") or 0):
                out.append(rid)
                continue
            if await _has_acl(login_user, resource_code, rid, action, s):
                out.append(rid)
                continue
            if action in spec["scope"] and await _owner_in_scope(login_user, owner_id, s):
                out.append(rid)
    return out


async def _owners(resource_code: str, ids: Sequence[int],
                  session: AsyncSession) -> dict[int, int]:
    """批量取归属人（{id: created_by}）。"""
    spec = spec_of(resource_code)
    model = _model_of(resource_code)
    id_col = getattr(model, spec["id_col"])
    owner_col = getattr(model, spec["owner_col"])
    rows = await session.execute(
        select(id_col, owner_col).where(id_col.in_(list(ids))))
    return {_as_int(r[0]): _as_int(r[1]) for r in rows.all()}


__all__ = [
    "ACTION_VIEW", "ACTION_USE", "ACTION_EDIT", "ACTION_DELETE", "ACTION_SHARE",
    "ACTION_CHAT", "ACTION_COPY", "ACTION_APIKEY", "ACTION_TEMPLATE", "ACTION_HISTORY",
    "ACTION_EXPORT", "ACTION_TEST", "ACTION_TOOLS", "ACTION_RENAME", "ACTION_REPLACE",
    "ACTION_TOGGLE", "ACTION_UPLOAD", "ACTION_REPARSE", "ACTION_GRAPH", "ACTION_PREVIEW",
    "ACTION_CHUNK", "ACTION_CONTENT", "ACTION_DOC_DELETE", "ACTION_EVAL", "ACTION_LABELS",
    "GRANTEE_USER", "GRANTEE_ROLE", "GRANTEE_DEPT", "GRANTEE_GROUP", "GRANTEE_ALL",
    "GRANTEE_LABELS", "RESOURCE_SPECS", "RESOURCE_TABLES",
    "KB_FILE_TO_DOC", "DOC_TO_KB_FILE",
    "spec_of", "check_actions", "grantee_conds", "granted_actions",
    "build_visible_cond", "decorate_actions",
    "ensure_action", "ensure_any", "ensure_action_strict", "accessible_ids", "load_owner",
    "load_resource_brief",
    "maybe_session",
]
