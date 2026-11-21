"""服务基类。

一个服务 = 一组关键词 + 命中后要做的事。服务由注册表统一管理，
共享同一个语音轮询器，所以新增功能不会多一份登录、多一份拉取。
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class Service(ABC):
    """所有语音服务的基类。"""

    #: 服务名，同时是配置里 ``services.<name>`` 的键
    name: str = "service"
    #: 越小越先尝试
    priority: int = 100
    #: 兜底服务（音乐）：前面没人认领时才会到它
    fallback: bool = False

    def __init__(self, config, log, app=None) -> None:
        self.config = config
        self.log = log
        self.app = app
        self._started = False

    async def start(self) -> None:
        """服务启动钩子（建立连接、校验配置）。失败不应阻塞主程序。"""
        self._started = True

    async def stop(self) -> None:
        """服务停止钩子。"""
        self._started = False

    @abstractmethod
    async def handle(
        self, did: str = "", query: str = "", ctrl_panel: bool = False, **kwargs
    ) -> bool:
        """认领并处理一句话；返回 False 表示交给下一个服务。"""
        raise NotImplementedError

    def describe(self) -> dict:
        return {
            "name": self.name,
            "priority": self.priority,
            "fallback": self.fallback,
            "started": self._started,
        }
