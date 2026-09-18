from typing import Optional
from pydantic import Field, BaseModel



class SkillRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="新的技能名称")


class SkillFileUpdateRequest(BaseModel):
    path: str = Field(..., max_length=500, description="技能内相对文件路径")
    content: str = Field("", description="文件内容")