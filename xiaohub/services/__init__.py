"""服务层入口：注册表 + 内置服务。

新增服务只需要：
1. 在 ``xiaomusic/services/<name>/`` 实现 ``Service`` 子类；
2. 在 ``_register_builtin`` 里按配置决定是否注册；
3. 配置里加 ``services.<name>``。

轮询、登录、去重、TTS 通道都是共用的，不需要再写一遍。
"""

from __future__ import annotations

from xiaohub.services.base import Service
from xiaohub.services.music import MusicService
from xiaohub.services.registry import ServiceRegistry

__all__ = ["Service", "ServiceRegistry", "MusicService", "build_registry"]


def build_registry(config, log, app, command_handler) -> ServiceRegistry:
    registry = ServiceRegistry(log)

    # 音乐是兜底：前面没人认领的句子都归它，保持原有行为
    registry.register(MusicService(command_handler, config, log, app))

    _register_builtin(registry, config, log, app)
    return registry


def _register_builtin(registry: ServiceRegistry, config, log, app) -> None:
    options = getattr(config, "services", None) or {}
    xgimi_options = options.get("xgimi")
    if xgimi_options:
        try:
            from xiaohub.services.xgimi.service import XgimiService

            registry.register(XgimiService(config, log, app, xgimi_options))
        except Exception as err:
            log.exception("xgimi service 注册失败：%s", err)
