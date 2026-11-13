# -*- coding: utf-8 -*-
"""自定义 OpenSandbox workspace/manager：首建沙箱时一步引导命令都不跑。

为什么要有这两个类
------------------
agentscope 的 ``OpenSandboxWorkspace`` 在**首次创建沙箱**时会跑一串引导命令
（apt-get 装 curl/ca-certificates/ripgrep → curl astral.sh 装 uv → uv venv →
uv pip install 网关依赖 → uv pip install agentscope，共 5 条，单条上限
``BOOTSTRAP_COMMAND_TIMEOUT=600s``）。这串依赖沙箱出网与 apt/pypi 源，实测
分钟级且经常失败；任一条非 0 退出，``_setup_mcp_gateway`` 直接抛
RuntimeError，整轮对话起不来。本项目改用自建镜像（已预装网关 venv），
这 5 条本就不该再跑。

为什么 manager 必须跟着一起继承（不是多余的一层）
------------------------------------------------
父类 ``_build_and_start`` 里写死 ``ws = OpenSandboxWorkspace(...)``（模块级
import，没有 workspace_cls 之类的注入口）。只换 workspace 类而不换 manager，
子类永远不会被实例化。所以 manager 子类只覆掉 ``_build_and_start`` 把构造
目标换成 ``CustomOpenSandboxWorkspace``，其余行为与父类完全一致。

跳掉后镜像必须自带什么（少一样就改报「gateway did not become healthy」）
--------------------------------------------------------------------
网关目录 GATEWAY_HOME = 沙箱内 ``/root/.agentscope``：
1. ``{GATEWAY_HOME}/.venv`` 存在，且其 ``bin/python`` 能 ``import agentscope``
   （框架用这个 python 拉网关，装进系统 site-packages 不算数）；
2. 系统包 curl、ca-certificates、ripgrep（内置 Bash 等工具依赖，ripgrep 供 Grep）；
3. 网关基础依赖齐（= 父类引导里 ``_GATEWAY_BASE_REQUIREMENTS`` 那批包）。
**不**需要把网关脚本 ``_mcp_gateway_app.py`` 烘进镜像：跳过引导后框架仍会自己
write_file 上传它和 glob helper（那段上传不在 ``_bootstrap_commands`` 里）。

影响范围：引导只在沙箱里缺 ``_gateway_script`` 时才走，而该文件落在沙箱文件系统
里，同一 workspace_id 的 resume/reattach 本来就走 fast-path——所以这里只影响
**首次建沙箱**那一次，跟后续每轮对话无关。

本模块只被 arq worker import（经 workspace_factory，同 model_factory 的口径）。
"""
from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
from agentscope.workspace import OpenSandboxWorkspace
from agentscope.workspace._opensandbox._constants import (
    DEFAULT_IMAGE,
    METADATA_WORKSPACE_ID_KEY,
    SANDBOX_WORKDIR,
)

from common.common_log.log_init import log

if TYPE_CHECKING:
    from opensandbox import Sandbox


HOST_WORKSPACE_BASE = "/data/opensandbox"
USER_ID_METADATA_KEY = "agentscope.user.id"


