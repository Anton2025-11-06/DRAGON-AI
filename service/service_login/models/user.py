# User 实体已下沉到 common 共享层，此处保留原导入路径以兼容历史引用
from common.common_entity.user_entity import User

__all__ = ["User"]
