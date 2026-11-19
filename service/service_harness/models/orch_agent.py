# -*- coding: utf-8 -*-
"""智能体编排 ORM 实体（四模式各一张表，对齐 sql/v2_init.sql PART 11）。

设计要点（口径同 harness_entity / agent_entity，只列名在本模块单点维护）：
- 归属与审计四列齐备：created_by / create_time / updated_by / update_time
  （用户明确要求「数据表要包含创建人，创建时间，修改人，修改时间」）。
- is_deleted 软删：删除动作走 ACL 严格口径（ensure_action_strict），不物删，
  列表/详情/鉴权一律按 is_deleted=0 过滤（与 document/knowledge_base 同款）。
- id 用 Integer：与既有实体一致，BIGINT 主键在 SQLite 变体下无法自增（集成测试用）。
- 挂载与配置一律 JSON 列：
  * 单智能体：mounts 直接存 {skillIds/toolIds/mcpIds/kbIds/modelId}；
  * 团队/目标/SOP：成员配置从 tb_orch_agent 克隆后单独落在各自 config_json，
    不回写单智能体表（克隆语义，见各 service）。
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import JSON, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from common.common_entity.base_entity import Base


class OrchAgent(Base):
    """单智能体配置表（模式一：一个可复用的 Agent 定义）"""
    __tablename__ = "tb_orch_agent"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="智能体名称")
    purpose: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用途")
    # 系统提示词：运行时经参数化 build_agent 覆盖默认段（深度探索那份不动）
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="系统提示词")
    # 挂载快照 {skillIds:[], toolIds:[], mcpIds:[], kbIds:[], modelId:int}
    mounts: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="挂载（技能/工具/MCP/知识库/模型）")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="修改人用户ID")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="0-正常 1-已删除")


class OrchTeam(Base):
    """团队模式配置表（模式二：队长 + 成员，运行时 TeamPipeline）

    主智能体单独一个 AgentState，子 agent 各自按 主session_id:index 作为各自 session；
    workspace 共用（用户单一沙箱工作区，运行侧天然共享，不在本表体现）。
    """
    __tablename__ = "tb_orch_team"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="团队名称")
    purpose: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用途")
    # 主智能体（队长）指向某条单智能体克隆体（tb_orch_agent.id）
    leader_agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="主智能体（队长）单智能体id")
    # 成员列表 [{agent_id, index, description}]：agent_id 指向克隆来源的单智能体，index 用于派生子 state 会话键
    members: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="成员智能体列表")
    # config_json 单独维护完整配置快照（成员克隆体配置 + 主标识），不回写单智能体表
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="团队完整配置快照")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="修改人用户ID")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="0-正常 1-已删除")


class OrchGoal(Base):
    """目标模式配置表（模式三：执行者 + 验证者，运行时 GoalPipeline，最多两个智能体）

    executor 与 verifier 共用一份 AgentState（顺序执行语义下成立）。workspace 共用。
    """
    __tablename__ = "tb_orch_goal"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="目标名称")
    purpose: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用途")
    executor_agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="执行者单智能体id")
    verifier_agent_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="验证者单智能体id")
    # config_json 单独维护执行者/验证者的完整配置快照与标识，不回写单智能体表
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="目标完整配置快照")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="修改人用户ID")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="0-正常 1-已删除")


class OrchSop(Base):
    """SOP 模式配置表（模式四：多里程碑流程，运行时 SOPEngine）

    流程中所有节点共用一份 AgentState（顺序执行语义下成立）。workspace 共用。
    每个节点：名称 / 流程定义 / 执行者 / 检查者 / 最大驳回次数（默认 3）。
    """
    __tablename__ = "tb_orch_sop"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="SOP 名称")
    purpose: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用途描述")
    # 流程节点数组（顺序即数组序）：
    # [{name, description, executor_agent_id, verifier_agent_id, max_attempts=3,
    #   executor_config, verifier_config}]
    steps: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="流程节点定义（按序）")
    # 流程顺序信息（单独维护；当前与 steps 数组序一致，留作显式快照以便重排回滚）
    flow_order: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="流程顺序")
    # config_json 单独维护 SOP 创建页全部信息 + 各节点执行者/检查者配置快照与标识
    config_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="SOP 完整配置快照")
    status: Mapped[int] = mapped_column(Integer, nullable=False, default=1, comment="0-停用 1-启用")
    created_by: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="创建人用户ID")
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="修改人用户ID")
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(),
                                                  onupdate=func.now())
    is_deleted: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="0-正常 1-已删除")
