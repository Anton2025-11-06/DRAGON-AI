from typing import Optional
from pydantic import Field, BaseModel




class SandboxChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="用户提问")
    skills: list = Field(default_factory=list, description="挂载的技能 id 列表")
    tools: list = Field(default_factory=list, description="挂载的工具 id 列表")
    kbs: list = Field(default_factory=list, description="挂载的知识库 id 列表")