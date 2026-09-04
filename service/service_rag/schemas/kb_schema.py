from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class KbCreateRequest(BaseModel):
    kb_name: str = Field(..., min_length=1, max_length=128, description="知识库名称")
    description: Optional[str] = Field(None, max_length=500, description="描述")


class KbUpdateRequest(BaseModel):
    kb_name: Optional[str] = Field(None, max_length=128, description="知识库名称")
    description: Optional[str] = Field(None, max_length=500, description="描述")
    is_public: Optional[int] = Field(None, ge=0, le=1, description="0私有 1公开")


class ShareRequest(BaseModel):
    share_type: int = Field(1, ge=1, le=2, description="1-授权用户 2-授权角色")
    target_id: int = Field(..., description="目标用户ID或角色ID")
    expire_time: Optional[datetime] = Field(None, description="过期时间，空为永久")


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500, description="检索问题")
    top_k: int = Field(10, ge=1, le=50, description="返回条数")
    kb_ids: Optional[list] = Field(None, description="指定检索的知识库ID列表，空则检索全部可见知识库")