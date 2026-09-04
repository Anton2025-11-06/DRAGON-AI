# -*- coding: utf-8 -*-
"""模型广场请求模型"""
from typing import List, Optional

from pydantic import BaseModel, Field


class ModelSuffixItem(BaseModel):
    """非直连模型的接口后缀：后缀 URI + 接口能力说明"""
    url: str = Field(..., min_length=1, max_length=200, description="接口后缀 URI，如 /v1/chat/completions")
    desc: Optional[str] = Field(None, max_length=200, description="接口能力说明")


class ModelSaveRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, description="模型名称")
    category: str = Field(..., description="分类（TEXT_GEN/EMBEDDING/RERANK/MULTIMODAL/IMAGE_GEN/AUDIO_GEN/VIDEO_GEN）")
    provider: str = Field(..., description="提供商（deepseek/qwen/doubao/hunyuan/kimi/openai）")
    model_name: str = Field(..., min_length=1, max_length=128, description="模型标识(API 调用时使用)")
    base_url: Optional[str] = Field(None, max_length=500, description="模型真实地址(直连=含接口完整路径，非直连=接口基础地址)")
    gateway_url: Optional[str] = Field(None, max_length=500, description="模型网关地址(展示给调用方)")
    # 是否直连：直连=base_url+接口后缀不可用；非直连=base_url+接口后缀转发
    is_direct: bool = Field(True, description="是否直连 1直连 0非直连")
    # 非直连时维护的后缀列表（每行：后缀 URI + 能力说明）
    suffixes: Optional[List[ModelSuffixItem]] = Field(None, description="接口后缀列表（非直连时维护）")
    api_key: Optional[str] = Field(None, max_length=500, description="管理端密钥")
    rate_limit_qps: int = Field(0, ge=0, description="每秒并发限制 0=不限")
    tutorial_md: Optional[str] = Field(None, description="使用教程 Markdown")
    status: bool = Field(True, description="启用状态")


class ModelTestRequest(BaseModel):
    """模型连通性测试请求：按分类调用对应探测端点"""
    category: str = Field(..., description="分类（TEXT_GEN/EMBEDDING/RERANK/...）")
    model_name: str = Field(..., min_length=1, max_length=128, description="模型标识")
    base_url: Optional[str] = Field(None, max_length=500, description="接口基础地址")
    api_key: Optional[str] = Field(None, max_length=500, description="管理端密钥")
    suffix_url: Optional[str] = Field(None, max_length=200, description="接口后缀 URI（非直连模型测试时拼接，如 /v1/chat/completions）")


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