from dataclasses import dataclass
from typing import Optional



@dataclass
class GraphIssue:
    code: str
    severity: str  # ERROR / WARNING / SUGGESTION
    message: str
    node_id: Optional[str] = None
    node_label: Optional[str] = None
    node_type: Optional[str] = None
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "nodeId": self.node_id,
            "nodeLabel": self.node_label,
            "nodeType": self.node_type,
            "suggestion": self.suggestion,
        }
