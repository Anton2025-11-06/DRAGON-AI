from typing import Optional
from pydantic import Field, BaseModel


class ToolSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="工具名称")
    description: Optional[str] = Field(None, max_length=500, description="工具描述")
    function_code: str = Field(..., description="Python 函数源码")
    parameters_schema: Optional[str] = Field(
        None, description='参数定义 JSON：{"parameters":[{name,type,required,description,default}]}')
    status: bool = True
    timeout: Optional[int] = Field(None, ge=100, le=600000, description="执行超时（毫秒）")


class ToolTestRequest(BaseModel):
    parameters: Optional[dict] = Field(default_factory=dict, description="测试传入参数")


class ToolDefinitionTestRequest(BaseModel):
    """按定义测试：表单里未保存的代码 + 参数直接跑一次（新增/编辑弹窗的「测试」）"""
    function_code: str = Field(..., min_length=1, description="Python 函数源码")
    parameters: Optional[dict] = Field(default_factory=dict, description="测试参数")
    timeout: Optional[int] = Field(None, ge=100, le=600000, description="执行超时（毫秒）")