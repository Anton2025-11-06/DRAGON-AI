from datetime import datetime
from sqlalchemy import String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column
from common.common_entity.base_entity import Base


class User(Base):
    __tablename__ = "tb_user"

    user_id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(128), nullable=False)
    password: Mapped[str] = mapped_column(String(1000), nullable=False)
    email: Mapped[str] = mapped_column(String(128), nullable=True)
    phone: Mapped[str] = mapped_column(String(32), nullable=True)
    real_name: Mapped[str] = mapped_column(String(64), nullable=True)
    status: Mapped[int] = mapped_column(nullable=False, default=1)
    is_deleted: Mapped[int] = mapped_column(nullable=False, default=0)
    create_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())