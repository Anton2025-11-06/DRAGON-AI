from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

# ==================== 资源实例授权（tb_resource_acl）请求体 ====================
# 动作码不在这里做枚举校验：合法集合按 resource_code 各不相同，唯一来源是
# common/common_permission/resource_guard.py 的 RESOURCE_SPECS，写库前由 AclService
# 统一 check_actions。Pydantic 里再抄一份枚举，等于给鉴权留下第二个会漂移的口径。


class AclGrantRow(BaseModel):
    """一条授权意图 = 一个主体 + 若干动作（落库时按动作拆行，与唯一键 uk_acl_row 对齐）"""
    grantee_type: int = Field(..., ge=1, le=5,
                              description="授权主体：1-用户 2-角色 3-部门 4-用户组 5-全员")
    grantee_id: int = Field(0, ge=0, description="主体ID；全员(5)时固定 0，其余类型必填")
    dept_include_sub: int = Field(0, ge=0, le=1,
                                  description="仅部门主体(3)有意义：0-仅本部门 1-含下级部门")
    actions: list[str] = Field(..., min_length=1, description="动作码列表（按资源类型校验）")
    expire_time: Optional[datetime] = Field(None, description="过期时间，null=永久有效")


class AclGrantsSaveRequest(BaseModel):
    """整表覆盖提交（授权弹窗一次把全部勾选结果回传，后端算差集：缺的行即被撤销）"""
    resource_code: str = Field(..., max_length=32,
                               description="资源类型编码 knowledge_base/document/workflow/...")
    resource_id: int = Field(..., gt=0, description="资源实例主键")
    grants: list[AclGrantRow] = Field(
        default_factory=list,
        description="目标授权全集；空数组=清掉这条资源上的全部显式授权")


class AclGrantsAppendRequest(AclGrantsSaveRequest):
    """增量追加（只加不减）：批量给多个资源授同一批人时，不必先拉全集再整表回传"""
    grants: list[AclGrantRow] = Field(..., min_length=1, description="要追加的授权行")
