from typing import Optional

from pydantic import BaseModel, Field

# ==================== 用户组（tb_user_group / tb_user_group_member）====================
# 用户组本身是组织对象，只走功能权限（system:usergroup:*）；
# 它作为「资源 ACL 的授权主体」时的鉴权发生在 acl_router，不在这里。


class UserGroupCreateRequest(BaseModel):
    group_name: str = Field(..., min_length=1, max_length=64, description="用户组名称（全局唯一）")
    description: Optional[str] = Field(None, max_length=255, description="描述")
    status: int = Field(1, ge=0, le=1, description="状态：0-停用 1-启用")


class UserGroupUpdateRequest(BaseModel):
    group_name: Optional[str] = Field(None, min_length=1, max_length=64, description="用户组名称")
    description: Optional[str] = Field(None, max_length=255, description="描述")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态：0-停用 1-启用")


class UserGroupMemberRequest(BaseModel):
    """成员整表提交：语义与角色分配菜单一致（先清后插），空数组即清空该组成员"""
    user_ids: list[int] = Field(default_factory=list, description="成员用户ID列表")
