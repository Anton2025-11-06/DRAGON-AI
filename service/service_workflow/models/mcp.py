from typing import Optional
from pydantic import Field, BaseModel



class McpServerSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="服务名称")
    type: str = Field("SSE", description="连接类型 SSE/STDIO")
    url: Optional[str] = Field(None, max_length=500, description="SSE 连接地址")
    command: Optional[str] = Field(None, max_length=255, description="STDIO 启动命令")
    args: Optional[str] = Field(None, max_length=1000, description="STDIO 参数 JSON 数组")
    env: Optional[str] = Field(None, max_length=1000, description="环境变量 JSON 对象")
    description: Optional[str] = Field(None, max_length=500, description="MCP 描述（这个连接是做什么的）")
    status: bool = True


class McpServerTestRequest(BaseModel):
    """按参数测试连接（添加/编辑表单测试按钮使用，无需保存）"""
    name: Optional[str] = None
    type: str = "SSE"
    url: Optional[str] = None
    command: Optional[str] = None
    args: Optional[str] = None
    env: Optional[str] = None


class McpServerPageRequest(BaseModel):
    """分页查询请求体（前端 fast-crud POST 风格）"""
    current: int = 1
    size: int = 10
    name: Optional[str] = None
    status: Optional[bool] = None


class McpToolCallRequest(BaseModel):
    """调用 MCP 工具（工作流工具节点 / 页面调试）"""
    tool_name: str = Field(..., min_length=1, max_length=128, description="工具名称")
    arguments: dict = Field(default_factory=dict, description="调用参数 JSON 对象")