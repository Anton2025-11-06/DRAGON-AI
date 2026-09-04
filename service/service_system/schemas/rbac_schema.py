from typing import Optional

from pydantic import BaseModel, Field


# ==================== 角色 ====================
class RoleCreateRequest(BaseModel):
    role_code: str = Field(..., min_length=2, max_length=64, description="角色编码，如 DEPT_MANAGER")
    role_name: str = Field(..., min_length=2, max_length=64, description="角色名称")
    description: Optional[str] = Field(None, max_length=255, description="描述")
    # 数据权限范围：1-全部 2-本部门及以下 3-本部门 4-仅本人 5-自定义部门
    data_scope: int = Field(4, ge=1, le=5, description="数据权限范围")
    dept_ids: Optional[str] = Field(None, max_length=1024, description="自定义数据权限部门ID，逗号分隔")


class RoleUpdateRequest(BaseModel):
    role_name: Optional[str] = Field(None, min_length=2, max_length=64, description="角色名称")
    description: Optional[str] = Field(None, max_length=255, description="描述")
    data_scope: Optional[int] = Field(None, ge=1, le=5, description="数据权限范围")
    dept_ids: Optional[str] = Field(None, max_length=1024, description="自定义数据权限部门ID，逗号分隔")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态：0-禁用 1-启用")


class RoleMenusRequest(BaseModel):
    """为角色分配菜单/按钮权限"""
    menu_ids: list[int] = Field(..., description="菜单ID列表（含按钮权限点）")


# ==================== 菜单 ====================
class MenuCreateRequest(BaseModel):
    parent_id: int = Field(0, ge=0, description="父级菜单ID，0为根")
    menu_name: str = Field(..., min_length=1, max_length=64, description="菜单名称")
    menu_type: int = Field(2, ge=1, le=3, description="类型：1-目录 2-菜单 3-按钮")
    path: Optional[str] = Field(None, max_length=255, description="前端路由路径")
    component: Optional[str] = Field(None, max_length=255, description="前端组件路径")
    perm: Optional[str] = Field(None, max_length=128, description="权限标识，如 system:user:add")
    icon: Optional[str] = Field(None, max_length=64, description="图标")
    sort: int = Field(0, description="排序号")
    visible: int = Field(1, ge=0, le=1, description="是否显示：0-隐藏 1-显示")
    status: int = Field(1, ge=0, le=1, description="状态：0-禁用 1-启用")


class MenuUpdateRequest(BaseModel):
    parent_id: Optional[int] = Field(None, ge=0, description="父级菜单ID")
    menu_name: Optional[str] = Field(None, min_length=1, max_length=64, description="菜单名称")
    menu_type: Optional[int] = Field(None, ge=1, le=3, description="类型：1-目录 2-菜单 3-按钮")
    path: Optional[str] = Field(None, max_length=255, description="前端路由路径")
    component: Optional[str] = Field(None, max_length=255, description="前端组件路径")
    perm: Optional[str] = Field(None, max_length=128, description="权限标识")
    icon: Optional[str] = Field(None, max_length=64, description="图标")
    sort: Optional[int] = Field(None, description="排序号")
    visible: Optional[int] = Field(None, ge=0, le=1, description="是否显示")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态")


# ==================== 部门 ====================
class DeptCreateRequest(BaseModel):
    parent_id: int = Field(0, ge=0, description="父部门ID，0为根")
    dept_name: str = Field(..., min_length=1, max_length=64, description="部门名称")
    sort: int = Field(0, description="排序号")
    status: int = Field(1, ge=0, le=1, description="状态：0-禁用 1-启用")


class DeptUpdateRequest(BaseModel):
    parent_id: Optional[int] = Field(None, ge=0, description="父部门ID")
    dept_name: Optional[str] = Field(None, min_length=1, max_length=64, description="部门名称")
    sort: Optional[int] = Field(None, description="排序号")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态")


# ==================== 操作日志查询 ====================
class OperateLogQuery(BaseModel):
    """操作日志筛选条件"""
    username: Optional[str] = Field(None, description="操作人模糊查询")
    module: Optional[str] = Field(None, description="模块")
    method: Optional[str] = Field(None, description="请求方法")
    status: Optional[int] = Field(None, description="状态码")
    trace_id: Optional[str] = Field(None, description="链路追踪ID")
    start_time: Optional[str] = Field(None, description="开始时间 YYYY-MM-DD HH:mm:ss")
    end_time: Optional[str] = Field(None, description="结束时间 YYYY-MM-DD HH:mm:ss")