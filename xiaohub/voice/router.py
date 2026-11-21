"""语音分发器。

轮询器只认这一层；它不关心消息最终由音乐还是投影仪处理。
服务按 ``priority`` 升序尝试，``fallback`` 服务永远排最后（音乐就是兜底）。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from xiaohub.services.registry import ServiceRegistry

logger = logging.getLogger("xiaohub")


class VoiceRouter:
    """把一句话路由给第一个认领它的服务。"""

    def __init__(self, registry: ServiceRegistry, log=None) -> None:
        self.registry = registry
        self.log = log or logger

    async def dispatch(
        self, did: str = "", query: str = "", ctrl_panel: bool = True, **kwargs
    ) -> bool:
        """返回 True 表示已有服务处理，False 表示无人认领。"""
        query = (query or "").strip()
        if not query:
            return False

        for service in self.registry.ordered():
            try:
                handled = await service.handle(
                    did=did, query=query, ctrl_panel=ctrl_panel, **kwargs
                )
            except Exception as err:  # 一个服务炸了不能拖垮其他服务
                self.log.exception("service %s handle failed: %s", service.name, err)
                continue
            if handled:
                self.log.debug("query routed to %s: %s", service.name, query)
                return True

        self.log.debug("no service claimed query: %s", query)
        return False
