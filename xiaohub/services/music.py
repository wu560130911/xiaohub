"""音乐服务：把现有的口令解析原样包进服务接口。

音乐不抢关键词：它排在最后，前面没有服务认领的句子照旧交给
``CommandHandler``，所以原有行为一字不改。
"""

from __future__ import annotations

from xiaohub.services.base import Service


class MusicService(Service):
    name = "music"
    priority = 1000
    fallback = True

    def __init__(self, command_handler, config, log, app=None) -> None:
        super().__init__(config, log, app)
        self.command_handler = command_handler

    async def handle(
        self, did: str = "", query: str = "", ctrl_panel: bool = False, **kwargs
    ) -> bool:
        await self.command_handler.do_check_cmd(
            did=did, query=query, ctrl_panel=ctrl_panel, **kwargs
        )
        # 兜底服务总是认领，保证没有"没人处理"的空白
        return True
