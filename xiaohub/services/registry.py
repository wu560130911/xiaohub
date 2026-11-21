"""服务注册表：全项目唯一的服务清单与排序规则。"""

from __future__ import annotations

from xiaohub.services.base import Service


class ServiceRegistry:
    def __init__(self, log) -> None:
        self.log = log
        self._services: list[Service] = []

    def register(self, service: Service) -> None:
        if any(item.name == service.name for item in self._services):
            raise ValueError(f"duplicate service name: {service.name}")
        self._services.append(service)
        self._services.sort(key=lambda s: (s.fallback, s.priority, s.name))
        self.log.info(
            "service registered: %s (priority=%s fallback=%s)",
            service.name,
            service.priority,
            service.fallback,
        )

    def get(self, name: str) -> Service | None:
        return next((s for s in self._services if s.name == name), None)

    def ordered(self) -> list[Service]:
        """按尝试顺序返回：非兜底在前，兜底永远最后。"""
        return list(self._services)

    def names(self) -> list[str]:
        return [s.name for s in self._services]

    async def start_all(self) -> None:
        for service in self._services:
            try:
                await service.start()
            except Exception as err:
                self.log.exception("service %s start failed: %s", service.name, err)

    async def stop_all(self) -> None:
        for service in reversed(self._services):
            try:
                await service.stop()
            except Exception as err:
                self.log.exception("service %s stop failed: %s", service.name, err)
