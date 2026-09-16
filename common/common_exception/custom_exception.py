from common.common_entity.graph_error_entity import GraphIssue


class UnauthorizedException(Exception):
    def __init__(self, message: str):
        # 调用父类构造，保存异常信息到 args
        super().__init__(message)
        self.message = message


class WorkflowGraphError(Exception):
    """图结构非法（保存/发布时校验用）"""

    def __init__(self, issues: list["GraphIssue"]):
        self.issues = issues
        super().__init__("; ".join(f"[{i.severity}] {i.code}: {i.message}" for i in issues))