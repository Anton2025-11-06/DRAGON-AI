# -*- coding: utf-8 -*-
"""模型广场请求模型"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CommonParam(BaseModel):
    """常用参数条目：底层以 JSON 存储的富列表，展示/编辑用；调用时按 type 转型 default 注入 model_params。"""
    name: str = Field(..., min_length=1, max_length=64, description="参数名")
    default: Optional[Any] = Field(None, description="默认值")
    desc: Optional[str] = Field(None, max_length=255, description="参数说明")
    type: str = Field("string", description="参数类型：boolean/integer/number/object/string")


class ModelSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="模型名称")
    category: str = Field(..., description="能力类型（13 类 code：text_to_text/text_embedding/text_rerank/image_embedding/multimodal_embedding/text_to_image/audio_to_text/image_understand/video_understand/ocr/image_to_video/text_to_video/text_to_audio）")
    provider: str = Field(..., description="供应商（openai/dashscope/zhipu）")
    model_name: str = Field(..., min_length=1, max_length=128, description="模型标识(API 调用时使用)")
    # 模型真实地址与密钥为必填（需求 1）
    base_url: str = Field(..., min_length=1, max_length=500, description="模型接口基础地址（各厂商 OpenAI 兼容/原生基础 URL，端点由 common_model 各类型子类自拼）")
    gateway_url: Optional[str] = Field(None, max_length=500, description="模型网关地址(展示给调用方)")
    api_key: str = Field(..., min_length=1, max_length=500, description="管理端密钥")
    rate_limit_qps: int = Field(0, ge=0, description="每秒并发限制 0=不限")
    # 高级设置（需求 2）
    supports_stream: bool = Field(False, description="是否支持流消息")
    supports_thinking: bool = Field(False, description="是否支持思考模式")
    stream_param: Optional[str] = Field(None, max_length=64, description="开启流式的参数键名")
    thinking_param: Optional[str] = Field(None, max_length=64, description="开启思考的参数键名")
    common_params: Optional[List[CommonParam]] = Field(None, description="常用参数列表")
    tutorial_md: Optional[str] = Field(None, description="使用教程 Markdown")
    status: bool = Field(True, description="启用状态")


class ModelTestRequest(BaseModel):
    """模型测试请求：走 common_model 按 (类型, 供应商) 真实调用，支持输入内容/流式/思考/常用参数。"""
    category: str = Field(..., description="能力类型（13 类 code：text_to_text/text_embedding/...）")
    provider: str = Field(..., description="供应商（openai/dashscope/zhipu）")
    model_name: str = Field(..., min_length=1, max_length=128, description="模型标识")
    base_url: Optional[str] = Field(None, max_length=500, description="接口基础地址（OpenAI 兼容基址）")
    api_key: Optional[str] = Field(None, max_length=500, description="管理端密钥")
    # 富测试（需求 2.5）：按类型的输入 + 流式/思考开关 + 常用参数值
    inputs: Optional[Dict[str, Any]] = Field(None, description="按能力类型的输入体（prompt/messages/input/image_url/...）")
    stream: bool = Field(False, description="是否流式")
    thinking: bool = Field(False, description="是否思考模式")
    params: Optional[Dict[str, Any]] = Field(None, description="常用参数键值（覆盖 model_params）")


class ModelPageRequest(BaseModel):
    current: int = Field(1, ge=1, description="页码")
    size: int = Field(10, ge=1, le=100, description="每页数量")
    name: Optional[str] = Field(None, max_length=128, description="模型名称模糊查询")
    category: Optional[str] = Field(None, description="分类筛选")
    provider: Optional[str] = Field(None, description="提供商筛选")
    status: Optional[bool] = Field(None, description="启用状态筛选")


class ModelApplyRequest(BaseModel):
    reason: Optional[str] = Field(None, max_length=500, description="申请理由")


class ApplyPageRequest(BaseModel):
    """申请审批列表分页请求（审批状态用 int 区分）"""
    current: int = Field(1, ge=1, description="页码")
    size: int = Field(10, ge=1, le=100, description="每页数量")
    status: Optional[int] = Field(None, ge=0, le=2, description="申请状态 0待审批 1已通过 2已拒绝")
    username: Optional[str] = Field(None, max_length=64, description="申请人模糊查询")
    model_id: Optional[int] = Field(None, ge=1, description="模型 id 筛选")


class ModelAuditRequest(BaseModel):
    approve: bool = Field(..., description="是否通过")
    reject_reason: Optional[str] = Field(None, max_length=500, description="拒绝原因")