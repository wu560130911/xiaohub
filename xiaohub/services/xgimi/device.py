"""投影仪设备层：BLE 唤醒 + UDP 关机 + TCP 状态探测。

不依赖 Home Assistant，也不依赖第三方云；只要宿主机的 BlueZ 能广播。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket

_LOGGER = logging.getLogger("xiaohub")

COMMAND_PORT = 16735
KEY_POWER_OFF = "KEYPRESSES:30"
DEFAULT_ALIVE_PORTS = (554, 16735, 16750, 80, 8080, 443)

# 极米 UDP 键码（与遥控器等价），可在 services.xgimi.actions 里按关键词映射
KEY_CODES = {
    "power": 116,
    "ok": 49,
    "play": 49,
    "pause": 49,
    "back": 48,
    "home": 35,
    "menu": 139,
    "up": 36,
    "down": 38,
    "left": 50,
    "right": 37,
    "volumeup": 115,
    "volumedown": 114,
    "volumemute": 113,
    "autofocus": 2099,
    "autofocus_new": 2103,
    "hibernate": 2106,
}


class XgimiDevice:
    def __init__(
        self,
        ip: str,
        token: str,
        adapter: str = "hci0",
        alive_port: int = 554,
        alive_ports: tuple[int, ...] | None = None,
        adv_duration: float = 4.0,
        incremental_token: bool = True,
        off_presses: int = 3,
        off_interval: float = 0.8,
        state_path: str | None = None,
    ) -> None:
        self.ip = ip
        self.alive_port = alive_port
        ports = DEFAULT_ALIVE_PORTS if alive_ports is None else alive_ports
        self.alive_ports = tuple(dict.fromkeys(int(p) for p in ports if int(p) > 0))
        self.adv_duration = adv_duration
        self.incremental_token = incremental_token
        self.off_presses = max(1, int(off_presses))
        self.off_interval = max(0.05, float(off_interval))
        self._state_path = state_path
        self.adapter_path = (
            adapter if adapter.startswith("/") else f"/org/bluez/{adapter}"
        )
        raw = str(token).replace(" ", "").replace(":", "").strip().upper()
        # 允许直接粘贴手机抓到的完整广播：4C000215 + body / 0215 + body
        if raw.startswith("4C000215") and len(raw) == 50:
            raw = raw[8:]
        elif raw.startswith("0215") and len(raw) == 46:
            raw = raw[4:]
        self._token = bytes.fromhex(raw)
        if len(self._token) == 16:
            self._kind = "android" if self._token.endswith(b"2CRKTM") else "remote"
        elif len(self._token) == 21:
            if not (
                self._token[6:9] == b"\xff\xff\xff"
                and self._token[9:15] == b"3CRKTM"
                and self._token[15:21] == b"\x00" * 6
            ):
                raise ValueError("21 字节 token 不是极米 iPhone iBeacon 格式")
            self._kind = "ibeacon"
        else:
            raise ValueError(
                "BLE token 必须是 16 字节（遥控器/安卓）或 21 字节（iPhone iBeacon），"
                f"当前 {len(self._token)} 字节"
            )
        self._counter = self._load_counter()
        self._lock = asyncio.Lock()

    async def _tcp_open(self, port: int) -> bool:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(self.ip, port), timeout=0.8
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            return False

    async def _ping(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                "ping", "-c", "1", "-W", "1", self.ip,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError):
            return False
        try:
            return await asyncio.wait_for(proc.wait(), timeout=2.0) == 0
        except asyncio.TimeoutError:
            proc.kill()
            return False

    async def probe_state(self) -> dict:
        """多路探测投影仪是否在线，返回判定依据，方便网页显示。"""
        checks = await asyncio.gather(
            *(self._tcp_open(port) for port in self.alive_ports)
        )
        open_ports = [port for port, ok in zip(self.alive_ports, checks) if ok]
        if open_ports:
            return {
                "on": True,
                "via": f"tcp:{open_ports[0]}",
                "open_ports": open_ports,
                "ping": None,
            }
        ping_ok = await self._ping()
        return {
            "on": ping_ok,
            "via": "ping" if ping_ok else "none",
            "open_ports": [],
            "ping": ping_ok,
        }

    async def is_on(self) -> bool:
        return bool((await self.probe_state())["on"])

    def _load_counter(self) -> int:
        """安卓 App 的 token 首字节是递增序号，必须跨重启保存。"""
        if self._kind != "android" or not self._state_path:
            return self._token[0]
        try:
            with open(self._state_path, encoding="utf-8") as file:
                state = json.load(file)
            if str(state.get("token", "")).upper() != self._token.hex().upper():
                return self._token[0]
            value = int(state.get("counter", self._token[0]))
            return value % 256
        except Exception:
            return self._token[0]

    def _save_counter(self) -> None:
        if self._kind != "android" or not self._state_path:
            return
        try:
            directory = os.path.dirname(self._state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            temporary = f"{self._state_path}.tmp"
            with open(temporary, "w", encoding="utf-8") as file:
                json.dump(
                    {"token": self._token.hex().upper(), "counter": self._counter},
                    file,
                )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._state_path)
        except Exception as err:
            _LOGGER.warning("xgimi: 保存 token 计数器失败：%s", err)

    def _next_payload(self) -> bytes:
        if self._kind == "android":
            # 手机抓到的包是已经用过的序号，必须先加一；否则投影仪认为重放
            self._counter = (self._counter + 1) % 256
            return bytes((self._counter,)) + self._token[1:]
        if self._kind != "remote" or not self.incremental_token:
            return self._token
        payload = bytes((self._counter,)) + self._token[1:]
        self._counter = (self._counter + 1) % 256
        return payload

    async def wake(self) -> None:
        # 延迟导入：没装 dbus-fast 时，音乐功能仍然可用
        from xiaohub.services.xgimi.ble import advertise

        async with self._lock:
            payload = self._next_payload()
            if self._kind == "ibeacon":
                await advertise(
                    self.adapter_path,
                    b"\x02\x15" + payload,
                    self.adv_duration,
                    manufacturer_id=0x004C,
                    local_name="",
                    service_uuid="",
                )
            elif self._kind == "android":
                await advertise(
                    self.adapter_path,
                    payload,
                    self.adv_duration,
                    manufacturer_id=0x0046,
                    local_name="",
                    service_uuid="",
                )
            else:
                await advertise(self.adapter_path, payload, self.adv_duration)
            self._save_counter()
        _LOGGER.info(
            "xgimi: BLE 唤醒广播已发出（%s, %s, 前缀 %02X），广播无回执，需看投影仪是否实际亮起",
            self.adapter_path,
            self._kind,
            payload[0],
        )

    async def power_off(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            for index in range(self.off_presses):
                sock.sendto(KEY_POWER_OFF.encode(), (self.ip, COMMAND_PORT))
                if index + 1 < self.off_presses:
                    await asyncio.sleep(self.off_interval)
        finally:
            sock.close()
        _LOGGER.info(
            "xgimi: UDP 关机已发送 %s 次 -> %s:%s（间隔 %.2fs）",
            self.off_presses,
            self.ip,
            COMMAND_PORT,
            self.off_interval,
        )

    async def send_key(self, name: str) -> None:
        """发一个普通按键（音量、对焦、静音……），走同一个 UDP 端口。"""
        code = KEY_CODES.get(name)
        if code is None:
            raise ValueError(f"unknown key: {name}; 可选：{', '.join(sorted(KEY_CODES))}")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(f"KEYPRESSES:{code}".encode(), (self.ip, COMMAND_PORT))
        finally:
            sock.close()
        _LOGGER.info("xgimi: 按键 %s(%s) 已发送", name, code)

    async def probe(self) -> str:
        from xiaohub.services.xgimi.ble import probe_adapter

        return await probe_adapter(self.adapter_path)