class CustomOpenSandboxWorkspace(OpenSandboxWorkspace):
    """OpenSandboxWorkspace + 首建引导命令清空 + 按用户挂载持久化工作区卷。"""

    def _bootstrap_commands(self) -> list[str]:
        """覆盖父类：返回空列表 = 整段引导命令一步不跑。

        调用方 ``_sandboxed_base._setup_mcp_gateway`` 的闸门是
        ``if not backend.file_exists(self._gateway_script)``，闸门内先逐条执行
        本方法的返回值、再由框架自己上传 glob helper 与网关脚本。空列表是基类
        docstring 写明的 fast-path（Docker 后端镜像自带 venv 就是这么走的），
        所以不需要为「脚本没落盘」做任何补偿。
        """
        return []

    async def _create_sandbox(self) -> Sandbox:
        """覆盖父类：首建沙箱时把宿主机持久化目录 bind 到容器 /workspace。

        父类实现不传 volumes（见其 ``Sandbox.create``），沙箱 pause/到期销毁后
        容器文件系统随之回收，模型这轮产出的交付物（present_output 登记的最终
        输出文件等）下次进来就没了。这里按用户挂持久卷：宿主机
        ``/data/workspace/{user_id}`` ↔ 容器 ``/workspace``（读写），同一 user_id
        跨沙箱生命周期持久；user_id 取 manager 写进 sandbox_metadata 的
        ``agentscope.user.id``（PER_USER 隔离下必带）。仅首建走这里——reattach/
        resume 复用同一只已挂载的容器，无需再声明 volumes。

        取不到合法 user_id 直接 raise：持久化是硬要求，宁可让本轮沙箱起不来报错，
        也不能静默退回不挂载建出一只「产出物留不住」的沙箱。

        ⚠ 本方法逐字镜像父类 ``_create_sandbox``（只多传一个 ``volumes``）：
        agentscope 升级若改了父类的 create 入参，本方法**不会报错、只会静默少传**
        （多余的 kwarg 才炸）。升 agentscope 时记得 diff 一次父类 ``_create_sandbox``。
        """
        from opensandbox import Sandbox
        from opensandbox.models.sandboxes import Host, Volume

        user_id = str(self.sandbox_metadata.get(USER_ID_METADATA_KEY) or "").strip()
        if not user_id.isdigit():
            # 持久化是硬要求：缺合法 user_id 就 raise，绝不退回不挂载（那会建出一只
            # 产出物留不住的沙箱），更不能把基目录整体挂进容器（用户间数据泄露）。
            raise ValueError(
                f"harness 沙箱缺合法 user_id（{USER_ID_METADATA_KEY}），无法定位持久化目录 "
                f"{HOST_WORKSPACE_BASE}/{{user_id}}：workspace_id={self.workspace_id!r} "
                f"user_id={user_id!r}"
            )

        volume = Volume(
            name="harness-workspace",
            host=Host(path=f"{HOST_WORKSPACE_BASE}/{user_id}"),
            mount_path=SANDBOX_WORKDIR,
            read_only=False,
        )
        kwargs: dict = {
            "image": self.image,
            "connection_config": self._connection_config(),
            "metadata": {
                **self.sandbox_metadata,
                METADATA_WORKSPACE_ID_KEY: self.workspace_id,
            },
            "timeout": timedelta(seconds=self.timeout_seconds),
            "ready_timeout": timedelta(seconds=self.timeout_seconds),
            "volumes": [volume],
        }
        if self.env:
            kwargs["env"] = self.env
        if self.resource:
            kwargs["resource"] = self.resource
        if self.entrypoint:
            kwargs["entrypoint"] = self.entrypoint
        if self.network_policy is not None:
            kwargs["network_policy"] = self.network_policy
        return await Sandbox.create(**kwargs)


class CustomOpenSandboxWorkspaceManager(OpenSandboxWorkspaceManager):
    """与父类唯一差别：``_build_and_start`` 造的是 CustomOpenSandboxWorkspace。"""

    # 官方镜像 + 跳引导是配置错误，但每个 workspace 都报一条会刷屏，进程内提示一次
    _default_image_warned = False

    async def _build_and_start(
        self,
        *,
        workspace_id: str,
        user_id: str,
        agent_id: str,
    ) -> CustomOpenSandboxWorkspace:
        """父类同名方法原样搬过来，只把构造的类换成子类。

        ⚠ 参数表与父类逐字对齐：agentscope 升级后若在 ``OpenSandboxWorkspace``
        构造参数或父类实参上增删，本方法**不会报错、只会静默少传**（多余的
        kwarg 才炸）。升 agentscope 时记得 diff 一次父类 ``_build_and_start``。
        """
        self._warn_if_default_image()
        ws = CustomOpenSandboxWorkspace(
            workspace_id=workspace_id,
            image=self._image,
            api_key=self._api_key,
            domain=self._domain,
            protocol=self._protocol,
            request_timeout_seconds=self._request_timeout_seconds,
            timeout_seconds=self._timeout_seconds,
            gateway_port=self._gateway_port,
            env=self._env,
            sandbox_metadata={
                "agentscope.user.id": user_id,
                "agentscope.agent.id": agent_id,
                **self._sandbox_metadata,
            },
            resource=self._resource,
            entrypoint=self._entrypoint,
            network_policy=self._network_policy,
            extra_pip=self._extra_pip,
            default_mcps=self._default_mcps,
            skill_paths=self._skill_paths,
        )
        await ws.initialize()
        return ws

    def _warn_if_default_image(self) -> None:
        """跑官方镜像却跳了引导 = 必挂，早一条日志比卡在健康检查好定位。

        官方 ``DEFAULT_IMAGE``（python:3.11-slim）里没有网关 venv，跳掉引导后
        第 4 步起网关会一直不通，报的是 ``gateway did not become healthy``，
        看日志的人很容易以为是网络/沙箱平台问题。
        """
        if self._image != DEFAULT_IMAGE or self._default_image_warned:
            return
        CustomOpenSandboxWorkspaceManager._default_image_warned = True
        log.warning(
            "harness 沙箱用官方默认镜像 {}，但首建引导已被 CustomOpenSandboxWorkspace 跳过："
            "该镜像没有网关 venv，initialize 会卡在健康检查。请把 "
            "Nacos service_harness.opensandbox.image 填成预装好网关 venv 的自建镜像",
            DEFAULT_IMAGE,
        )
