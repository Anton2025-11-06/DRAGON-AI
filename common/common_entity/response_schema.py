from typing import Any

from pydantic import BaseModel


class ApiResponse(BaseModel):
    code: int = 200
    message: str = "success"
    data: Any = None

    @staticmethod
    def success(data=None, message: str = "success"):
        return ApiResponse(code=200, message=message, data=data).dict()

    @staticmethod
    def error(code: int = -1, message: str = "error"):
        return ApiResponse(code=code, message=message, data=None).dict()
