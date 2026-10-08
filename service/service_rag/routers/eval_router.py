# -*- coding: utf-8 -*-
"""知识评测路由（RAGAS）：可选库列表 / 发起评测 / 运行历史 / 详情 / 删除 / 模板下载 / 问答对导入。

对外口径 ``/api/rag/eval/**``（网关剥前缀后落到 ``/eval/**``）。

鉴权口径
--------
整颗口子统一挂功能权限 ``ai:kb:eval``（进得了评测页的门槛）；「能不能看到这个库、能不能对它
跑评测」再按知识库的 **eval ACL** 收口——列表用 build_visible_cond 下推、发起前 ensure_action
单点卡（都在这条服务里做，见 RagEvalService）。eval 不在 scope 里，同部门不自动放开评测可见性。

执行模型
--------
``POST /runs`` 落库后立即投 ARQ ragflow 队列并返回 runId，**HTTP 不同步跑评测**；前端不轮询，
靠手动刷新 ``POST /page`` / ``GET /runs/{id}`` 看状态与结果（需求决策：后台任务 + 手动刷新）。

模板与导入
----------
``GET /template`` 现造「问答对模板.xlsx」（列：序号/问题/参考答案）直接回流；``POST /pairs/import``
用 openpyxl 读前两列（问题/参考答案）回显问答对，**只解析不落库**——前端填进动态表单还能再编辑。
"""
from __future__ import annotations

import io
import urllib.parse
from typing import List

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from openpyxl import load_workbook

from common.common_entity.response_schema import ApiResponse
from common.common_permission.permission import get_login_user, has_permission
from common.common_utils.excel_util import ExcelUtil
from service.service_rag.schemas.rag_schema import (
    EvalPairReq, EvalRunCreateReq, EvalRunPageReq,
)
from service.service_rag.services.rag_eval_service import RagEvalService

router = APIRouter(prefix="/eval", tags=["知识库评测"])

# 模板列：序号 / 问题 / 参考答案（导入按此顺序读前两列为问答对，序号列忽略）
TEMPLATE_HEADERS = ["序号", "问题", "参考答案"]
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _attachment_headers(filename: str) -> dict:
    """中文文件名下载头：filename 走 ASCII 兜底，filename* 用 RFC 5987 编码，两边浏览器都认。"""
    quoted = urllib.parse.quote(filename)
    return {"Content-Disposition": f"attachment; filename={quoted}; filename*=UTF-8''{quoted}"}


@router.get("/kbs", summary="可评测的知识库列表（按 eval ACL 过滤）")
@has_permission("ai:kb:eval")
async def kbs(request: Request):
    """归属人/ADMIN 天然可见自己的库；非本人库必须被显式授了 eval 才出现在这里。"""
    return ApiResponse.success(data=await RagEvalService.kbs(await get_login_user(request)))


@router.post("/runs", summary="发起评测（落库后投队列，立即返回 runId）")
@has_permission("ai:kb:eval")
async def create_run(request: Request, body: EvalRunCreateReq):
    """卡 eval ACL + 三个模型类型，落 run/items 后入队；不同步跑评测。"""
    data = await RagEvalService.create_run(await get_login_user(request), body)
    return ApiResponse.success(
        data=data, message="评测已提交，稍后手动刷新查看结果")


@router.post("/page", summary="评测运行历史分页（个人资产：归属人 + ADMIN 可见）")
@has_permission("ai:kb:eval")
async def page_runs(request: Request, body: EvalRunPageReq):
    return ApiResponse.success(data=await RagEvalService.page_runs(
        await get_login_user(request), body))


@router.get("/template", summary="下载问答对模板（xlsx：序号/问题/参考答案）")
@has_permission("ai:kb:eval")
async def template():
    content = ExcelUtil.get_excel_template(TEMPLATE_HEADERS, [], [])
    return StreamingResponse(
        io.BytesIO(content), media_type=_XLSX_MIME,
        headers=_attachment_headers("问答对模板.xlsx"))


@router.post("/pairs/import", summary="导入问答对 Excel（仅解析回显，不落库）")
@has_permission("ai:kb:eval")
async def import_pairs(request: Request, file: UploadFile = File(...)):
    """读问题列与参考答案列组成问答对回显，前端填进动态表单后仍可编辑再提交。

    列定位与模板解耦：表头有「问题/参考答案」按名字取列（模板第一列是序号也能识别）；
    没表头则按位置读前两列——手填的两列文件与下载模板另存都吃得下。
    """
    name = (file.filename or "").lower()
    if not name.endswith((".xlsx", ".xlsm")):
        raise ValueError("仅支持 .xlsx / .xlsm 文件，请用模板另存或重新导出")
    raw = await file.read()
    if not raw:
        raise ValueError("上传文件为空")
    wb = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        ws = wb.active
        if not ws:
            raise ValueError("工作簿没有活动工作表")
        rows = [list(r) for r in ws.iter_rows(values_only=True)]
        q_idx, r_idx, start = 0, 1, 0
        if rows:
            header = [str(c).strip().lower() if c is not None else "" for c in rows[0]]
            hit_q = next((i for i, c in enumerate(header)
                          if c in ("问题", "question")), None)
            if hit_q is not None:
                q_idx = hit_q
                r_idx = next((i for i, c in enumerate(header)
                              if c in ("参考答案", "答案", "reference", "ground_truth",
                                      "ground truth")), q_idx + 1)
                start = 1
        pairs: List[EvalPairReq] = []
        for row in rows[start:]:
            question = row[q_idx] if q_idx < len(row) else None
            reference = row[r_idx] if r_idx < len(row) else None
            text = str(question).strip() if question is not None else ""
            if not text:
                continue
            ref_text = str(reference).strip() if reference is not None else ""
            pairs.append(EvalPairReq(question=text, reference=(ref_text or None)))
        if not pairs:
            raise ValueError("未解析到有效问答对（问题列不能为空）")
    finally:
        wb.close()
    return ApiResponse.success(
        data={"pairs": [{"question": p.question, "reference": p.reference} for p in pairs],
              "total": len(pairs)},
        message=f"已解析 {len(pairs)} 个问答对")


@router.get("/runs/{run_id}", summary="评测详情（run + 逐问答对结果 + 指标均值）")
@has_permission("ai:kb:eval")
async def run_detail(request: Request, run_id: int):
    return ApiResponse.success(data=await RagEvalService.run_detail(
        await get_login_user(request), run_id))


@router.delete("/runs/{run_id}", summary="删除评测记录（软删）")
@has_permission("ai:kb:eval")
async def delete_run(request: Request, run_id: int):
    await RagEvalService.delete_run(await get_login_user(request), run_id)
    return ApiResponse.success(message="删除成功")
