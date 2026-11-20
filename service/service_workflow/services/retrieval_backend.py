# -*- coding: utf-8 -*-
"""知识检索节点的召回后端装载（Elasticsearch / Neo4j / rag 运行参数）。

为什么要有这一层
----------------
工作流跑「知识检索」节点有两条执行路径，两条都要求**执行所在的那个进程**里有 ES 单例，
否则服务层就抛「检索服务未就绪：ES 未初始化」：

1. 画布上点调试运行 —— ``WorkflowExecutionService._drive`` 走 websocket 分支，节点是在
   service_workflow 这个 API 进程里直接跑的（不投队列）；
2. 正式提交 / 定时 / API Key 触发 —— 投 arq 队列，节点在 workflow worker 进程里跑。

这两处原先各写一套（且 API 进程那套根本没写，ES 只在 worker 里装），配置段口径很容易飘，
所以收敛成同一个入口：服务在 ``create_app(on_ready=...)`` 里调，worker 在 bootstrap 里调，
两边读的都是 Nacos 的 ``service_workflow`` 段。

配置段回落口径
--------------
es/neo4j 优先取 ``service_workflow`` 自己这两段（工作流域的人改这里就够，不必去动
知识库域的配置），本段没有再回落 ``arq_ragflow``（这两个后端的原始出处）。两处都没有
才算没配。**注意 ``arq_workflow`` 段只被用来取 arq 的 redis 地址**（见
common_arq.queue.CustomRedisSettings），把 es/neo4j 写在那里是永远读不到的。

失败一律只降级、不往外抛
------------------------
ES 挂掉时工作流的其余节点（大模型/条件/代码/HTTP）照样能跑，所以既不能让 API 服务
起不来、也不能让 worker 起不来——这与 service_rag 的口径相反（那边检索是主链路，
连不上就该阻断启动，别起来一个什么都查不了的库服务）。真跑到检索节点时，服务层会
抛出带排查指引的错误信息，比启动期一句堆栈更好定位。
"""
from __future__ import annotations

from typing import Any, Optional

from common.common_constants.constant import ARQ_RAGFLOW, SERVICE_WORKFLOW
from common.common_log.log_init import log

SECTION_ES = "es"
SECTION_NEO4J = "neo4j"


async def _ragflow_section() -> dict[str, Any]:
    """取 arq_ragflow 段作为回落来源（data_id 没建、读失败都算「没有」，不往外抛）。"""
    try:
        from common.common_nacos.nacos_client import nacos_client

        return await nacos_client.get_config_content(ARQ_RAGFLOW) or {}
    except Exception as e:  # noqa: BLE001
        log.warning(f"{ARQ_RAGFLOW} 段读取失败，知识检索后端只能从 {SERVICE_WORKFLOW} 段取: {e}")
        return {}


def _section(cfg: dict, name: str) -> dict:
    """取一个配置段（非字典/空值一律当没有：yml 里写了个空段就是 None）。"""
    value = cfg.get(name)
    return value if isinstance(value, dict) and value else {}


async def bootstrap(primary_cfg: Optional[dict] = None) -> dict:
    """装载知识检索节点的召回后端（幂等，可在配置变更后重复调用刷新）。

    :param primary_cfg: 调用方已经读好的 ``service_workflow`` 整份 yml；留空则自行读一次
        （API 进程的 on_ready 会拿到 create_app 已读好的那份，不额外发请求）
    :return: 实际生效的两个段（给启动日志与自检接口用；没装上的段就是空字典）
    """
    primary = primary_cfg if isinstance(primary_cfg, dict) else await _pull_own_section()
    es_cfg = _section(primary, SECTION_ES)
    neo4j_cfg = _section(primary, SECTION_NEO4J)
    # rag 运行参数（mineru/es 两段）跟着 es 的真实来源走，同一份 yml 只认一个出处
    settings_cfg: dict = primary if es_cfg else {}

    if not es_cfg or not neo4j_cfg:
        fallback = await _ragflow_section()
        if not es_cfg:
            es_cfg = _section(fallback, SECTION_ES)
            settings_cfg = fallback if es_cfg else {}
        if not neo4j_cfg:
            neo4j_cfg = _section(fallback, SECTION_NEO4J)

    if es_cfg:
        await _init_es(es_cfg, settings_cfg)
    else:
        log.warning(f"未找到 es 配置段（Nacos {SERVICE_WORKFLOW}.{SECTION_ES} / "
                    f"{ARQ_RAGFLOW}.{SECTION_ES}），知识检索节点将报「检索服务未就绪」")

    if neo4j_cfg:
        await _init_neo4j(neo4j_cfg)
    else:
        log.info("未找到 neo4j 配置段，知识检索节点的图谱增强不可用（切片检索不受影响）")
    return {SECTION_ES: es_cfg, SECTION_NEO4J: neo4j_cfg}


async def _pull_own_section() -> dict:
    """没拿到现成配置时自己读一次本服务段（读不到只告警，等同没配）。"""
    try:
        from common.common_nacos.nacos_client import nacos_client

        return await nacos_client.get_config_content(SERVICE_WORKFLOW) or {}
    except Exception as e:  # noqa: BLE001
        log.warning(f"{SERVICE_WORKFLOW} 段读取失败，知识检索后端本轮不装载: {e}")
        return {}


async def _init_es(es_cfg: dict, settings_cfg: dict) -> None:
    """建 ES 单例并顺带装配 rag 运行参数（索引名、分词器等与知识库域同一份配置）。"""
    try:
        from common.common_es import init_es

        await init_es(es_cfg)
        log.info("知识检索后端就绪：Elasticsearch")
    except Exception as e:  # noqa: BLE001
        log.warning(f"ES 装载失败，知识检索节点将报「检索服务未就绪」: {e}")
        return
    try:
        from service.service_rag.services import rag_settings

        await rag_settings.bootstrap(settings_cfg)
    except Exception as e:  # noqa: BLE001
        # 运行参数没装上只是回到代码里的默认值（索引名/检索默认量级），不影响已经建好的连接
        log.warning(f"rag 运行参数装载失败，本轮全部使用代码默认值: {e}")


async def _init_neo4j(neo4j_cfg: dict) -> None:
    """建 Neo4j 单例：只服务「图谱增强」那一路，挂了不该把主链路（关键词+向量）算成不可用。"""
    try:
        from common.common_neo4j import init_neo4j

        await init_neo4j(neo4j_cfg)
        log.info("知识图谱后端就绪：Neo4j")
    except Exception as e:  # noqa: BLE001
        log.warning(f"neo4j unavailable, 知识检索节点的图谱增强不可用: {e}")


async def close() -> None:
    """释放两个后端的连接池（未装载的在各自 close 里就是空操作，退出期不报错）。"""
    try:
        from common.common_es import close_es

        await close_es()
    except Exception as e:  # noqa: BLE001
        log.warning(f"elasticsearch close failed: {e}")
    try:
        from common.common_neo4j import close_neo4j

        await close_neo4j()
    except Exception as e:  # noqa: BLE001
        log.warning(f"neo4j close failed: {e}")
