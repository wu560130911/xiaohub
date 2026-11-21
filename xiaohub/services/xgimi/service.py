"""投影仪服务：配置关键词 → 开关机动作。

配置示例（setting.json）::

    "services": {
      "xgimi": {
        "enabled": true,
        "ip": "192.168.31.50",
        "token": "12D7C7899B9F80FFFFFF3043524B544D",
        "adapter": "hci0",
        "on": ["打开投影", "开投影"],
        "off": ["关闭投影", "关投影"]
      }
    }
"""

from __future__ import annotations

import asyncio
import os
import time

from xiaohub.services.base import Service
from xiaohub.services.xgimi.device import XgimiDevice
from xiaohub.services.xgimi.matcher import (
    DEFAULT_OFF_KEYS,
    DEFAULT_ON_KEYS,
    detect_intent,
    normalize,
)

DEFAULT_TTS = {
    "on": "开机中",
    "off": "关了",
    "ready": "投影好了",
    "cooling": "刚关掉，等一会儿再开",
    "already_on": "已经开着呢",
    "failed": "投影没响应",
}


class XgimiService(Service):
    name = "xgimi"
    priority = 10

    def __init__(self, config, log, app=None, options: dict | None = None) -> None:
        super().__init__(config, log, app)
        self.options = options or {}
        self.enabled = bool(self.options.get("enabled", True))
        self.on_keys: list[str] = list(self.options.get("on") or DEFAULT_ON_KEYS)
        self.off_keys: list[str] = list(self.options.get("off") or DEFAULT_OFF_KEYS)
        self.feedback = bool(self.options.get("feedback", True))
        self.announce_ready = bool(self.options.get("announce_ready", True))
        self.off_cooldown = float(self.options.get("off_cooldown", 30))
        self.on_guard = float(self.options.get("on_guard", 25))
        self.adv_rounds = max(1, int(self.options.get("adv_rounds", 2)))
        self.tts = {**DEFAULT_TTS, **(self.options.get("tts") or {})}
        # 关键词 → UDP 键名，例如 {"投影静音": "volumemute"}
        self.actions: dict[str, str] = dict(self.options.get("actions") or {})
        self.device: XgimiDevice | None = None
        self._last_on_sent = 0.0
        self._last_off = 0.0
        self._tasks: set[asyncio.Task] = set()

    async def start(self) -> None:
        if not self.enabled:
            self.log.info("xgimi service disabled")
            return
        try:
            raw_ports = self.options.get("alive_ports")
            if isinstance(raw_ports, str):
                alive_ports = tuple(
                    int(part)
                    for part in raw_ports.replace("，", ",").split(",")
                    if part.strip().isdigit()
                )
            elif isinstance(raw_ports, (list, tuple)):
                alive_ports = tuple(int(part) for part in raw_ports)
            else:
                alive_ports = None
            conf_path = getattr(self.config, "conf_path", None)
            state_path = (
                os.path.join(conf_path, "xgimi_token_state.json")
                if conf_path
                else None
            )
            self.device = XgimiDevice(
                ip=self.options["ip"],
                token=self.options["token"],
                adapter=str(self.options.get("adapter", "hci0")),
                alive_port=int(self.options.get("alive_port", 554)),
                alive_ports=alive_ports,
                adv_duration=float(self.options.get("adv_duration", 4.0)),
                incremental_token=bool(self.options.get("incremental_token", True)),
                off_presses=int(self.options.get("off_presses", 3)),
                off_interval=float(self.options.get("off_interval", 0.8)),
                state_path=state_path,
            )
        except Exception as err:
            self.log.error("xgimi service init failed: %s", err)
            self.device = None
            return
        try:
            await self.device.probe()
            self.log.info("xgimi ready: %s", self.options["ip"])
        except Exception as err:
            # BLE 不可用时仍保留服务：关机走 UDP，不依赖蓝牙
            self.log.warning("xgimi BLE unavailable (%s); 开机将失败，关机仍可用", err)
        self._started = True

    async def stop(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def handle(
        self, did: str = "", query: str = "", ctrl_panel: bool = False, **kwargs
    ) -> bool:
        if self.device is None:
            return False
        intent = detect_intent(query, self.on_keys, self.off_keys)
        if intent is None:
            key_name = self._match_action(query)
            if key_name is None:
                return False
            self.log.info("xgimi 命中按键：%s（%s）", key_name, query)
            try:
                await self._interrupt(did)
                await self.device.send_key(key_name)
            except Exception as err:
                self.log.exception("xgimi key failed: %s", err)
                await self._say(did, self.tts["failed"])
            return True

        self.log.info("xgimi 命中口令：%s（%s）", intent, query)
        try:
            await self._interrupt(did)
            if intent == "on":
                await self._power_on(did)
            else:
                await self._power_off(did)
        except Exception as err:
            self.log.exception("xgimi action failed: %s", err)
            await self._say(did, self.tts["failed"])
        # 认领：即使失败也不要再让音乐服务去猜这句话
        return True

    # ---------- 动作 ----------

    async def _power_on(self, did: str) -> None:
        device = self.device
        assert device is not None
        now = time.time()
        if await device.is_on():
            await self._say(did, self.tts["already_on"])
            return
        if now - self._last_off < self.off_cooldown:
            await self._say(did, self.tts["cooling"])
            return
        if now - self._last_on_sent < self.on_guard:
            await self._say(did, self.tts["on"])
            return

        self._last_on_sent = now
        await self._say(did, self.tts["on"])
        # 广播要 8 秒，放后台跑，别把语音轮询堵住
        self._spawn(self._wake_and_watch(did))

    async def _wake_and_watch(self, did: str) -> None:
        device = self.device
        if device is None:
            return
        sent = 0
        for i in range(self.adv_rounds):
            try:
                await device.wake()
                sent += 1
            except Exception as err:
                self.log.error("xgimi 第 %s 轮广播失败：%s", i + 1, err)
            await asyncio.sleep(0.5)
        if sent == 0:
            await self._say(did, self.tts["failed"])
            return
        await self._watch_ready(did)

    async def _power_off(self, did: str) -> None:
        device = self.device
        assert device is not None
        await device.power_off()
        self._last_off = time.time()
        await self._say(did, self.tts["off"])

    async def _watch_ready(self, did: str, timeout: float = 90.0) -> None:
        device = self.device
        if device is None:
            return
        deadline = time.time() + timeout
        while time.time() < deadline:
            await asyncio.sleep(3)
            try:
                if await device.is_on():
                    if self.announce_ready:
                        await self._say(did, self.tts["ready"])
                    return
            except Exception:
                pass
        self.log.warning("xgimi 开机超时，投影仪仍未上线")

    # ---------- 辅助 ----------

    def _match_action(self, query: str) -> str | None:
        if not self.actions:
            return None
        normalized = normalize(query)
        for keyword, key_name in self.actions.items():
            if normalize(keyword) and normalize(keyword) in normalized:
                return key_name
        return None

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _interrupt(self, did: str) -> None:
        """尽力打断小爱正在说的话，失败不影响动作。"""
        try:
            devices = getattr(getattr(self.app, "device_manager", None), "devices", {})
            device = devices.get(did)
            if device is not None and hasattr(device, "group_force_stop_xiaoai"):
                await device.group_force_stop_xiaoai()
        except Exception as err:
            self.log.debug("xgimi interrupt failed: %s", err)

    async def _say(self, did: str, text: str) -> None:
        if not self.feedback or not text or self.app is None:
            return
        try:
            await self.app.do_tts(did, text)
        except Exception as err:
            self.log.debug("xgimi tts failed: %s", err)

    def describe(self) -> dict:
        data = super().describe()
        data.update(
            {
                "ip": self.options.get("ip"),
                "on_keys": self.on_keys,
                "off_keys": self.off_keys,
            }
        )
        return data
