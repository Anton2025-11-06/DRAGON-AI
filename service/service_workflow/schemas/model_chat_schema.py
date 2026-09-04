from typing import List, Optional

from pydantic import BaseModel, Field


class ChatSessionCreateReq(BaseModel):
    """新建会话：可携带授权模型快照与偏好"""
    title: Optional[str] = Field(None, max_length=128, description="会话标题（缺省为「新会话」）")
    model_apply_id: int = Field(0, ge=0, description="授权记录 apply_id")
    model_name: Optional[str] = Field(None, max_length=128, description="模型标识")
    reasoning: bool = Field(False, description="深度思考偏好")
    stream: bool = Field(True, description="流式偏好")


class ChatSessionUpdateReq(BaseModel):
    """更新会话：仅更新传入字段（部分更新）"""
    title: Optional[str] = Field(None, max_length=128, description="会话标题")
    model_apply_id: Optional[int] = Field(None, ge=0, description="授权记录 apply_id")
    model_name: Optional[str] = Field(None, max_length=128, description="模型标识")
    reasoning: Optional[bool] = Field(None, description="深度思考偏好")
    stream: Optional[bool] = Field(None, description="流式偏好")


class ChatMessageItem(BaseModel):
    """单条消息（对话已由网关转发至上游，本结构仅用于落库记录）"""
    role: str = Field(..., description="角色 USER/ASSISTANT")
    content: Optional[str] = Field(None, description="消息内容")
    reasoning_content: Optional[str] = Field(None, description="深度思考内容")
    model_name: Optional[str] = Field(None, max_length=128, description="模型标识")


class ChatMessageSaveReq(BaseModel):
    """批量保存会话消息"""
    messages: List[ChatMessageItem] = Field(default_factory=list, max_length=50)