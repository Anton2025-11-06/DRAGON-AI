from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, model_validator
from typing import Optional


class UserCreateRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=128, description="用户名")
    password: str = Field(..., min_length=6, max_length=1000, description="密码")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, max_length=32, description="手机号")
    real_name: Optional[str] = Field(None, max_length=64, description="真实姓名")


class VerifyRequest(BaseModel):
    user_id: int = Field(description="用户id")
    password: str = Field(..., min_length=6, max_length=1000, description="密码")

class Login(BaseModel):
    username: str = Field(..., min_length=3, max_length=128, description="用户名")
    password: str = Field(..., min_length=6, max_length=1000, description="密码")


class RegisterRequest(UserCreateRequest):
    confirm_password: str = Field(..., min_length=6, max_length=1000, description="确认密码")

    @model_validator(mode="after")
    def _check_confirm(self):
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class UserUpdateRequest(BaseModel):
    username: Optional[str] = Field(None, min_length=3, max_length=128, description="用户名")
    password: Optional[str] = Field(None, min_length=6, max_length=1000, description="密码")
    email: Optional[EmailStr] = Field(None, description="邮箱")
    phone: Optional[str] = Field(None, max_length=32, description="手机号")
    real_name: Optional[str] = Field(None, max_length=64, description="真实姓名")
    status: Optional[int] = Field(None, ge=0, le=1, description="状态：0-禁用，1-启用")


class UserResponse(BaseModel):
    user_id: int
    username: str
    email: Optional[str]
    phone: Optional[str]
    real_name: Optional[str]
    status: int
    create_time: datetime
    update_time: datetime

    class Config:
        from_attributes = True


class UserListResponse(BaseModel):
    total: int
    items: list[UserResponse]