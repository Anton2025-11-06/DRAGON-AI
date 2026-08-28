from typing import Optional, Any
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import text
from common.common_log.log_init import log


class AsyncMysqlClient:
    _instance: Optional["AsyncMysqlClient"] = None
    # 异步引擎
    _engine = None
    # 会话工厂
    _session_factory = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


    async def init(
        self,
        host: str,
        port: int,
        user: str,
        password: str,
        database: str,
        pool_size: int = 20,
        max_overflow: int = 30,
        echo: bool = False
    ):
        """
        初始化异步MySQL连接池
        :param host: 数据库地址
        :param port: 端口
        :param user: 账号
        :param password: 密码
        :param database: 库名
        :param pool_size: 连接池常驻连接数
        :param max_overflow: 峰值临时扩容连接
        :param echo: 是否打印SQL日志（开发开启，生产关闭）
        """
        if self._engine:
            log.info("Mysql async engine already initialized, skip re-init")
            return

        # 异步mysql连接串（mysql+aiomysql）
        db_url = f"mysql+aiomysql://{user}:{password}@{host}:{port}/{database}"
        try:
            self._engine = create_async_engine(
                url=db_url,
                pool_size=pool_size,
                max_overflow=max_overflow,
                echo=echo,
                pool_recycle=300,  # 5分钟回收闲置连接，避免断开
                pool_pre_ping=True  # 取出连接前自动ping校验存活
            )
            # 创建会话工厂
            self._session_factory = async_sessionmaker(
                bind=self._engine,
                expire_on_commit=False,
                class_=AsyncSession
            )
            # 测试连通性
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
            log.info(f"Async Mysql connect success: {host}:{port}/{database}")
        except Exception as e:
            log.error(f"Mysql connect failed: {str(e)}")
            raise e

    async def close(self):
        """服务关闭销毁连接池"""
        if self._engine:
            await self._engine.dispose()
            log.info("Async Mysql engine connection pool closed")

    def get_session(self) -> AsyncSession:
        """获取数据库会话（上下文管理器使用）"""
        if not self._session_factory:
            raise RuntimeError("Mysql client not initialized, call init() first")
        return self._session_factory()

    @property
    def engine(self):
        """暴露原生异步引擎，复杂底层操作使用"""
        if not self._engine:
            raise RuntimeError("Mysql client not initialized, call init() first")
        return self._engine

mysql_client = AsyncMysqlClient()