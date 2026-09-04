from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator
from typing import Optional


def _empty_str_to_none(cls, v):
    """空字符串统一转为 None：避免 '' 无法通过 EmailStr 等校验导致参数校验失败"""
    if isinstance(v, str) and not v.strip():
        return None
    return v


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=128, description="用户名")
    password: str = Field(..., min_length=6, max_length=1000, description="密码")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, max_length=32, description="手机号")
    real_name: Optional[str] = Field(None, max_length=64, description="真实姓名")
    # 所属部门ID（数据权限组织单元），不传默认 0
    dept_id: Optional[int] = Field(None, ge=0, description="所属部门ID")
    # 初始角色列表（可选，不传则默认 USER 角色）
    role_ids: Optional[list[int]] = Field(None, description="初始分配的角色ID列表")

    @field_validator("email", "phone", "real_name", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(cls, v)


class VerifyRequest(BaseModel):
    user_id: int = Field(description="用户id")
    password: str = Field(..., min_length=6, max_length=1000, description="密码")


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=128, description="用户名")
    password: Optional[str] = Field(None, min_length=6, max_length=1000, description="密码")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, max_length=32, description="手机号")
    real_name: Optional[str] = Field(None, max_length=64, description="真实姓名")
    dept_id: Optional[int] = Field(None, ge=0, description="所属部门ID")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态：0-禁用，1-启用")

    @field_validator("email", "phone", "real_name", mode="before")
    @classmethod
    def _empty_to_none(cls, v):
        return _empty_str_to_none(cls, v)


class ResetPasswordRequest(BaseModel):
    """管理员重置用户密码"""
    password: str = Field(..., min_length=6, max_length=1000, description="新密码")


class ChangeOwnPasswordRequest(BaseModel):
    """修改自己的密码（需校验原密码）"""
    old_password: str = Field(..., min_length=6, max_length=1000, description="原密码")
    new_password: str = Field(..., min_length=6, max_length=1000, description="新密码")


class UserResponse(BaseModel):
    user_id: int
    username: str
    email: Optional[str]
    phone: Optional[str]
    real_name: Optional[str]
    dept_id: Optional[int] = 0
    dept_name: Optional[str] = None
    status: int
    roles: list = []
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    total: int
    items: list[UserResponse]


class AssignRolesRequest(BaseModel):
    """为用户分配角色"""
    role_ids: list[int] = Field(..., description="角色ID列表")